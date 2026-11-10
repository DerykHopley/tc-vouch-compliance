"""The improvement plan (#410, spec #350 stories 9 and 10), in the Internal improvement goal only.

Each remediation item has a priority: the lead's, else the default from its requirement's verdict
(vouch.remediation's `default_priority`). The lead changes an approved item's priority, and marks
an approved item of an accepted control done with a note, which is final. Once every approved
item of a control's newest assessment is done, a re-assessment is suggested; the control is
Closed once a later assessment of it is accepted with no requirement not or partly evidenced,
after the last done mark (`card_flags`). Closed changes no review status: it is an accepted
control, counted as one.

The marks are kept per user and profile beside the decisions, whose table stays final: in
Postgres, table vouch_remediation_plan, each mark in one transaction with its audit entry
(`priority`, `done`) under the thread's lock, as a decision is (vouch.database's `planning`);
without a database, this process's memory and trail, whose writes can't fail. The same priority
or the same note again writes nothing.

Under Audit readiness the routes refuse (409) and the cards carry neither flag; the items' new
fields are sent in both goals, so switching back shows the kept marks again.
"""

import threading
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Annotated, Any, Literal, NamedTuple, Protocol, get_args

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, StringConstraints

from vouch.audit import AuditEntry, ProcessTrail, Reviewer, evidence_versions, now, process_trail
from vouch.database import AuditAction, Database, Goal, PlanWrites, Priority, RemediationWrites
from vouch.graph import NO_NUL_PATTERN, Assessment, load_assessment, state_serializer, thread_key
from vouch.remediation import (
    NO_ITEM,
    DecidedItem,
    ItemKey,
    Key,
    Mark,
    ProcessItems,
    UnknownItemError,
    decided_from_row,
    default_priority,
    item_data,
    process_items,
)

IMPROVEMENT: Goal = "internal-improvement"
MAX_NOTE = 2000
"""A done mark's note, trimmed, is 1 to MAX_NOTE characters."""

NOT_IMPROVEMENT = "The improvement plan is part of the Internal improvement goal."
APPROVE_FIRST = "Approve this remediation item first."
DONE_ALREADY = "This remediation item is done already."
DONE_WITH = "This remediation item was marked done already: {note}"
ACCEPT_FIRST = "Accept the control before marking its remediation items done."
REJECTED = "This control's verdict was rejected, so its remediation items wait: assess it again."


class NotInImprovementGoal(RuntimeError):
    """The profile's goal is Audit readiness, which has no plan (a 409)."""


class NotPlannable(RuntimeError):
    """The item or its assessment can't take this mark now (a 409): not approved, done
    already, or its assessment rejected (a priority) or not accepted (a done mark)."""


class PriorityRequest(BaseModel):
    """The priority the lead sets on an approved item."""

    priority: Priority


class DoneRequest(BaseModel):
    """What the lead says was done, marking an approved item done."""

    note: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_NOTE, pattern=NO_NUL_PATTERN
        ),
    ]


MarkWrite = Callable[[ItemKey, str, Mark, AuditEntry], None]


class Marking(NamedTuple):
    """One mark, under its thread's lock (`RemediationPlan.marking`)."""

    saver: BaseCheckpointSaver
    """Where the assessment is read, as the review's steps left it."""
    decided: Callable[[ItemKey], DecidedItem | None]
    """The lead's decision on the item, if any."""
    kept: Callable[[ItemKey], Mark | None]
    """The marks kept on the item, if any."""
    write: MarkWrite
    """Keep the item's marks (key, control id, marks) with the entry, together."""


class RemediationPlan(Protocol):
    """Where the lead's plan marks are kept, each with its audit entry."""

    def marking(self, key: str) -> AbstractContextManager[Marking]:
        """A mark on the thread `key` (graph.thread_key): the thread's lock held for the block,
        waiting while a step of the review or a decision holds it, so two marks at once take
        turns. Its reads and writes on one connection (Postgres: one transaction)."""
        ...

    def marks(self, user_id: str, profile_id: str, thread_ids: Collection[str]) -> dict[Key, Mark]:
        """The user's marks on those threads' items under the profile (one read)."""
        ...


