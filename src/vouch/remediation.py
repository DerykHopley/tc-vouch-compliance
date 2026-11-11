"""Remediation items the compliance lead approves or discards (#43).

The assessment graph drafts one item per requirement not or partly evidenced (vouch.graph's
RemediationItem), kept in the assessment's thread. Once the findings are confirmed (the review's
Plan fixes step, #372), the lead approves each draft (maybe with another owner role or due date)
or discards it; the control is accepted only once none is left a draft. The
decision is kept per user and compliance profile, keyed by the assessment's thread and the
requirement, with its audit entry: both, or neither (Postgres: one transaction,
vouch.database's `decide_remediation`; without a database, this process's memory and trail,
whose writes can't fail).

A decision is final: the same decision again changes nothing and writes no entry (a double
click approves once); the other one, or an approval with other values, is refused, naming what
was kept. Only items whose findings are confirmed are decided, as an override at Confirm
findings adds or removes items; a rejected assessment's wait, as it is assessed again. An
assessment accepted before the three-step review can still have drafts: they are decided too.

The lead picks a due date in their own calendar, which the server doesn't know: a date is
refused only when it is past everywhere (before today in UTC-12), or further away than the
graph's horizon from the latest calendar day current anywhere (today in UTC+14).
"""

import threading
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from functools import cache
from typing import Annotated, Any, Literal, NamedTuple, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, StringConstraints

from vouch.audit import AuditEntry, ProcessTrail, Reviewer, evidence_versions, now, process_trail
from vouch.database import Database, Priority, RemediationWrites
from vouch.graph import (
    MAX_DUE_DAYS,
    NO_NUL_PATTERN,
    WAITING,
    Assessment,
    RemediationItem,
    load_assessment,
    state_serializer,
    thread_key,
)
from vouch.language import decided_note, placeholder, says_no_owner

ItemStatus = Literal["draft", "approved", "discarded"]
Decision = Literal["approve", "discard"]
DECIDED: dict[Decision, ItemStatus] = {"approve": "approved", "discard": "discarded"}
MAX_OWNER_ROLE = 200
EARLIEST_OFFSET = timedelta(hours=-12)  # UTC-12: the last place a calendar day is still current
LATEST_OFFSET = timedelta(hours=14)  # UTC+14: the first place a calendar day begins

PAST_DUE = "The due date {due} has passed. Choose today or a later date."
TOO_FAR = "The due date {due} is more than {days} days away. Choose an earlier date."
OWNER_NEEDED = "Vouch suggested no owner for this item. Name the role that owns the fix."
NOT_CONFIRMED = "Remediation items are approved or discarded once the findings are confirmed."
REJECTED = "This control's verdict was rejected, so its remediation items wait: assess it again."
NO_ITEM = "This assessment has no remediation item for requirement {requirement_id}."
DECIDED_ALREADY = "This remediation item was {status} already."
APPROVED_WITH_OTHERS = (
    "This remediation item was approved already, with owner {owner_role} and due {due_date}."
)


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
class Mark:
    """The lead's marks on an approved item in the improvement plan (#410, vouch.plan): the
    priority they chose (None: the default), and when it was marked done, with the note (None:
    not done)."""

    priority: Priority | None
    done_at: datetime | None
    done_note: str | None


@dataclass(frozen=True)
class DecidedItem:
    """A remediation item as the lead decided it."""

    thread_id: str
    requirement_id: str
    status: ItemStatus
    missing: str
    owner_role: str
    due_date: date
    decided_at: datetime


Key = tuple[str, str]
"""An item's thread id and requirement id, under one user and profile."""


class ItemKey(NamedTuple):
    """Where one decision is kept: the user, the profile, the thread and the requirement."""

    user_id: str
    profile_id: str
    thread_id: str
    requirement_id: str


class DueDateError(ValueError):
    """An approval's due date is past everywhere, or beyond the horizon (a 422)."""


class OwnerNeededError(ValueError):
    """An approval of an item Vouch suggested no owner for names none (a 422)."""


class NotDecidableError(RuntimeError):
    """The assessment's findings aren't confirmed, or it was rejected, so its items wait (a
    409)."""


class UnknownItemError(LookupError):
    """The assessment has no item for that requirement (a 404)."""


