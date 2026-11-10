"""The Dossier overview (#40): where the compliance lead stands, from each control's newest
assessment under one compliance profile (vouch.memory's `newest_by_control`, from the threads).

Readiness counts accepted verdicts only (GLOSSARY: Reviewed verdict, Readiness): a proposed or a
rejected assessment shows on its control's card, but not in the counts.
"""

import itertools
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from typing import Literal, TypedDict, get_args

from vouch.citations import EvidenceKind
from vouch.graph import Assessment, Status, Verdict
from vouch.isa import Column, IsaControl, Requirement
from vouch.plan import CardFlags, ReassessReason
from vouch.profile import ComplianceProfile, ProtectionLevel

QUOTES_SHOWN = 6  # the evidence quote cards on the overview, at most

CardStatus = Status | Literal["not-assessed"]
NOT_ASSESSED: CardStatus = "not-assessed"
CARD_STATUSES: tuple[CardStatus, ...] = (NOT_ASSESSED, *get_args(Status))
VERDICTS: tuple[Verdict, ...] = ("evidenced", "partly-evidenced", "not-evidenced", "not-applicable")

NextAction = Literal["review", "assess-again", "assess"]


class Readiness(TypedDict):
    """Counted from the listed controls' accepted verdicts only; `open` is every listed control
    not accepted."""

    controls: int
    accepted: int
    by_verdict: dict[Verdict, int]
    open: int
    remediation_items: int  # the accepted controls' items the lead hasn't discarded (#43)
    remediation_approved: int  # of those, the ones the lead approved: the report's count (#310)


class ControlCard(TypedDict):
    id: str
    question: str
    iso_27001: str
    status: CardStatus
    verdict: Verdict | None
    # The improvement plan's (#410, vouch.plan.card_flags) and the open questions' (#414,
    # vouch.questions): false (empty) on a control not assessed; `closed` and the `fixes-done`
    # reason under Internal improvement only. `reassess_suggested` is true exactly when
    # `reassess_reasons` isn't empty. None changes the status, the counts or the next step.
    reassess_suggested: bool
    reassess_reasons: list[ReassessReason]
    closed: bool


class Quote(TypedDict):
    control_id: str
    requirement_id: str
    doc_id: str
    version: str
    section: str
    quote: str
    kind: EvidenceKind
    stale: bool


class NextStep(TypedDict):
    control_id: str
    action: NextAction


class PaperRequirement(TypedDict):
    id: str
    column: Column
    text: str
    source: str  # the base framework, or the country overlay's name
    not_applicable: str | None  # the profile's reason; None when it applies


class Overview(TypedDict):
    company: str
    country: str
    # The working paper's head shows the profile's protection level and answer language (#41).
    protection_level: ProtectionLevel
    answer_language: str  # ISO 639-1
    readiness: Readiness
    by_status: dict[CardStatus, int]  # how many listed controls are in each status
    next_step: NextStep | None
    controls: list[ControlCard]
    quotes: list[Quote]


def control_requirements(profile: ComplianceProfile, control: IsaControl) -> list[PaperRequirement]:
    """Each requirement the profile brings in for the control: applicable ones first, then the
    not applicable ones with the profile's reason (the order an assessment lists them in)."""
    requirements = profile.requirements_for(control)

    def row(r: Requirement, not_applicable: str | None) -> PaperRequirement:
        return {
            "id": r.id,
            "column": r.column,
            "text": r.text,
            "source": r.source,
            "not_applicable": not_applicable,
        }

    return [row(r, None) for r in requirements.applicable] + [
        row(n.requirement, n.reason) for n in requirements.not_applicable
    ]


def requirements_by_control(
    profile: ComplianceProfile, controls: Iterable[IsaControl]
) -> dict[str, list[PaperRequirement]]:
    """Each control's requirements under the profile, by control id: what its working paper
    lists before an assessment (#41). They change only with the profile; the API sends them with
    the overview, so opening a paper costs no request of its own (each counts to the rate limit)."""
    return {c.id: control_requirements(profile, c) for c in controls}