class ProcessPlan:
    """Marks without a database: in this process, their entries in its audit trail, the
    decisions read from this process's (vouch.remediation's ProcessItems)."""

    def __init__(
        self,
        items: Callable[[], ProcessItems] = process_items,
        trail: Callable[[], ProcessTrail] = process_trail,
    ) -> None:
        self._items = items
        self._trail = trail
        self._marks: dict[ItemKey, Mark] = {}
        self._guard = threading.Lock()

    @contextmanager
    def marking(self, key: str) -> Iterator[Marking]:
        with self._trail().holding(key, wait=True) as saver:
            yield Marking(saver, self._decided, self._kept, self._write)

    def _decided(self, key: ItemKey) -> DecidedItem | None:
        mine = self._items().decided(key.user_id, key.profile_id, [key.thread_id])
        return mine.get((key.thread_id, key.requirement_id))

    def _kept(self, key: ItemKey) -> Mark | None:
        with self._guard:
            return self._marks.get(key)

    def _write(self, key: ItemKey, control_id: str, mark: Mark, entry: AuditEntry) -> None:
        with self._guard:
            self._marks[key] = mark
            self._trail().keep([entry])

    def marks(self, user_id: str, profile_id: str, thread_ids: Collection[str]) -> dict[Key, Mark]:
        threads = set(thread_ids)
        with self._guard:
            return {
                (k.thread_id, k.requirement_id): m
                for k, m in self._marks.items()
                if (k.user_id, k.profile_id) == (user_id, profile_id) and k.thread_id in threads
            }


def _mark_from_row(row: Mapping[str, Any]) -> Mark:
    return Mark(priority=row["priority"], done_at=row["done_at"], done_note=row["done_note"])


class PostgresPlan:
    """Marks in Postgres (vouch.database), each in one transaction with its entry."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @contextmanager
    def marking(self, key: str) -> Iterator[Marking]:
        with self.db.planning(key, state_serializer()) as (saver, decisions, writes):
            yield Marking(saver, _decided(decisions), _kept(writes), _writing(writes))

    def marks(self, user_id: str, profile_id: str, thread_ids: Collection[str]) -> dict[Key, Mark]:
        if not thread_ids:
            return {}
        rows = self.db.plan_marks(user_id, profile_id, list(thread_ids))
        return {(r["thread_id"], r["requirement_id"]): _mark_from_row(r) for r in rows}


def _decided(decisions: RemediationWrites) -> Callable[[ItemKey], DecidedItem | None]:
    def decided(key: ItemKey) -> DecidedItem | None:
        row = decisions.kept(key._asdict())
        return decided_from_row(row) if row is not None else None

    return decided


def _kept(writes: PlanWrites) -> Callable[[ItemKey], Mark | None]:
    def kept(key: ItemKey) -> Mark | None:
        row = writes.kept(key._asdict())
        return _mark_from_row(row) if row is not None else None

    return kept


def _writing(writes: PlanWrites) -> MarkWrite:
    def write(key: ItemKey, control_id: str, mark: Mark, entry: AuditEntry) -> None:
        row = key._asdict() | {"control_id": control_id} | asdict(mark)
        writes.mark(row, [{k: v for k, v in asdict(entry).items() if k != "id"}])

    return write


@cache
def process_plan() -> ProcessPlan:
    """Marks without a database: one store per process, so a restart forgets them with the
    assessments."""
    return ProcessPlan()


def _entry(
    assessment: Assessment,
    requirement_id: str,
    action: AuditAction,
    note: str,
    reviewer: Reviewer,
    at: datetime,
) -> AuditEntry:
    """The audit entry of a mark: the requirement's verdict before and after (a mark doesn't
    change it) and the evidence it rests on, as a decision's (vouch.remediation)."""
    requirement = next((r for r in assessment.requirements if r.id == requirement_id), None)
    verdict = requirement.verdict if requirement else None
    return AuditEntry(
        id=None,
        user_id=reviewer.id,
        reviewer_name=reviewer.name,
        at=at,
        profile_id=assessment.profile_id,
        control_id=assessment.control_id,
        thread_id=assessment.thread_id,
        action=action,
        requirement_id=requirement_id,
        note=note,
        verdict_before=verdict,
        verdict_after=verdict,
        evidence=evidence_versions([requirement] if requirement else []),
        model=assessment.model,
        prompt_version=assessment.prompt_version,
    )


class _Item(NamedTuple):
    assessment: Assessment
    key: ItemKey
    decided: DecidedItem | None
    mark: Mark | None


def _read(
    marking: Marking, thread_id: str, requirement_id: str, user_id: str, profile_id: str, scope: str
) -> _Item:
    assessment = load_assessment(thread_id, marking.saver, scope=scope)
    if not any(i.requirement_id == requirement_id for i in assessment.remediation):
        raise UnknownItemError(NO_ITEM.format(requirement_id=requirement_id))
    key = ItemKey(user_id, profile_id, thread_id, requirement_id)
    return _Item(assessment, key, marking.decided(key), marking.kept(key))


