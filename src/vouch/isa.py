"""The base framework: Vouch's 12 VDA ISA 2027.1 controls and their requirements.

Read from `data/vda-isa-2027.1/controls.yaml`, extracted unchanged from the ISA Excel beside it
(`vouch.isa_extract`); licence and credit are in that folder's NOTICE.md.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from vouch.controls import UnknownControlError

CATALOGUE_DIR = Path(__file__).resolve().parent / "data" / "vda-isa-2027.1"
CONTROLS_FILE = CATALOGUE_DIR / "controls.yaml"

Column = Literal["must", "should", "high", "very high"]
COLUMNS: tuple[Column, ...] = ("must", "should", "high", "very high")


class CatalogueError(RuntimeError):
    """A catalogue data file is missing or doesn't parse; the message names the file."""

    def __init__(self, path: Path, problem: str):
        super().__init__(f"cannot read ISA catalogue {path}: {problem}")
        self.path = path


@dataclass(frozen=True)
class Requirement:
    id: str  # M1, S2, H1, V1: the column's letter and its bullet's position
    column: Column
    text: str  # unchanged from the ISA
    source: str  # "VDA ISA 2027.1"


@dataclass(frozen=True)
class IsaControl:
    id: str  # the ISA control number, such as 5.1.1
    question: str  # ISA's control question, unchanged
    iso_27001: str  # ISA's ISO 27001 reference, as written (e.g. "ISO 27001:2022: A.8.24")
    requirements: tuple[Requirement, ...]


@dataclass(frozen=True)
class Catalogue:
    source: str
    controls: tuple[IsaControl, ...]


def _parse(data: object) -> Catalogue:
    if not isinstance(data, dict):
        raise TypeError("expected a mapping with 'source' and 'controls'")
    source = data["source"]
    controls = []
    for entry in data["controls"]:
        requirements = []
        for req in entry["requirements"]:
            if req["column"] not in COLUMNS:
                raise ValueError(f"control {entry['id']}: unknown column {req['column']!r}")
            requirements.append(Requirement(req["id"], req["column"], req["text"], source))
        controls.append(
            IsaControl(entry["id"], entry["question"], entry["iso_27001"], tuple(requirements))
        )
    return Catalogue(source, tuple(controls))


def load_catalogue(path: Path | None = None) -> Catalogue:
    """The catalogue, or CatalogueError naming the file."""
    path = CONTROLS_FILE if path is None else path
    try:
        return _parse(yaml.safe_load(path.read_text(encoding="utf-8")))
    except OSError as error:
        raise CatalogueError(path, error.strerror or str(error)) from error
    except yaml.YAMLError as error:
        raise CatalogueError(path, f"not valid YAML ({error.__class__.__name__})") from error
    except KeyError as error:
        raise CatalogueError(path, f"missing field {error}") from error
    except (TypeError, ValueError) as error:
        raise CatalogueError(path, str(error)) from error


def get_control(control_id: str, path: Path | None = None) -> IsaControl:
    """The control with this ISA number, or UnknownControlError."""
    for control in load_catalogue(path).controls:
        if control.id == control_id:
            return control
    raise UnknownControlError(control_id)
