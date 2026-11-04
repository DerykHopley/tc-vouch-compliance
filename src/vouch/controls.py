"""The company's controls, read from the fixture catalogue."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from vouch.paths import FIXTURES_DIR

CONTROLS_FILE = FIXTURES_DIR / "nordlicht" / "controls.yaml"


class UnknownControlError(LookupError):
    """No control with this id exists in the catalogue."""

    def __init__(self, control_id: str):
        super().__init__(f"unknown control id: {control_id}")
        self.control_id = control_id


@dataclass(frozen=True)
class EvidencePoint:
    id: str
    text: str


@dataclass(frozen=True)
class Control:
    id: str
    title: str
    statement: str
    owner_role: str
    evidence_points: tuple[EvidencePoint, ...]


def load_control(control_id: str, path: Path = CONTROLS_FILE) -> Control:
    """Return the control with this id, or raise UnknownControlError."""
    for entry in yaml.safe_load(path.read_text())["controls"]:
        if entry["id"] == control_id:
            return Control(
                id=entry["id"],
                title=entry["title"],
                statement=entry["statement"],
                owner_role=entry["owner_role"],
                evidence_points=tuple(EvidencePoint(**p) for p in entry["evidence_points"]),
            )
    raise UnknownControlError(control_id)
