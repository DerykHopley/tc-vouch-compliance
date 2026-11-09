"""Compliance profiles: one YAML file per company, validated on load.

A profile stacks the layers that decide what one company must show: the base framework,
its protection level, country overlays adding requirements, requirements it marks not
applicable, the evidence sources the agent may use, and (unused for now) an asset register
and risks.
"""

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from vouch.documents import SUFFIX, DocumentChoice, document_path, escaped, is_chosen
from vouch.isa import (
    SOURCE,
    BaseFramework,
    BaseFrameworkError,
    Column,
    IsaControl,
    Requirement,
    UnknownControlError,
    load_base_framework,
)
from vouch.paths import FIXTURES_DIR

log = logging.getLogger(__name__)

PROFILE_FILE = "profile.yaml"
DEFAULT_PROFILE = "nordlicht"  # a request naming no profile is under this one (#47)
NORDLICHT_PROFILE = FIXTURES_DIR / "nordlicht" / PROFILE_FILE
# The sample library's reference profile (#370): the eval judges the library under it, and it is
# no company, so no user sees it (vouch.api.user_profiles).
SAMPLE_LIBRARY = "sample-library"

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

    @property
    def overlay(self) -> tuple[Requirement, ...]:
        """The requirements the country overlays add: the applicable ones, then the rest."""
        every = self.applicable + tuple(n.requirement for n in self.not_applicable)
        return tuple(r for r in every if r.column == "overlay")


class ProfileError(ValueError):
    """A compliance profile is missing or invalid; the message names the file and the field."""

    def __init__(self, path: Path, problem: str):
        super().__init__(f"invalid compliance profile {path}: {problem}")
        self.path = path
        self.problem = problem  # without the path: what a developer page may show


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
        try:
            found = resolved.is_dir()
        except OSError as error:  # a name the file system refuses, such as one too long
            raise ValueError(f"folder {folder} can't be used: {error.strerror or error}") from None
        if not found:
            raise ValueError(f"folder {resolved} does not exist")
        return resolved

    # The documents of the folder the profile may use (ids: file names without .md); None, the
    # whole folder. A company set up in the app chooses from the sample library (#384).
    # An id given with `.md` is kept without it.
    documents: DocumentChoice = Field(default=None, min_length=1)

    @field_validator("documents", mode="after")
    @classmethod
    def _documents_in_folder(
        cls, documents: DocumentChoice, info: ValidationInfo
    ) -> DocumentChoice:
        folder = info.data.get("folder")
        if documents is None or folder is None:  # no choice, or the folder already refused
            return documents
        if escaped(folder):  # swapped for a link since its own check: none of it may be read
            raise ValueError(f"the folder {folder.name} now points outside its allowed folder")
        ids = tuple(doc_id.removesuffix(SUFFIX) for doc_id in documents)
        seen: set[str] = set()
        for doc_id in ids:
            if doc_id in seen:
                raise ValueError(f"{doc_id} is named twice")
            seen.add(doc_id)
            # An id stripped of one .md must not end in another: document_path would strip it
            # again and find the file, while every reader compares the stored id (#384).
            if doc_id.endswith(SUFFIX) or document_path(folder, doc_id) is None:
                raise ValueError(f"no document {doc_id} in the folder {folder.name}")
        return ids

    def uses(self, doc_id: str) -> bool:
        """Whether the profile may use this document of the folder (every one, with no choice)."""
        return is_chosen(doc_id, self.documents)


class CountryOverlayRequirement(_Strict):
    control: str  # the base framework control it adds to; the profile checks it exists
    id: str = Field(pattern=r"^O[1-9][0-9]*$")
    text: NonEmpty


class CountryOverlay(_Strict):
    """A country overlay: national rules adding requirements to base framework controls."""

    name: NonEmpty  # becomes each of its requirements' source
    requirements: tuple[CountryOverlayRequirement, ...]


class NotApplicable(_Strict):
    control: str  # the profile checks it exists
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
    def _controls_exist_in_the_profiles_isa_edition(self) -> Self:
        """Runs before the other model checks, which look the controls up (a test with an
        unknown not-applicable control pins this order)."""
        named = [
            (f"overlays.{i}.requirements.{j}.control", added.control)
            for i, overlay in enumerate(self.overlays)
            for j, added in enumerate(overlay.requirements)
        ] + [
            (f"not_applicable.{i}.control", entry.control)
            for i, entry in enumerate(self.not_applicable)
        ]
        for field, control_id in named:
            try:
                self.isa_control(control_id)
            except UnknownControlError as error:
                raise ValueError(f"{field}: unknown control {control_id} in {SOURCE}") from error
        return self

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
            control = self.isa_control(entry.control)
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

    def document_choices(self) -> dict[str, list[str]]:
        """Each source limited to chosen documents, to its chosen ids; a source using its whole
        folder is absent. An assessment keeps it in its state (`document_choices`)."""
        return {
            name: list(source.documents)
            for name, source in self.evidence_sources.items()
            if source.documents is not None
        }

    def isa_edition(self) -> BaseFramework:
        """The base framework in the ISA edition of this profile's answer language (English
        when the ISA isn't shipped in it), or BaseFrameworkError naming the file."""
        return load_base_framework(language=self.answer_language)

    def isa_control(self, control_id: str) -> IsaControl:
        """The control in this profile's ISA edition (`isa_edition`), or UnknownControlError."""
        for control in self.isa_edition().controls:
            if control.id == control_id:
                return control
        raise UnknownControlError(control_id)

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
    except UnicodeDecodeError as error:  # saved in another encoding, such as Latin-1
        raise ProfileError(path, f"not UTF-8 text (byte {error.start})") from error
    except RecursionError as error:  # PyYAML composes nested collections recursively
        raise ProfileError(path, "not valid YAML (nested too deeply)") from error
    except yaml.YAMLError as error:
        raise ProfileError(path, f"not valid YAML ({error.__class__.__name__})") from error
    try:
        return ComplianceProfile.model_validate(data, context={"base_dir": path.parent})
    except ValidationError as error:
        raise ProfileError(path, _describe(error)) from error
    except BaseFrameworkError as error:  # the controls it names couldn't be looked up
        raise ProfileError(path, str(error)) from error
    except OSError as error:  # a backstop: a path it names the file system refuses
        raise ProfileError(
            path, f"a path it names can't be used: {error.strerror or error}"
        ) from error


