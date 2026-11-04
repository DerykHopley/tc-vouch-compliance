"""Compliance profiles: one YAML file per company, validated on load.

A profile stacks the layers that decide what one company must show: the base framework,
its protection level, overlays adding requirements, requirements it marks not applicable,
the evidence sources the agent may use, and (unused for now) an asset register and risks.
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

from vouch.isa import Column, IsaControl, Requirement, load_base_framework, load_isa_control

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
PROFILE_FILE = "profile.yaml"
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


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceSource(_Strict):
    folder: Path  # relative to the profile file in YAML; resolved to an existing folder on load

    @field_validator("folder", mode="after")
    @classmethod
    def _folder_exists(cls, folder: Path, info: ValidationInfo) -> Path:
        base = info.context.get("base_dir") if isinstance(info.context, dict) else None
        resolved = (base / folder) if base is not None else folder
        if not resolved.is_dir():
            raise ValueError(f"folder {resolved} does not exist")
        return resolved


def _known_control(control_id: str) -> str:
    framework = load_base_framework()
    if control_id not in {control.id for control in framework.controls}:
        raise ValueError(f"unknown control {control_id} in {framework.source}")
    return control_id


ControlId = Annotated[str, AfterValidator(_known_control)]


class OverlayRequirement(_Strict):
    control: ControlId  # the base framework control it adds to
    id: str = Field(pattern=r"^O[1-9][0-9]*$")
    text: NonEmpty


class Overlay(_Strict):
    name: NonEmpty  # becomes each of its requirements' source
    requirements: tuple[OverlayRequirement, ...]


class NotApplicable(_Strict):
    control: ControlId
    requirement: str | None = None  # None: the whole control is not applicable
    reason: NonEmpty


class ComplianceProfile(_Strict):
    id: str
    company: str
    country: str
    answer_language: str
    protection_level: ProtectionLevel
    base_framework: Literal["VDA ISA 2027.1"]  # vouch.isa.SOURCE, the one base framework
    overlays: tuple[Overlay, ...]
    not_applicable: tuple[NotApplicable, ...]
    evidence_sources: dict[EvidenceSourceName, EvidenceSource]
    asset_register: tuple[dict[str, Any], ...] = ()
    risks: tuple[dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def _not_applicable_requirements_exist(self) -> Self:
        for index, entry in enumerate(self.not_applicable):
            if entry.requirement is None:
                continue
            known = {r.id for r in load_isa_control(entry.control).requirements}
            known |= {
                added.id
                for overlay in self.overlays
                for added in overlay.requirements
                if added.control == entry.control
            }
            if entry.requirement not in known:
                raise ValueError(
                    f"not_applicable.{index}.requirement: control {entry.control} "
                    f"has no requirement {entry.requirement}"
                )
        return self

    def requirements_for(self, control: IsaControl) -> ControlRequirements:
        """The control's requirements under this profile: those its protection level and
        overlays bring in, split into applicable and not applicable (with the reason)."""
        columns = PROTECTION_COLUMNS[self.protection_level]
        brought_in = [r for r in control.requirements if r.column in columns]
        brought_in += [
            Requirement(added.id, "overlay", added.text, overlay.name)
            for overlay in self.overlays
            for added in overlay.requirements
            if added.control == control.id
        ]
        applicable, excluded = [], []
        for requirement in brought_in:
            reason = self._not_applicable_reason(control.id, requirement.id)
            if reason is None:
                applicable.append(requirement)
            else:
                excluded.append(NotApplicableRequirement(requirement, reason))
        return ControlRequirements(tuple(applicable), tuple(excluded))

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