class DecidedAlreadyError(RuntimeError):
    """The item was decided already, the other way or with other values (a 409)."""


def default_priority(assessment: Assessment, requirement_id: str) -> Priority:
    """An item's priority until the lead sets one (#410): high for a requirement not evidenced,
    medium for one partly evidenced, by its verdict as the assessment holds it (after Confirm
    findings, its overrides applied)."""
    requirement = next((r for r in assessment.requirements if r.id == requirement_id), None)
    return (
        "high" if requirement is not None and requirement.verdict == "not-evidenced" else "medium"
    )


def item_data(
    item: RemediationItem | DecidedItem,
    status: ItemStatus,
    priority: Priority | None,
    mark: Mark | None = None,
    *,
    language: str,
) -> dict[str, Any]:
    """An item as the API shows it: with its priority (None for a discarded one) and, once an
    approved one is marked done, `done` (when and the lead's note; else None). Its "not drafted"
    in its assessment's answer `language` (vouch.language.placeholder, #535), as every route
    serves it; its owner role stays the NO_OWNER sentinel the frontend compares with, and the
    stored item stays English."""
    due = item.due_date
    done = None
    if status == "approved" and mark is not None and mark.done_at is not None:
        done = {"at": mark.done_at.isoformat(), "note": mark.done_note}
    return {
        "requirement_id": item.requirement_id,
        "missing": placeholder(item.missing, language),
        "owner_role": item.owner_role,
        "due_date": due.isoformat() if isinstance(due, date) else due,
        "status": status,
        "priority": None if status == "discarded" else priority,
        "done": done,
    }


def shown_items(
    assessment: Assessment,
    decided: Mapping[Key, DecidedItem],
    marks: Mapping[Key, Mark] | None = None,
) -> list[dict[str, Any]]:
    """The assessment's remediation items as the API shows them: each draft, or as decided, with
    its priority (the lead's, else `default_priority`) and done mark (`item_data`), in both goals
    (#410)."""
    shown = []
    for item in assessment.remediation:
        key = (assessment.thread_id, item.requirement_id)
        kept = decided.get(key)
        mark = (marks or {}).get(key) if kept is not None else None
        priority = (mark.priority if mark else None) or default_priority(
            assessment, item.requirement_id
        )
        language = assessment.answer_language
        shown.append(
            item_data(kept, kept.status, priority, mark, language=language)
            if kept
            else item_data(item, "draft", priority, language=language)
        )
    return shown


ReviewStep = Literal["confirm-findings", "plan-fixes", "accept"]
"""The step of the review a waiting assessment is at (#372)."""


def drafts_left(assessment: Assessment, decided: Mapping[Key, DecidedItem]) -> list[str]:
    """The requirements whose remediation items are still drafts, in the assessment's order."""
    return [
        i.requirement_id
        for i in assessment.remediation
        if (assessment.thread_id, i.requirement_id) not in decided
    ]


def review_step(assessment: Assessment, decided: Mapping[Key, DecidedItem]) -> ReviewStep | None:
    """Where the assessment's review stands: Confirm findings until they are confirmed, then
    Plan fixes while any item is a draft, then Accept; None once reviewed."""
    if assessment.status != WAITING:
        return None
    if assessment.confirmed is None:
        return "confirm-findings"
    return "plan-fixes" if drafts_left(assessment, decided) else "accept"


def check_due(due: date, at: datetime) -> None:
    """DueDateError for a due date past everywhere at `at` (before the date in UTC-12), or more
    than MAX_DUE_DAYS after the latest date current anywhere (the date in UTC+14)."""
    if due < (at + EARLIEST_OFFSET).date():
        raise DueDateError(PAST_DUE.format(due=due.isoformat()))
    if due > (at + LATEST_OFFSET).date() + timedelta(days=MAX_DUE_DAYS):
        raise DueDateError(TOO_FAR.format(due=due.isoformat(), days=MAX_DUE_DAYS))


DecideWrite = Callable[[str, str, DecidedItem, AuditEntry], tuple[DecidedItem, bool]]


