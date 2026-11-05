"""Remediation items the compliance lead approves or discards (#43).

The assessment graph drafts one item per not-evidenced requirement (vouch.graph's
RemediationItem), kept in the assessment's thread. Once the control's verdict is accepted, the
lead approves each draft (maybe with another owner role or due date) or discards it. The
decision is kept per user and compliance profile, keyed by the assessment's thread and the
requirement, with its audit entry: both, or neither (Postgres: one transaction,
vouch.database's `decide_remediation`; without a database, this process's memory and trail,
whose writes can't fail).

A decision is final: the same decision again changes nothing and writes no entry (a double
click approves once); the other one is refused. Only an accepted assessment's items are
decided, as the review can still change a proposed one's (an override adds or removes items),
and a rejected one is assessed again.
"""

import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from functools import cache
from typing import Annotated, Any, Literal, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, StringConstraints

from vouch.audit import AuditEntry, ProcessTrail, Reviewer, evidence_versions, process_trail
from vouch.database import Database
from vouch.graph import NO_NUL_PATTERN, Assessment, RemediationItem, load_assessment

ItemStatus = Literal["draft", "approved", "discarded"]
Decision = Literal["approve", "discard"]
DECIDED: dict[Decision, ItemStatus] = {"approve": "approved", "discard": "discarded"}
MAX_OWNER_ROLE = 200

PAST_DUE = "The due date {due} has passed. Choose today or a later date."
NOT_ACCEPTED = "Remediation items are approved or discarded once the control's verdict is accepted."
NO_ITEM = "This assessment has no remediation item for requirement {requirement_id}."
DECIDED_ALREADY = "This remediation item was {status} already."


class DecisionRequest(BaseModel):
    """The lead's decision on one remediation item; an approval may change its owner role and
    due date first."""

    decision: Decision
    owner_role: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=MAX_OWNER_ROLE,
                pattern=NO_NUL_PATTERN,
            ),
        ]
        | None
    ) = None
    due_date: date | None = None


@dataclass(frozen=True)
class DecidedItem:
    """A remediation item as the lead decided it."""

    thread_id: str
    requirement_id: str
    status: ItemStatus
    missing: str
    owner_role: str
    due_date: str  # ISO 8601
    decided_at: datetime

    def shown(self) -> dict[str, Any]:
        """What the API shows of it, as of a draft."""
        return item_data(self, self.status)


Key = tuple[str, str]
"""An item's thread id and requirement id."""


class PastDueError(ValueError):
    """An approval with a due date before today (a 422)."""


class NotAcceptedError(RuntimeError):
    """The assessment isn't accepted, so its items wait (a 409)."""


class UnknownItemError(LookupError):
    """The assessment has no item for that requirement (a 404)."""


class DecidedAlreadyError(RuntimeError):
    """The item was decided the other way already (a 409)."""


def item_data(item: RemediationItem | DecidedItem, status: ItemStatus) -> dict[str, Any]:
    return {
        "requirement_id": item.requirement_id,
        "missing": item.missing,
        "owner_role": item.owner_role,
        "due_date": item.due_date,
        "status": status,
    }


def shown_items(assessment: Assessment, decided: Mapping[Key, DecidedItem]) -> list[dict[str, Any]]:
    """The assessment's remediation items as the API shows them: each draft, or as decided."""
    shown = []
    for item in assessment.remediation:
        kept = decided.get((assessment.thread_id, item.requirement_id))
        shown.append(kept.shown() if kept else item_data(item, "draft"))
    return shown


class RemediationItems(Protocol):
    """Where the lead's decisions are kept, each with its audit entry."""

    def decide(
        self, user_id: str, profile_id: str, item: DecidedItem, entry: AuditEntry
    ) -> tuple[DecidedItem, bool]:
        """Keep the item and its entry together, unless the item was decided already: then
        nothing is written. (The kept item, whether this call decided it.)"""
        ...

    def decided(
        self, user_id: str, profile_id: str, thread_id: str | None = None
    ) -> dict[Key, DecidedItem]:
        """The user's decided items under the profile (one thread's, when given)."""
        ...


class ProcessItems:
    """Decisions without a database: in this process, their entries in its audit trail
    (vouch.audit's, read at each decision, as the trail's own checkpointer is)."""

    def __init__(self, trail: Callable[[], ProcessTrail] = process_trail) -> None:
        self._trail = trail
        self._items: dict[tuple[str, str, str, str], DecidedItem] = {}
        self._guard = threading.Lock()

    def decide(
        self, user_id: str, profile_id: str, item: DecidedItem, entry: AuditEntry
    ) -> tuple[DecidedItem, bool]:
        key = (user_id, profile_id, item.thread_id, item.requirement_id)
        with self._guard:
            kept = self._items.get(key)
            if kept is not None:
                return kept, False
            self._items[key] = item
            self._trail().keep([entry])
        return item, True

    def decided(
        self, user_id: str, profile_id: str, thread_id: str | None = None
    ) -> dict[Key, DecidedItem]:
        with self._guard:
            return {
                (thread, requirement): item
                for (user, profile, thread, requirement), item in self._items.items()
                if (user, profile) == (user_id, profile_id) and thread_id in (None, thread)
            }