def _shown(item: _Item, mark: Mark | None) -> dict[str, Any]:
    assert item.decided is not None
    priority = (mark.priority if mark else None) or default_priority(
        item.assessment, item.key.requirement_id
    )
    return item_data(item.decided, item.decided.status, priority, mark)


def set_priority(
    plan: RemediationPlan,
    thread_id: str,
    requirement_id: str,
    priority: Priority,
    *,
    goal: Goal,
    reviewer: Reviewer,
    profile_id: str,
    scope: str,
    clock: Callable[[], datetime] = now,
) -> dict[str, Any]:
    """The lead sets an approved, not done item's priority, with its `priority` audit entry,
    together; the item as the API shows it. The priority it has already writes nothing.

    Raises NotInImprovementGoal, UnknownAssessmentError (another user's or profile's thread
    too), UnknownItemError, NotPlannable (a rejected assessment, an item not approved or done
    already) and DatabaseUnavailable (Postgres)."""
    if goal != IMPROVEMENT:
        raise NotInImprovementGoal(NOT_IMPROVEMENT)
    with plan.marking(thread_key(thread_id, scope)) as marking:
        item = _read(marking, thread_id, requirement_id, reviewer.id, profile_id, scope)
        if item.assessment.status == "rejected":
            raise NotPlannable(REJECTED)
        if item.decided is None or item.decided.status != "approved":
            raise NotPlannable(APPROVE_FIRST)
        kept = item.mark or Mark(priority=None, done_at=None, done_note=None)
        if kept.done_at is not None:
            raise NotPlannable(DONE_ALREADY)
        before = kept.priority or default_priority(item.assessment, requirement_id)
        if priority == before:
            return _shown(item, item.mark)
        mark = Mark(priority=priority, done_at=None, done_note=None)
        note = f"Priority: {priority} (was {before})."
        entry = _entry(item.assessment, requirement_id, "priority", note, reviewer, clock())
        marking.write(item.key, item.assessment.control_id, mark, entry)
        return _shown(item, mark)


def mark_done(
    plan: RemediationPlan,
    thread_id: str,
    requirement_id: str,
    note: str,
    *,
    goal: Goal,
    reviewer: Reviewer,
    profile_id: str,
    scope: str,
    clock: Callable[[], datetime] = now,
) -> dict[str, Any]:
    """The lead marks an approved item of an accepted control done, with what was done (`note`,
    trimmed already) and its `done` audit entry, together; the item as the API shows it. Done
    is final: the same note again writes nothing, another is refused naming the kept one.

    Raises NotInImprovementGoal, UnknownAssessmentError (another user's or profile's thread
    too), UnknownItemError, NotPlannable (an assessment not accepted, an item not approved or
    done with another note) and DatabaseUnavailable (Postgres)."""
    if goal != IMPROVEMENT:
        raise NotInImprovementGoal(NOT_IMPROVEMENT)
    with plan.marking(thread_key(thread_id, scope)) as marking:
        item = _read(marking, thread_id, requirement_id, reviewer.id, profile_id, scope)
        if item.assessment.status != "accepted":
            raise NotPlannable(ACCEPT_FIRST)
        if item.decided is None or item.decided.status != "approved":
            raise NotPlannable(APPROVE_FIRST)
        kept = item.mark or Mark(priority=None, done_at=None, done_note=None)
        if kept.done_at is not None:
            if kept.done_note == note:
                return _shown(item, kept)
            raise NotPlannable(DONE_WITH.format(note=kept.done_note))
        at = clock()
        mark = Mark(priority=kept.priority, done_at=at, done_note=note)
        entry = _entry(item.assessment, requirement_id, "done", note, reviewer, at)
        marking.write(item.key, item.assessment.control_id, mark, entry)
        return _shown(item, mark)


ReassessReason = Literal["fixes-done", "question-answered", "question-overdue"]
"""Why a re-assessment of a control is suggested: its fixes are all done (#410, Internal
improvement only), or an open question on it was answered or is overdue (#414, vouch.questions,
in both goals). Listed in this order."""
REASSESS_REASONS: tuple[ReassessReason, ...] = get_args(ReassessReason)


