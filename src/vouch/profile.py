"""Compliance profiles: one YAML file per company, validated on load.

A profile stacks the layers that decide what one company must show: the base framework,
its protection level, country overlays adding requirements, requirements it marks not
applicable, the evidence sources the agent may use, and (unused for now) an asset register
and risks.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from vouch.isa import (
    SOURCE,
    BaseFrameworkError,
    Column,
    IsaControl,
    Requirement,
    UnknownControlError,
    load_isa_control,
)
from vouch.paths import FIXTURES_DIR

PROFILE_FILE = "profile.yaml"
DEFAULT_PROFILE = "nordlicht"  # the one profile until a user can choose theirs
NORDLICHT_PROFILE = FIXTURES_DIR / "nordlicht" / PROFILE_FILE

ProtectionLevel = Literal["normal", "high", "very high"]
EvidenceSourceName = Literal["policies", "records", "patch"]
NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# The base framework columns each protection level brings in; very high includes high.
PROTECTION_COLUMNS: dict[ProtectionLevel, frozenset[Column]] = {
    "normal": frozenset({"must", "should"}),
    "high": frozenset({"must", "should", "high"}),
    "very high": frozenset({"must", "should", "high", "very high"}),
}


@dataclass(frozen=True)
class NotApplicableRequirement:
    requirement: Requirement
    reason: str


@dataclass(frozen=True)
class ControlRequirements:
    """One control's requirements under a profile, in the base framework's order."""

    applicable: tuple[Requirement, ...]
    not_applicable: tuple[NotApplicableRequirement, ...]


class ProfileError(ValueError):
    """A compliance profile is missing or invalid; the message names the file and the field."""

    def __init__(self, path: Path, problem: str):
        super().__init__(f"invalid compliance profile {path}: {problem}")
        self.path = path


def _profile_dir(info: ValidationInfo) -> Path:
    """The profile file's folder, which load_profile passes as context; never the cwd."""
    base = info.context.get("base_dir") if isinstance(info.context, dict) else None
    if not isinstance(base, Path):
        raise TypeError("validate a profile with context={'base_dir': <its folder>}")
    return base


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceSource(_Strict):
    folder: Path  # relative to the profile file in YAML; resolved to an existing folder on load

    @field_validator("folder", mode="after")
    @classmethod
    def _folder_inside_profile_dir(cls, folder: Path, info: ValidationInfo) -> Path:
        base = _profile_dir(info).resolve()
        resolved = (base / folder).resolve()
        if folder.is_absolute() or not resolved.is_relative_to(base):
            raise ValueError(f"must stay inside the profile's folder {base}, found {folder}")
        if not resolved.is_dir():
            raise ValueError(f"folder {resolved} does not exist")
        return resolved


def _known_control(control_id: str) -> str:
    try:
        load_isa_control(control_id)
    except UnknownControlError as error:
        raise ValueError(f"unknown control {control_id} in {SOURCE}") from error
    return control_id


ControlId = Annotated[str, AfterValidator(_known_control)]


class CountryOverlayRequirement(_Strict):
    control: ControlId  # the base framework control it adds to
    id: str = Field(pattern=r"^O[1-9][0-9]*$")
    text: NonEmpty


class CountryOverlay(_Strict):
    """A country overlay: national rules adding requirements to base framework controls."""

    name: NonEmpty  # becomes each of its requirements' source
    requirements: tuple[CountryOverlayRequirement, ...]


class NotApplicable(_Strict):
    control: ControlId
    requirement: str | None = None  # None: the whole control is not applicable
    reason: NonEmpty