def _from_row(row: Mapping[str, Any]) -> DecidedItem:
    due = row["due_date"]
    return DecidedItem(
        thread_id=row["thread_id"],
        requirement_id=row["requirement_id"],
        status=row["status"],
        missing=row["missing"],
        owner_role=row["owner_role"],
        due_date=due.isoformat() if isinstance(due, date) else due,
        decided_at=row["decided_at"],
    )


class PostgresItems:
    """Decisions in Postgres (vouch.database), each in one transaction with its entry."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def decide(
        self, user_id: str, profile_id: str, item: DecidedItem, entry: AuditEntry
    ) -> tuple[DecidedItem, bool]:
        row = asdict(item) | {"user_id": user_id, "profile_id": profile_id}
        audit_row = {k: v for k, v in asdict(entry).items() if k != "id"}
        kept, new = self.db.decide_remediation(row, [audit_row])
        return _from_row(kept), new

    def decided(
        self, user_id: str, profile_id: str, thread_id: str | None = None
    ) -> dict[Key, DecidedItem]:
        rows = self.db.remediation_items(user_id, profile_id, thread_id)
        items = [_from_row(row) for row in rows]
        return {(i.thread_id, i.requirement_id): i for i in items}


@cache
def process_items() -> ProcessItems:
    """Decisions without a database: one store per process, so a restart forgets them with the
    assessments."""
    return ProcessItems()


def _now() -> datetime:
    return datetime.now(UTC)


def _entry(
    assessment: Assessment, item: DecidedItem, decision: Decision, reviewer: Reviewer
) -> AuditEntry:
    """The audit entry of the decision: the item as decided in its note, the requirement's
    verdict before and after (a decision doesn't change it) and the evidence it rests on."""
    requirement = next((r for r in assessment.requirements if r.id == item.requirement_id), None)
    verdict = requirement.verdict if requirement else None
    return AuditEntry(
        id=None,
        user_id=reviewer.id,
        reviewer_name=reviewer.name,
        at=item.decided_at,
        profile_id=assessment.profile_id,
        control_id=assessment.control_id,
        thread_id=assessment.thread_id,
        action=decision,
        requirement_id=item.requirement_id,
        note=f"{item.missing} Owner: {item.owner_role}. Due {item.due_date}.",
        verdict_before=verdict,
        verdict_after=verdict,
        evidence=evidence_versions([requirement] if requirement else []),
        model=assessment.model,
        prompt_version=assessment.prompt_version,
    )


def decide(
    items: RemediationItems,
    saver: BaseCheckpointSaver,
    thread_id: str,
    requirement_id: str,
    request: DecisionRequest,
    *,
    reviewer: Reviewer,
    profile_id: str,
    scope: str,
    clock: Callable[[], datetime] = _now,
    today: Callable[[], date] = date.today,
) -> DecidedItem:
    """The lead's decision on the assessment's item for the requirement, kept with its audit
    entry; the kept item. The same decision again returns the kept item, writing nothing.

    Raises UnknownAssessmentError (another user's or profile's thread too), NotAcceptedError,
    UnknownItemError, PastDueError (an approval due before today), DecidedAlreadyError, and
    DatabaseUnavailable (Postgres)."""
    assessment = load_assessment(thread_id, saver, scope=scope)
    if assessment.status != "accepted":
        raise NotAcceptedError(NOT_ACCEPTED)
    draft = next((i for i in assessment.remediation if i.requirement_id == requirement_id), None)
    if draft is None:
        raise UnknownItemError(NO_ITEM.format(requirement_id=requirement_id))
    due = request.due_date.isoformat() if request.due_date else draft.due_date
    if request.decision == "approve" and date.fromisoformat(due) < today():
        raise PastDueError(PAST_DUE.format(due=due))
    item = DecidedItem(
        thread_id=thread_id,
        requirement_id=requirement_id,
        status=DECIDED[request.decision],
        missing=draft.missing,
        owner_role=request.owner_role or draft.owner_role,
        due_date=due,
        decided_at=clock(),
    )
    kept, _ = items.decide(
        reviewer.id, profile_id, item, _entry(assessment, item, request.decision, reviewer)
    )
    if kept.status != item.status:
        raise DecidedAlreadyError(DECIDED_ALREADY.format(status=kept.status))
    return kept