class Deciding(NamedTuple):
    """One decision, under its thread's lock (`RemediationItems.deciding`)."""

    saver: BaseCheckpointSaver
    """Where the assessment is read, as the review's steps left it."""
    kept: Callable[[ItemKey], "DecidedItem | None"]
    """The item decided already under the key, if any."""
    decide: DecideWrite
    """Keep the item and its entry together (user id, profile id, item, entry), unless the item
    was decided already: then nothing is written. (The kept item, whether this call decided
    it.)"""


class RemediationItems(Protocol):
    """Where the lead's decisions are kept, each with its audit entry."""

    def deciding(self, key: str) -> AbstractContextManager[Deciding]:
        """A decision on the thread `key` (graph.thread_key): the thread's review lock held for
        the block, waiting while a step of the review holds it (#372), so no Reject or Accept
        lands between the assessment read and the decision. Its reads and writes on one
        connection (Postgres: one transaction)."""
        ...

    def decided(
        self, user_id: str, profile_id: str, thread_ids: Collection[str]
    ) -> dict[Key, DecidedItem]:
        """The user's decided items of those threads under the profile (one read)."""
        ...


class ProcessItems:
    """Decisions without a database: in this process, their entries in its audit trail
    (vouch.audit's, read at each decision, as the trail's own checkpointer is)."""

    def __init__(self, trail: Callable[[], ProcessTrail] = process_trail) -> None:
        self._trail = trail
        self._items: dict[ItemKey, DecidedItem] = {}
        self._guard = threading.Lock()

    def forget(self, profile_id: str) -> None:
        """Reset demo (#605): every user's decisions under the profile gone."""
        with self._guard:
            self._items = {k: v for k, v in self._items.items() if k.profile_id != profile_id}

    @contextmanager
    def deciding(self, key: str) -> Iterator[Deciding]:
        with self._trail().holding(key, wait=True) as saver:
            yield Deciding(saver, self._kept, self._decide)

    def _kept(self, key: ItemKey) -> DecidedItem | None:
        with self._guard:
            return self._items.get(key)

    def _decide(
        self, user_id: str, profile_id: str, item: DecidedItem, entry: AuditEntry
    ) -> tuple[DecidedItem, bool]:
        key = ItemKey(user_id, profile_id, item.thread_id, item.requirement_id)
        with self._guard:
            kept = self._items.get(key)
            if kept is not None:
                return kept, False
            self._items[key] = item
            self._trail().keep([entry])
        return item, True

    def _mine(self, user_id: str, profile_id: str, thread_ids: Iterable[str]) -> list[DecidedItem]:
        threads = set(thread_ids)
        with self._guard:
            return [
                item
                for key, item in self._items.items()
                if (key.user_id, key.profile_id) == (user_id, profile_id)
                and key.thread_id in threads
            ]

    def decided(
        self, user_id: str, profile_id: str, thread_ids: Collection[str]
    ) -> dict[Key, DecidedItem]:
        mine = self._mine(user_id, profile_id, thread_ids)
        return {(i.thread_id, i.requirement_id): i for i in mine}


def decided_from_row(row: Mapping[str, Any]) -> DecidedItem:
    return DecidedItem(
        thread_id=row["thread_id"],
        requirement_id=row["requirement_id"],
        status=row["status"],
        missing=row["missing"],
        owner_role=row["owner_role"],
        due_date=row["due_date"],
        decided_at=row["decided_at"],
    )