class ComplianceProfile(_Strict):
    id: NonEmpty  # the profile's folder name, such as nordlicht
    company: NonEmpty
    country: NonEmpty
    answer_language: str = Field(pattern=r"^[a-z]{2}$")  # ISO 639-1 code: en, de
    protection_level: ProtectionLevel
    base_framework: Literal["VDA ISA 2027.1"]  # vouch.isa.SOURCE; a test keeps them equal
    overlays: tuple[CountryOverlay, ...]  # country overlays
    not_applicable: tuple[NotApplicable, ...]
    evidence_sources: dict[EvidenceSourceName, EvidenceSource]
    asset_register: tuple[dict[str, Any], ...] = ()
    risks: tuple[dict[str, Any], ...] = ()

    @field_validator("id", mode="after")
    @classmethod
    def _id_is_folder_name(cls, profile_id: str, info: ValidationInfo) -> str:
        folder = _profile_dir(info).name
        if profile_id != folder:
            raise ValueError(f"must be the profile's folder name, {folder}")
        return profile_id

    @model_validator(mode="after")
    def _overlay_ids_unique_per_control(self) -> Self:
        seen: set[tuple[str, str]] = set()
        for i, overlay in enumerate(self.overlays):
            for j, added in enumerate(overlay.requirements):
                if (added.control, added.id) in seen:
                    raise ValueError(
                        f"overlays.{i}.requirements.{j}.id: "
                        f"control {added.control} already has {added.id}"
                    )
                seen.add((added.control, added.id))
        return self

    @model_validator(mode="after")
    def _not_applicable_entries_are_sound(self) -> Self:
        for index, entry in enumerate(self.not_applicable):
            for earlier, other in enumerate(self.not_applicable[:index]):
                if other.control == entry.control and (
                    None in (entry.requirement, other.requirement)
                    or entry.requirement == other.requirement
                ):
                    raise ValueError(
                        f"not_applicable.{index}: overlaps not_applicable.{earlier} "
                        f"(control {entry.control})"
                    )
            if entry.requirement is None:
                continue
            control = load_isa_control(entry.control)
            every = {r.id for r in control.requirements + self._overlay_requirements(control.id)}
            if entry.requirement not in every:
                raise ValueError(
                    f"not_applicable.{index}.requirement: control {entry.control} "
                    f"has no requirement {entry.requirement}"
                )
            if entry.requirement not in {r.id for r in self._brought_in(control)}:
                raise ValueError(
                    f"not_applicable.{index}.requirement: {entry.control} {entry.requirement} "
                    f"doesn't apply at {self.protection_level} protection"
                )
        return self

    def requirements_for(self, control: IsaControl) -> ControlRequirements:
        """The control's requirements under this profile: those its protection level and
        country overlays bring in, split into applicable and not applicable (with the reason)."""
        applicable, excluded = [], []
        for requirement in self._brought_in(control):
            reason = self._not_applicable_reason(control.id, requirement.id)
            if reason is None:
                applicable.append(requirement)
            else:
                excluded.append(NotApplicableRequirement(requirement, reason))
        return ControlRequirements(tuple(applicable), tuple(excluded))

    def _brought_in(self, control: IsaControl) -> tuple[Requirement, ...]:
        """The base requirements in the protection level's columns, then the overlays' ones."""
        columns = PROTECTION_COLUMNS[self.protection_level]
        base = tuple(r for r in control.requirements if r.column in columns)
        return base + self._overlay_requirements(control.id)

    def _overlay_requirements(self, control_id: str) -> tuple[Requirement, ...]:
        """The requirements the country overlays add to this control, each with its source."""
        return tuple(
            Requirement(added.id, "overlay", added.text, overlay.name)
            for overlay in self.overlays
            for added in overlay.requirements
            if added.control == control_id
        )

    def _not_applicable_reason(self, control_id: str, requirement_id: str) -> str | None:
        for entry in self.not_applicable:
            if entry.control == control_id and entry.requirement in (None, requirement_id):
                return entry.reason
        return None


def _describe(error: ValidationError) -> str:
    """Each problem as `field.path: message`, so the error names the field."""
    problems = []
    for item in error.errors():
        field = ".".join(str(part) for part in item["loc"])
        message = item["msg"].removeprefix("Value error, ")
        problems.append(f"{field}: {message}" if field else message)
    return "; ".join(problems)


def load_profile(path: Path) -> ComplianceProfile:
    """The validated profile in this YAML file, or ProfileError naming the file and the field."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ProfileError(path, error.strerror or str(error)) from error
    except yaml.YAMLError as error:
        raise ProfileError(path, f"not valid YAML ({error.__class__.__name__})") from error
    try:
        return ComplianceProfile.model_validate(data, context={"base_dir": path.parent})
    except ValidationError as error:
        raise ProfileError(path, _describe(error)) from error
    except BaseFrameworkError as error:  # the controls it names couldn't be looked up
        raise ProfileError(path, str(error)) from error


class UnknownProfileError(LookupError):
    """No profile folder with this name under the fixtures."""

    def __init__(self, name: str):
        super().__init__(f"unknown profile: {name}")
        self.name = name


def load_named_profile(name: str) -> ComplianceProfile:
    """The profile in `fixtures/<name>/profile.yaml`; a name is one lowercase word or slug."""
    path = FIXTURES_DIR / name / PROFILE_FILE
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) or not path.is_file():
        raise UnknownProfileError(name)
    return load_profile(path)