def readiness(
    controls: Iterable[IsaControl],
    latest: Mapping[str, Assessment],
    discarded: Collection[tuple[str, str]] = (),
    approved: Collection[tuple[str, str]] = (),
) -> Readiness:
    """Readiness from the listed controls' accepted verdicts only (`latest`: each control's
    newest assessment); `discarded` names the remediation items the lead discarded, by thread and
    requirement id, which `remediation_items` leaves out (#43); `approved` those approved, which
    `remediation_approved` counts (#310). The overview's, and the readiness report's (#234), so
    the two never disagree."""
    controls = list(controls)
    listed = [latest[c.id] for c in controls if c.id in latest]
    accepted = [a for a in listed if a.status == "accepted"]
    verdicts = Counter(a.verdict for a in accepted)
    items = [(a.thread_id, i.requirement_id) for a in accepted for i in a.remediation]
    return {
        "controls": len(controls),
        "accepted": len(accepted),
        "by_verdict": {v: verdicts[v] for v in VERDICTS},
        "open": len(controls) - len(accepted),
        "remediation_items": sum(key not in discarded for key in items),
        "remediation_approved": sum(key in approved and key not in discarded for key in items),
    }


def overview(
    profile: ComplianceProfile,
    controls: Iterable[IsaControl],
    latest: Mapping[str, Assessment],
    discarded: Collection[tuple[str, str]] = (),
    approved: Collection[tuple[str, str]] = (),
    flags: Mapping[str, CardFlags] | None = None,
) -> Overview:
    """The overview: the company, the profile's protection level and answer language, readiness from accepted verdicts only, a card per control
    (in the base framework's order) with its review state and verdict, how many controls are in
    each state, the evidence quotes from assessed controls not rejected, and the next step (None
    once every control is accepted). Assessments of controls not in `controls` are left out.
    `discarded` names the remediation items the lead discarded, by thread and requirement id:
    the band counts the others (#43); `approved` those approved, counted apart (#310). `flags`
    are the improvement plan's and the open questions' per control (#410, #414; none: no flag
    set)."""
    controls = list(controls)
    listed = {c.id: latest[c.id] for c in controls if c.id in latest}
    flags = flags or {}
    cards = [_card(control, listed.get(control.id), flags.get(control.id)) for control in controls]
    statuses = Counter(card["status"] for card in cards)
    return {
        "company": profile.company,
        "country": profile.country,
        "protection_level": profile.protection_level,
        "answer_language": profile.answer_language,
        "readiness": readiness(controls, listed, discarded, approved),
        "by_status": {status: statuses[status] for status in CARD_STATUSES},
        "next_step": _next_step(cards),
        "controls": cards,
        "quotes": _quotes(controls, listed),
    }


_NEXT: tuple[tuple[CardStatus, NextAction], ...] = (
    ("waiting-for-review", "review"),
    ("rejected", "assess-again"),
    (NOT_ASSESSED, "assess"),
)
"""What the lead does next: review a waiting run first, then assess a rejected control again,
then assess one not yet assessed; the first such control in the base framework's order."""


def _card(
    control: IsaControl, assessment: Assessment | None, flags: CardFlags | None
) -> ControlCard:
    return {
        "id": control.id,
        "question": control.question,
        "iso_27001": control.iso_27001,
        "status": assessment.status if assessment else NOT_ASSESSED,
        "verdict": assessment.verdict if assessment else None,
        "reassess_suggested": bool(assessment and flags and flags.reassess_suggested),
        "reassess_reasons": list(flags.reasons) if assessment and flags else [],
        "closed": bool(assessment and flags and flags.closed),
    }


def _next_step(cards: list[ControlCard]) -> NextStep | None:
    for status, action in _NEXT:
        for card in cards:
            if card["status"] == status:
                return {"control_id": card["id"], "action": action}
    return None


def _quotes(controls: list[IsaControl], latest: Mapping[str, Assessment]) -> list[Quote]:
    """Each checked quote once, up to QUOTES_SHOWN, spread across the assessed controls: one
    from each in control order, then a second from each, and so on (#147), so the first
    control's evidence doesn't fill them all. None from a rejected assessment, whose evidence
    the lead turned down."""
    per_control: list[list[Quote]] = []
    for control in controls:
        assessment = latest.get(control.id)
        if assessment is None or assessment.status == "rejected":
            continue
        per_control.append(
            [
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
                for requirement in assessment.requirements
                for citation in requirement.citations
            ]
        )
    quotes: list[Quote] = []
    seen: set[tuple[str, str]] = set()
    for round_ in itertools.zip_longest(*per_control):  # each control's 1st, then 2nd, ...
        for found in round_:
            if found is None or (found["doc_id"], found["quote"]) in seen:
                continue
            seen.add((found["doc_id"], found["quote"]))
            quotes.append(found)
            if len(quotes) == QUOTES_SHOWN:
                return quotes
    return quotes
