"""The base framework: Vouch's 12 VDA ISA 2027.1 controls and their requirements.

Read from `data/vda-isa-2027.1/controls.yaml` (English) or `controls-de.yaml` (German), each
extracted unchanged from its ISA Excel beside it (`vouch.isa_extract`); licence and credit are
in that folder's NOTICE.md. Both editions carry the same requirement ids, so a compliance
profile picks its edition by its answer language without changing what it names.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

import yaml

FRAMEWORK_DIR = Path(__file__).resolve().parent / "data" / "vda-isa-2027.1"
CONTROLS_FILE = FRAMEWORK_DIR / "controls.yaml"  # the English edition, the default
CONTROLS_DE_FILE = FRAMEWORK_DIR / "controls-de.yaml"
NOTICE_FILE = FRAMEWORK_DIR / "NOTICE.md"
SOURCE = "VDA ISA 2027.1"

Column = Literal["must", "should", "high", "very high", "overlay"]
# Each base framework column and the letter its requirement ids start with, in the Excel's
# order. Overlay requirements (O1, ...) come from a compliance profile, never from the Excel.
COLUMN_PREFIX: dict[Column, str] = {"must": "M", "should": "S", "high": "H", "very high": "V"}


class UnknownControlError(LookupError):
    """No control with this id exists in the base framework."""

    def __init__(self, control_id: str):
        super().__init__(f"unknown control id: {control_id}")
        self.control_id = control_id


class BaseFrameworkError(RuntimeError):
    """A base framework data file is missing or doesn't parse; the message names the file."""

    def __init__(self, path: Path, problem: str):
        super().__init__(f"cannot read base framework file {path}: {problem}")
        self.path = path


@dataclass(frozen=True)
class Requirement:
    id: str  # M1, S2, H1, V1: the column's letter and its bullet's position; O1 for an overlay
    column: Column
    text: str  # unchanged from the ISA, or the overlay's own words
    source: str  # the base framework, or the country overlay's name


@dataclass(frozen=True)
class IsaControl:
    id: str  # the ISA control number, such as 5.1.1
    question: str  # ISA's control question, unchanged
    iso_27001: str  # ISA's ISO 27001 reference, as written (e.g. "ISO 27001:2022: A.8.24")
    requirements: tuple[Requirement, ...]
    source: str  # "VDA ISA 2027.1"


@dataclass(frozen=True)
class BaseFramework:
    source: str
    controls: tuple[IsaControl, ...]


def _field(mapping: object, key: str, kind: type, where: str):
    """mapping[key], checked to be of this kind; ValueError or TypeError saying where."""
    if not isinstance(mapping, dict):
        raise TypeError(f"{where}: expected a mapping, found {type(mapping).__name__}")
    if key not in mapping:
        raise ValueError(f"{where}: missing field '{key}'")
    value = mapping[key]
    if not isinstance(value, kind):
        raise TypeError(f"{where}: '{key}' must be a {kind.__name__}, found {value!r}")
    return value


def _parse(data: object) -> BaseFramework:
    source = _field(data, "source", str, "top level")
    controls = []
    for entry in _field(data, "controls", list, "top level"):
        control_id = _field(entry, "id", str, "a control")
        where = f"control {control_id}"
        question = _field(entry, "question", str, where)
        iso_27001 = _field(entry, "iso_27001", str, where)
        requirements = []
        counts = dict.fromkeys(COLUMN_PREFIX, 0)
        for req in _field(entry, "requirements", list, where):
            req_id = _field(req, "id", str, f"{where}, a requirement")
            column = _field(req, "column", str, f"{where} {req_id}")
            if column not in COLUMN_PREFIX:
                raise ValueError(f"{where} {req_id}: unknown column {column!r}")
            counts[column] += 1
            expected = f"{COLUMN_PREFIX[column]}{counts[column]}"
            if req_id != expected:
                raise ValueError(
                    f"{where} {req_id}: expected id {expected} "
                    f"(each column numbers its requirements from 1, without gaps or repeats)"
                )
            text = _field(req, "text", str, f"{where} {req_id}")
            requirements.append(Requirement(req_id, column, text, source))
        controls.append(IsaControl(control_id, question, iso_27001, tuple(requirements), source))
    return BaseFramework(source, tuple(controls))


@cache
def _load(path: Path) -> BaseFramework:
    try:
        return _parse(yaml.safe_load(path.read_text(encoding="utf-8")))
    except OSError as error:
        raise BaseFrameworkError(path, error.strerror or str(error)) from error
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        at = f" at line {mark.line + 1}" if mark is not None else ""
        raise BaseFrameworkError(
            path, f"not valid YAML{at} ({error.__class__.__name__})"
        ) from error
    except (TypeError, ValueError) as error:
        raise BaseFrameworkError(path, str(error)) from error


def edition_files() -> dict[str, Path]:
    """Each shipped ISA edition's controls file, by ISO 639-1 language: the one map of
    editions, which `vouch.isa_extract` writes to. Built on each call, so a test can swap a
    file."""
    return {"en": CONTROLS_FILE, "de": CONTROLS_DE_FILE}


def edition_file(language: str) -> Path:
    """The controls file of the ISA edition in this language; English for a language the
    ISA isn't shipped in."""
    return edition_files().get(language, CONTROLS_FILE)


def load_base_framework(path: Path | None = None, *, language: str = "en") -> BaseFramework:
    """The base framework in this language's edition, or the file at `path` (each read once),
    or BaseFrameworkError naming the file."""
    return _load(edition_file(language) if path is None else path)


def load_isa_control(control_id: str, language: str = "en") -> IsaControl:
    """The control with this ISA number, in this language's edition, or UnknownControlError."""
    for control in load_base_framework(language=language).controls:
        if control.id == control_id:
            return control
    raise UnknownControlError(control_id)
