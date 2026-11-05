"""The Dossier overview (#40): where the compliance lead stands, from the assessments kept for
them under one compliance profile.

Readiness counts accepted verdicts only (GLOSSARY: Reviewed verdict, Readiness): a proposed or a
rejected assessment shows on its control's card, but not in the counts.
"""

import threading
from collections.abc import Iterable, Mapping
from typing import Any, Literal, Protocol

from vouch.graph import Assessment
from vouch.isa import IsaControl
from vouch.profile import ComplianceProfile

QUOTES_SHOWN = 6  # the evidence quote cards on the overview, at most
NOT_ASSESSED = "not-assessed"

NextAction = Literal["review", "assess-again", "assess"]


class AssessmentRecords(Protocol):
    """Each user's latest assessment of each control, per compliance profile."""

    def keep(self, user_id: str, assessment: Assessment) -> None:
        """Keep `assessment` as the latest of its control, proposed or reviewed."""
        ...

    def latest(self, user_id: str, profile_id: str) -> dict[str, Assessment]:
        """The user's latest assessment of each control under the profile, by control id."""
        ...


class MemoryAssessmentRecords:
    """The records in memory, one per app, until the Postgres store (#38): a restart forgets
    them, as it forgets the paused runs in the in-memory checkpointer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()  # the stream records from the threadpool
        self._latest: dict[tuple[str, str], dict[str, Assessment]] = {}

    def keep(self, user_id: str, assessment: Assessment) -> None:
        with self._lock:
            mine = self._latest.setdefault((user_id, assessment.profile_id), {})
            mine[assessment.control_id] = assessment

    def latest(self, user_id: str, profile_id: str) -> dict[str, Assessment]:
        with self._lock:
            return dict(self._latest.get((user_id, profile_id), {}))


def overview(
    profile: ComplianceProfile,
    controls: Iterable[IsaControl],
    latest: Mapping[str, Assessment],
) -> dict[str, Any]:
    """The overview: the company, readiness from accepted verdicts only, a card per control
    (in the base framework's order) with its review state and verdict, the evidence quotes from
    assessed controls, and the next step (None once every control is accepted)."""
    controls = list(controls)
    cards = [_card(control, latest.get(control.id)) for control in controls]
    accepted = [a for a in latest.values() if a.status == "accepted"]
    by_verdict = {v: sum(a.verdict == v for a in accepted) for v in _VERDICT_COUNTS}
    readiness = {
        "controls": len(controls),
        "accepted": len(accepted),
        **{_VERDICT_COUNTS[v]: n for v, n in by_verdict.items()},
        "open": len(controls) - len(accepted),
        "remediation_items": sum(len(a.remediation) for a in accepted),
    }
    return {
        "company": profile.company,
        "country": profile.country,
        "readiness": readiness,
        "next_step": _next_step(cards),
        "controls": cards,
        "quotes": _quotes(controls, latest),
    }


_VERDICT_COUNTS = {
    "evidenced": "evidenced",
    "partly-evidenced": "partly_evidenced",
    "not-evidenced": "not_evidenced",
    "not-applicable": "not_applicable",
}

_NEXT: tuple[tuple[str, NextAction], ...] = (
    ("waiting-for-review", "review"),
    ("rejected", "assess-again"),
    (NOT_ASSESSED, "assess"),
)
"""What the lead does next: review a waiting run first, then assess a rejected control again,
then assess one not yet assessed; the first such control in the base framework's order."""


def _card(control: IsaControl, assessment: Assessment | None) -> dict[str, Any]:
    return {
        "id": control.id,
        "question": control.question,
        "iso_27001": control.iso_27001,
        "status": assessment.status if assessment else NOT_ASSESSED,
        "verdict": assessment.verdict if assessment else None,
    }


def _next_step(cards: list[dict[str, Any]]) -> dict[str, str] | None:
    for status, action in _NEXT:
        for card in cards:
            if card["status"] == status:
                return {"control_id": card["id"], "action": action}
    return None


def _quotes(controls: list[IsaControl], latest: Mapping[str, Assessment]) -> list[dict[str, Any]]:
    """Each checked quote once, control by control, up to QUOTES_SHOWN."""
    quotes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for control in controls:
        assessment = latest.get(control.id)
        for requirement in assessment.requirements if assessment else ():
            for citation in requirement.citations:
                if len(quotes) == QUOTES_SHOWN:
                    return quotes
                if (citation.doc_id, citation.quote) in seen:
                    continue
                seen.add((citation.doc_id, citation.quote))
                quotes.append(
                    {
                        "control_id": control.id,
                        "requirement_id": requirement.id,
                        "doc_id": citation.doc_id,
                        "version": citation.version,
                        "section": citation.section,
                        "quote": citation.quote,
                        "kind": citation.kind,
                        "stale": citation.stale,
                    }
                )
    return quotes