@dataclass(frozen=True)
class CardFlags:
    """What the improvement plan (#410) and the open questions (#414) add to a control's card."""

    reassess_suggested: bool
    """A re-assessment is suggested: true exactly when `reasons` isn't empty."""
    closed: bool
    """The first assessment accepted after the last done mark of the control's latest accepted
    plan has no requirement not or partly evidenced."""
    reasons: tuple[ReassessReason, ...] = ()
    """Why the re-assessment is suggested, in REASSESS_REASONS' order, each at most once.
    `fixes-done`: every approved item of the control's latest accepted plan is done, and no
    assessment of it was accepted after the last done mark."""

    def __post_init__(self) -> None:
        if self.reassess_suggested != bool(self.reasons):
            raise ValueError("a re-assessment is suggested exactly when it has a reason")


def _approved(assessment: Assessment, decided: Mapping[Key, DecidedItem]) -> list[Key]:
    keys = [(assessment.thread_id, i.requirement_id) for i in assessment.remediation]
    return [k for k in keys if (d := decided.get(k)) is not None and d.status == "approved"]


def _last_done(keys: Iterable[Key], marks: Mapping[Key, Mark]) -> datetime | None:
    """When the last of the items was marked done; None while any isn't (or there are none)."""
    times: list[datetime] = []
    for key in keys:
        mark = marks.get(key)
        if mark is None or mark.done_at is None:
            return None
        times.append(mark.done_at)
    return max(times, default=None)


def accepted_at(assessment: Assessment) -> datetime | None:
    """When an accepted assessment was accepted; None for any other, or one with no time kept."""
    review = assessment.review
    if assessment.status != "accepted" or review is None or not review.reviewed_at:
        return None
    at = datetime.fromisoformat(review.reviewed_at)
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


def _has_gap(assessment: Assessment) -> bool:
    return any(r.verdict in ("not-evidenced", "partly-evidenced") for r in assessment.requirements)


def _flags(
    runs: Sequence[Assessment], decided: Mapping[Key, DecidedItem], marks: Mapping[Key, Mark]
) -> CardFlags | None:
    """One control's flags from its assessments, oldest first: the latest accepted plan whose
    approved items are all done, then the first assessment accepted after its last done mark
    decides. None accepted since: a re-assessment is suggested (a rejected run keeps the
    suggestion), hidden while the newest run waits for review, which is the next step then; one with no gap: Closed; one with a gap: neither (its items start
    the plan again). A later accepted plan with an approved item not done is the open one: it
    resets the earlier done plan, so a Closed control reopens (#420's review)."""
    done_by: datetime | None = None
    for run in runs:
        if accepted_at(run) is None:
            continue
        approved = _approved(run, decided)
        last = _last_done(approved, marks)
        if last is not None:
            done_by = last
        elif approved:  # approved items, not all done: this plan is open again
            done_by = None
    if done_by is None:
        return None
    after = next((r for r in runs if (at := accepted_at(r)) is not None and at > done_by), None)
    if after is None:
        if runs[-1].status == "waiting-for-review":
            return None
        return CardFlags(reassess_suggested=True, closed=False, reasons=("fixes-done",))
    return None if _has_gap(after) else CardFlags(reassess_suggested=False, closed=True)


def card_flags(
    history: Sequence[Assessment],
    decided: Mapping[Key, DecidedItem],
    marks: Mapping[Key, Mark],
    goal: Goal,
) -> dict[str, CardFlags]:
    """Each control's flags, from its assessments listed since the fresh start (`history`,
    newest first, vouch.memory's `assessments`), the user's decisions and marks on them; only
    controls with a flag set are named, and none under Audit readiness."""
    if goal != IMPROVEMENT:
        return {}
    runs: dict[str, list[Assessment]] = {}
    for assessment in reversed(history):  # oldest first
        runs.setdefault(assessment.control_id, []).append(assessment)
    flags = {control_id: _flags(mine, decided, marks) for control_id, mine in runs.items()}
    return {control_id: f for control_id, f in flags.items() if f is not None}


def with_reasons(
    flags: Mapping[str, CardFlags], more: Mapping[str, Iterable[ReassessReason]]
) -> dict[str, CardFlags]:
    """The cards' flags with `more` reasons per control (the open questions', #414) added to
    those they have, in REASSESS_REASONS' order, each once; only controls with a flag set are
    named."""
    merged: dict[str, CardFlags] = {}
    for control_id in {*flags, *more}:
        kept = flags.get(control_id, CardFlags(reassess_suggested=False, closed=False))
        reasons = {*kept.reasons, *more.get(control_id, ())}
        ordered = tuple(r for r in REASSESS_REASONS if r in reasons)
        if ordered or kept.closed:
            merged[control_id] = CardFlags(bool(ordered), kept.closed, ordered)
    return merged