PROFILE_LOAD_ERRORS: tuple[type[Exception], ...] = (ProfileError, BaseFrameworkError)
"""What loading a profile that is there raises when it can't be used: the profile file invalid,
or the base framework it needs unreadable. Add a new load error here, and every caller that
leaves out or refuses a broken profile catches it (vouch.api)."""


_PATH = re.compile(r"(?<![\w.])/(?:[^\s/:;,'\"]+/)*([^\s/:;,'\"]+)")
"""An absolute path in a message; its last part is group 1."""


def load_problem(error: Exception) -> str:
    """A load error's problem in words, each path in it cut to its last part (a folder or a
    file's name), so a developer page shows no server path."""
    text = error.problem if isinstance(error, ProfileError) else str(error)
    return _PATH.sub(r"\1", text)


class UnknownProfileError(LookupError):
    """No profile folder with this name in the profiles folder (Settings.profiles_dir)."""

    def __init__(self, name: str):
        super().__init__(f"unknown profile: {name}")
        self.name = name


PROFILE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
"""A profile's name, its folder's: one lowercase word or slug of at most 63 characters, so it
never names a path, nor one too long for the file system."""


def load_named_profile(name: str, folder: Path = FIXTURES_DIR) -> ComplianceProfile:
    """The profile in `<folder>/<name>/profile.yaml`; the API and the CLI pass their
    Settings.profiles_dir."""
    if not PROFILE_NAME.fullmatch(name):
        raise UnknownProfileError(name)
    path = folder / name / PROFILE_FILE
    try:
        found = path.is_file()
    except OSError:  # a name the file system refuses is no profile it holds
        found = False
    if not found:
        raise UnknownProfileError(name)
    return load_profile(path)


@dataclass(frozen=True)
class BrokenProfile:
    """A profile left out because it can't be loaded: its name and the problem, path-free."""

    id: str
    problem: str


def _version(folder: Path, name: str) -> tuple[int, int, int] | None:
    """The profile file's version: its modification time, its change time and its size; None
    when it can't be looked at (then the load says why). The change time moves with a change
    of permissions or owner too, so a profile left out as unreadable and then made readable is
    read again (#167)."""
    try:
        stat = (folder / name / PROFILE_FILE).stat()
    except (OSError, ValueError):
        return None
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


@lru_cache(maxsize=256)
def _read_version(
    folder: Path, name: str, version: tuple[int, int, int] | None
) -> ComplianceProfile | BrokenProfile:
    try:
        return load_named_profile(name, folder)
    except PROFILE_LOAD_ERRORS as error:
        log.warning("compliance profile %s left out: %s", name, error)
        return BrokenProfile(name, load_problem(error))


class _ProfileReader:
    """read_profile_once: a function with its cache's `cache_clear`, typed (#129)."""

    def __call__(self, folder: Path, name: str) -> ComplianceProfile | BrokenProfile:
        """The named profile in `folder`, or why it can't be used: read once per version of its
        file (logged once per broken version), so a profile fixed or changed while the app runs
        is read again. Raises UnknownProfileError, which isn't kept: a profile added later is
        found."""
        return _read_version(folder, name, _version(folder, name))

    def cache_clear(self) -> None:
        _read_version.cache_clear()


read_profile_once = _ProfileReader()


@dataclass(frozen=True)
class ProfileScan:
    """The profiles in a profiles folder: those that load, and those left out."""

    loaded: tuple[ComplianceProfile, ...]
    broken: tuple[BrokenProfile, ...]

    def find(self, name: str) -> ComplianceProfile | BrokenProfile | None:
        """The profile in the folder `name`, loaded or broken; None when there's none."""
        profiles: tuple[ComplianceProfile | BrokenProfile, ...] = (*self.loaded, *self.broken)
        return next((p for p in profiles if p.id == name), None)


def scan_profiles(folder: Path) -> ProfileScan:
    """Every profile in `folder`, each read once (read_profile_once)."""
    read = [read_profile_once(folder, name) for name in profile_names(folder)]
    loaded = tuple(p for p in read if isinstance(p, ComplianceProfile))
    broken = tuple(p for p in read if isinstance(p, BrokenProfile))
    return ProfileScan(loaded, broken)


def profile_names(folder: Path) -> tuple[str, ...]:
    """The names of the profiles in `folder`, sorted: each a folder with a profile file, valid
    or not; none when the folder can't be read."""
    try:
        found = [path.parent.name for path in folder.glob(f"*/{PROFILE_FILE}") if path.is_file()]
    except OSError:
        log.exception("the compliance profiles in %s could not be listed", folder)
        return ()
    return tuple(sorted(name for name in found if PROFILE_NAME.fullmatch(name)))