class PostgresItems:
    """Decisions in Postgres (vouch.database), each in one transaction with its entry."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @contextmanager
    def deciding(self, key: str) -> Iterator[Deciding]:
        with self.db.deciding(key, state_serializer()) as (saver, writes):

            def kept(item_key: ItemKey) -> DecidedItem | None:
                row = writes.kept(item_key._asdict())
                return decided_from_row(row) if row is not None else None

            yield Deciding(saver, kept, _writing(writes))

    def decided(
        self, user_id: str, profile_id: str, thread_ids: Collection[str]
    ) -> dict[Key, DecidedItem]:
        if not thread_ids:
            return {}
        rows = self.db.remediation_items(user_id, profile_id, list(thread_ids))
        items = [decided_from_row(row) for row in rows]
        return {(i.thread_id, i.requirement_id): i for i in items}


def _writing(writes: RemediationWrites) -> DecideWrite:
    def decide(
        user_id: str, profile_id: str, item: DecidedItem, entry: AuditEntry
    ) -> tuple[DecidedItem, bool]:
        row = asdict(item) | {"user_id": user_id, "profile_id": profile_id}
        audit_row = {k: v for k, v in asdict(entry).items() if k != "id"}
        kept, new = writes.decide(row, [audit_row])
        return decided_from_row(kept), new

    return decide


@cache
def process_items() -> ProcessItems:
    """Decisions without a database: one store per process, so a restart forgets them with the
    assessments."""
    return ProcessItems()


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
        note=decided_note(item.missing, item.owner_role, item.due_date),
        verdict_before=verdict,
        verdict_after=verdict,
        evidence=evidence_versions([requirement] if requirement else []),
        model=assessment.model,
        prompt_version=assessment.prompt_version,
    )


def _as_kept(kept: DecidedItem, request: DecisionRequest) -> DecidedItem:
    """The kept item, when the request asks for what it already is; else DecidedAlreadyError,
    naming what was kept, so the lead's other values aren't silently dropped."""
    if kept.status != DECIDED[request.decision]:
        raise DecidedAlreadyError(DECIDED_ALREADY.format(status=kept.status))
    if request.decision == "approve" and (
        request.owner_role not in (None, kept.owner_role)
        or request.due_date not in (None, kept.due_date)
    ):
        due = kept.due_date.isoformat()
        raise DecidedAlreadyError(
            APPROVED_WITH_OTHERS.format(owner_role=kept.owner_role, due_date=due)
        )
    return kept


def decide(
    items: RemediationItems,
    thread_id: str,
    requirement_id: str,
    request: DecisionRequest,
    *,
    reviewer: Reviewer,
    profile_id: str,
    scope: str,
    clock: Callable[[], datetime] | None = None,
) -> DecidedItem:
    """The lead's decision on the assessment's item for the requirement, kept with its audit
    entry; the kept item. A decision made already is looked up first: the same one again
    returns it, writing nothing, whatever the date. All of it under the thread's review lock
    (`RemediationItems.deciding`), so a Reject or Accept can't land between the read and the
    decision.

    Raises UnknownAssessmentError (another user's or profile's thread too), NotDecidableError,
    UnknownItemError, DecidedAlreadyError, OwnerNeededError and DueDateError (an approval), and
    DatabaseUnavailable (Postgres). `clock` defaults to vouch.audit's `now`."""
    with items.deciding(thread_key(thread_id, scope)) as deciding:
        return _decide(
            deciding, thread_id, requirement_id, request, reviewer, profile_id, scope, clock
        )


def _decide(
    deciding: Deciding,
    thread_id: str,
    requirement_id: str,
    request: DecisionRequest,
    reviewer: Reviewer,
    profile_id: str,
    scope: str,
    clock: Callable[[], datetime] | None,
) -> DecidedItem:
    """`decide`, under the thread's lock."""
    assessment = load_assessment(thread_id, deciding.saver, scope=scope)
    if assessment.status == "rejected":
        raise NotDecidableError(REJECTED)
    if assessment.status == WAITING and assessment.confirmed is None:
        raise NotDecidableError(NOT_CONFIRMED)
    draft = next((i for i in assessment.remediation if i.requirement_id == requirement_id), None)
    if draft is None:
        raise UnknownItemError(NO_ITEM.format(requirement_id=requirement_id))
    kept = deciding.kept(ItemKey(reviewer.id, profile_id, thread_id, requirement_id))
    if kept is not None:
        return _as_kept(kept, request)
    at = (clock or now)()
    owner = request.owner_role or draft.owner_role
    due = request.due_date or date.fromisoformat(draft.due_date)
    if request.decision == "approve":
        if says_no_owner(owner):  # in any answer language's words (#535)
            raise OwnerNeededError(OWNER_NEEDED)
        check_due(due, at)
    item = DecidedItem(
        thread_id=thread_id,
        requirement_id=requirement_id,
        status=DECIDED[request.decision],
        missing=draft.missing,
        owner_role=owner,
        due_date=due,
        decided_at=at,
    )
    kept, new = deciding.decide(
        reviewer.id, profile_id, item, _entry(assessment, item, request.decision, reviewer)
    )
    return kept if new else _as_kept(kept, request)
