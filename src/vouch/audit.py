"""The audit trail (#39): the lead's every action, append-only, written with its decision.

One entry per action of the compliance lead: the findings confirmed and each override with
them (the review's first step, #372), the review's accept or reject, and each remediation item
approved or discarded (vouch.remediation, #43), and each change of the profile's Goal
(vouch.goal, #369, with the goal before and after), and each review round started or closed
(vouch.rounds, #402, with the round's name), and each evidence document added (`add_document`,
#585, with the document's id and version as its evidence). An entry says
who, when, under which profile, on which control (and requirement), the note, the verdict
before and after, the versions of the evidence documents it rests on, and the model and prompt
version of the run, read from the assessment's thread, never assumed.

Each step of a review is recorded or not at all (`confirm_findings`, `review`). Its entries are
built from the waiting assessment and the step before anything is written, and the trail keeps the two together:
with a database, the entries and the decision (LangGraph's checkpoint) go in one transaction on
one connection (vouch.database's `Database.reviewing`), so a failed entry rolls the decision
back; without one, the entries are kept in this process once the decision is written, which
can't fail.

The trail also keeps two steps of one thread's review apart: the second is refused at once
(NotWaitingForReviewError, a 409) while the first holds the thread's lock.

Nothing offers an update or a delete: the table refuses both too (vouch-db-setup's trigger).
"""

import logging
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any, Literal, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver

from vouch import graph
from vouch.added_documents import AddedDocument, AddedDocuments, NewDocument
from vouch.database import AuditAction, Database, Goal, ReviewInProgress
from vouch.graph import (
    NO_DRAFTS,
    WAITING,
    Assessment,
    ConfirmFindings,
    NotWaitingForReviewError,
    RequirementVerdict,
    ReviewDecision,
    Verdict,
    apply_overrides,
    counts_towards_roll_up,
    load_assessment,
    resume,
    roll_up,
    state_serializer,
    thread_key,
)
from vouch.memory import process_checkpointer

Action = AuditAction
DEFAULT_PAGE = 100
MAX_PAGE = 500
"""How many entries one read of the trail returns, unless asked for fewer; at most."""

log = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class EvidenceVersion:
    """One evidence document, at the version an entry's verdicts rest on."""

    doc_id: str
    version: str


@dataclass(frozen=True)
class AuditEntry:
    """One action of the compliance lead. `requirement_id` is None for the control's own; `id`
    is the trail's, once the entry is kept (newer entries don't always have higher ones)."""

    id: int | None
    user_id: str
    reviewer_name: str
    """The reviewer's display name as it was at review time (#154): history keeps it."""
    at: datetime
    profile_id: str
    control_id: str
    thread_id: str
    action: Action
    requirement_id: str | None
    note: str
    verdict_before: Verdict | None
    verdict_after: Verdict | None
    evidence: tuple[EvidenceVersion, ...]
    model: str
    prompt_version: str
    goal_before: Goal | None = None
    goal_after: Goal | None = None
    """A change of the profile's Goal (`goal`, #369): the goal before and after; None on every
    other entry."""
    round_name: str | None = None
    """A review round started or closed (`round-start`, `round-close`, #402): the round's name;
    None on every other entry."""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["at"] = self.at.isoformat()
        return data


@dataclass(frozen=True)
class Reviewer:
    """Who acts on the trail: their user id, and their display name as it is now (#154)."""

    id: str
    name: str


@dataclass(frozen=True)
class Page:
    """Entries newest first, and the `before` that reads on after them (None: no more)."""

    entries: list[AuditEntry]
    next: int | None


def now() -> datetime:
    """The time an action is recorded at, in UTC: the trail's and vouch.remediation's clock."""
    return datetime.now(UTC)


def evidence_versions(requirements: list[RequirementVerdict]) -> tuple[EvidenceVersion, ...]:
    """Each document the requirements cite, at its version, once, sorted."""
    cited = {EvidenceVersion(c.doc_id, c.version) for r in requirements for c in r.citations}
    return tuple(sorted(cited))


def _entry(waiting: Assessment, reviewer: Reviewer, at: datetime, **fields: Any) -> AuditEntry:
    """An entry on the waiting assessment's control, by the reviewer, at `at`."""
    return AuditEntry(
        id=None,
        user_id=reviewer.id,
        reviewer_name=reviewer.name,
        at=at,
        profile_id=waiting.profile_id,
        control_id=waiting.control_id,
        thread_id=waiting.thread_id,
        model=waiting.model,
        prompt_version=waiting.prompt_version,
        **fields,
    )


def _control_evidence(requirements: list[RequirementVerdict]) -> tuple[EvidenceVersion, ...]:
    """The control's evidence: that of the requirements its verdict rests on (graph.roll_up's)."""
    return evidence_versions([r for r in requirements if counts_towards_roll_up(r)])


def confirm_entries(
    waiting: Assessment, findings: ConfirmFindings, reviewer: Reviewer, at: datetime
) -> list[AuditEntry]:
    """The entries the lead's Confirm findings makes (#372): one per override, then `confirm`
    for the control, its verdict before Vouch's roll-up and after the roll-up with the
    overrides (so read newest first, the confirm comes before its overrides).
    InvalidReviewError for an override graph.confirm_findings would refuse."""
    reviewed, overrides = apply_overrides(waiting.requirements, findings.overrides)
    by_id = {r.id: r for r in reviewed}
    entries = [
        _entry(
            waiting,
            reviewer,
            at,
            action="override",
            requirement_id=o.requirement_id,
            note=o.reason,
            verdict_before=o.agent_verdict,
            verdict_after=o.verdict,
            evidence=evidence_versions([by_id[o.requirement_id]]),
        )
        for o in overrides
    ]
    entries.append(
        _entry(
            waiting,
            reviewer,
            at,
            action="confirm",
            requirement_id=None,
            note="",
            verdict_before=waiting.verdict,
            verdict_after=roll_up(reviewed),
            evidence=_control_evidence(reviewed),
        )
    )
    return entries


def review_entries(
    waiting: Assessment, decision: ReviewDecision, reviewer: Reviewer, at: datetime
) -> list[AuditEntry]:
    """The entry the lead's decision on a waiting assessment makes: `accept` or `reject` for the
    control, with the note. The decision changes no verdict: before and after are the control's
    verdict as it waits (the confirmed roll-up, once the findings are confirmed). Its evidence
    is that of the requirements the verdict rests on (graph.roll_up's)."""
    return [
        _entry(
            waiting,
            reviewer,
            at,
            action=decision.decision,
            requirement_id=None,
            note=decision.note,
            verdict_before=waiting.verdict,
            verdict_after=waiting.verdict,
            evidence=_control_evidence(waiting.requirements),
        )
    ]


Record = Callable[[list[AuditEntry]], None]


class AuditTrail(Protocol):
    """Where entries are kept, and how a decision is written with them."""

    def recording(self, key: str) -> AbstractContextManager[tuple[BaseCheckpointSaver, Record]]:
        """A checkpointer to decide on, and a way to record entries: both kept when the block
        ends, or neither when it raises. `key` is the thread's (graph.thread_key): while one
        recording holds it, another raises NotWaitingForReviewError at once."""
        ...

    def entries(
        self, user_id: str, profile_id: str, *, limit: int, before: int | None = None
    ) -> list[AuditEntry]:
        """At most `limit` of the user's entries under the profile, newest first, after the
        entry with id `before` when given."""
        ...


BEING_REVIEWED = "This control is being reviewed in another tab; try again in a moment."
"""The 409 a lead sees while another review of the same assessment holds it (#396): plain, with
nothing internal (the thread key names the user, the profile and the thread)."""


def _busy(key: str) -> NotWaitingForReviewError:
    log.info("a review of %s was refused: another holds it", key)
    return NotWaitingForReviewError(BEING_REVIEWED)


class ProcessTrail:
    """The trail without a database: a list in this process, decided on the process's
    checkpointer (vouch.memory's, read at each review, as the API's other routes read it).
    Entries are kept once the block ends: the decision is written by then, and a list append
    can't fail."""

    def __init__(
        self, checkpointer: Callable[[], BaseCheckpointSaver] = process_checkpointer
    ) -> None:
        self._checkpointer = checkpointer
        self._entries: list[AuditEntry] = []
        self._guard = threading.Lock()
        self._released = threading.Condition(self._guard)
        self._held: set[str] = set()

    @contextmanager
    def holding(self, key: str, *, wait: bool = False) -> Iterator[BaseCheckpointSaver]:
        """The thread's lock (`key`, graph.thread_key) held for the block, and the checkpointer
        to read and write the thread on. A step of the review is refused at once while another
        holds it (`wait` False: NotWaitingForReviewError); a remediation decision waits for it
        (vouch.remediation, #372)."""
        with self._guard:
            if key in self._held and not wait:
                raise _busy(key)
            self._released.wait_for(lambda: key not in self._held)
            self._held.add(key)
        try:
            yield self._checkpointer()
        finally:
            with self._guard:
                self._held.discard(key)
                self._released.notify_all()

    @contextmanager
    def recording(self, key: str) -> Iterator[tuple[BaseCheckpointSaver, Record]]:
        with self.holding(key) as saver:
            staged: list[AuditEntry] = []
            yield saver, staged.extend
            self.keep(staged)

    def keep(self, entries: list[AuditEntry]) -> None:
        """Append entries, each given the trail's next id: a list append, which can't fail."""
        with self._guard:
            first = len(self._entries) + 1
            self._entries.extend(
                AuditEntry(**(asdict(e) | {"id": first + n})) for n, e in enumerate(entries)
            )

    def entries(
        self, user_id: str, profile_id: str, *, limit: int, before: int | None = None
    ) -> list[AuditEntry]:
        with self._guard:
            kept = self._entries if before is None else self._entries[: max(before - 1, 0)]
            mine = [e for e in kept if (e.user_id, e.profile_id) == (user_id, profile_id)]
        return mine[::-1][:limit]


class PostgresTrail:
    """The trail in Postgres (vouch.database): a decision and its entries in one transaction."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @contextmanager
    def recording(self, key: str) -> Iterator[tuple[BaseCheckpointSaver, Record]]:
        try:
            with self.db.reviewing(key, state_serializer()) as (saver, append):

                def record(entries: list[AuditEntry]) -> None:
                    rows = [asdict(entry) for entry in entries]
                    append([{k: v for k, v in row.items() if k != "id"} for row in rows])

                yield saver, record
        except ReviewInProgress:
            raise _busy(key) from None

    def entries(
        self, user_id: str, profile_id: str, *, limit: int, before: int | None = None
    ) -> list[AuditEntry]:
        rows = self.db.audit_entries(user_id, profile_id, limit=limit, before=before)
        return [
            AuditEntry(**(row | {"evidence": tuple(EvidenceVersion(**e) for e in row["evidence"])}))
            for row in rows
        ]


def keep_row(row: Mapping[str, Any]) -> None:
    """Keep an entry given as a mapping of vouch.database's AUDIT_COLUMNS in the process's trail
    (the process store of added documents keeps its adds' entries so)."""
    evidence = tuple(EvidenceVersion(**e) for e in row["evidence"])
    process_trail().keep([AuditEntry(**({**row, "id": None, "evidence": evidence}))])


@cache
def process_trail() -> ProcessTrail:
    """The trail without a database: one per process, so a restart forgets it with the
    decisions."""
    return ProcessTrail()


def read(
    trail: AuditTrail,
    user_id: str,
    profile_id: str,
    *,
    limit: int = DEFAULT_PAGE,
    before: int | None = None,
) -> Page:
    """A page of the user's entries under the profile, newest first."""
    entries = trail.entries(user_id, profile_id, limit=limit + 1, before=before)
    more = len(entries) > limit
    entries = entries[:limit]
    return Page(entries, entries[-1].id if more else None)


def read_all(trail: AuditTrail, user_id: str, profile_id: str) -> list[AuditEntry]:
    """Every one of the user's entries under the profile, newest first: `read`'s pages, each
    as large as one may be, until there are no more (#57's export holds the whole trail)."""
    entries: list[AuditEntry] = []
    before: int | None = None
    while True:
        page = read(trail, user_id, profile_id, limit=MAX_PAGE, before=before)
        entries += page.entries
        if page.next is None:
            return entries
        before = page.next


def _waiting_assessment(saver: BaseCheckpointSaver, thread_id: str, scope: str) -> Assessment:
    """The thread's assessment, waiting for review, which a step's entries are built from;
    NotWaitingForReviewError when reviewed. Which step it waits at is graph's to check
    (`confirm_findings`, `resume`): a step refused there writes no entry either."""
    waiting = load_assessment(thread_id, saver, scope=scope)
    if waiting.status != WAITING:
        raise NotWaitingForReviewError(f"assessment {thread_id} is not waiting for review")
    return waiting


def confirm_findings(
    trail: AuditTrail,
    thread_id: str,
    findings: ConfirmFindings,
    *,
    reviewer: Reviewer,
    scope: str = "",
    clock: Callable[[], datetime] = now,
) -> Assessment:
    """The review's first step (#372): `graph.confirm_findings` on the thread with the lead's
    findings, and its entries recorded, together, as `review` does. Raises what
    graph.confirm_findings raises (WrongStepError for findings confirmed already;
    NotWaitingForReviewError also while another step of the thread's review runs), and
    DatabaseUnavailable (Postgres)."""
    with trail.recording(thread_key(thread_id, scope)) as (saver, record):
        waiting = _waiting_assessment(saver, thread_id, scope)
        record(confirm_entries(waiting, findings, reviewer, clock()))
        return graph.confirm_findings(thread_id, findings, saver, scope=scope)


def review(
    trail: AuditTrail,
    thread_id: str,
    decision: ReviewDecision,
    *,
    reviewer: Reviewer,
    scope: str = "",
    clock: Callable[[], datetime] = now,
    drafts: Callable[[Assessment], Sequence[str]] = NO_DRAFTS,
) -> Assessment:
    """`resume` the thread with the lead's decision and record its entry, together: the entry
    is built first, and when it can't be built or written, or `resume` refuses the step, the
    decision isn't written either. `drafts` is `resume`'s: the requirements whose remediation
    items are still drafts, which hold an Accept back (vouch.remediation keeps the decisions).
    Raises what `resume` raises (NotWaitingForReviewError also while another step of the
    thread's review runs), and DatabaseUnavailable (Postgres)."""
    with trail.recording(thread_key(thread_id, scope)) as (saver, record):
        waiting = _waiting_assessment(saver, thread_id, scope)
        at = clock()
        record(review_entries(waiting, decision, reviewer, at))
        return resume(
            thread_id,
            decision,
            saver,
            scope=scope,
            reviewer_id=reviewer.id,
            reviewer_name=reviewer.name,
            reviewed_at=at,
            drafts=drafts,
        )


def document_entry(
    reviewer: Reviewer,
    added: AddedDocument,
    note: str,
    at: datetime,
    action: Literal["document-add", "document-withdraw"] = "document-add",
) -> AuditEntry:
    """The audit entry of a document added (#585) or withdrawn (#586, its newest version):
    who, when and under which profile, the document's id and version as its evidence, and the
    note; no control, thread, verdict, model or prompt."""
    return AuditEntry(
        id=None,
        user_id=reviewer.id,
        reviewer_name=reviewer.name,
        at=at,
        profile_id=added.profile_id,
        control_id="",
        thread_id="",
        action=action,
        requirement_id=None,
        note=note,
        verdict_before=None,
        verdict_after=None,
        evidence=(EvidenceVersion(added.doc_id, str(added.version)),),
        model="",
        prompt_version="",
    )


def add_document(
    store: AddedDocuments,
    new: NewDocument,
    *,
    reviewer: Reviewer,
    profile_id: str,
    builtin_ids: set[str],
    note: str = "",
    clock: Callable[[], datetime] = now,
) -> AddedDocument:
    """The lead adds a document (or its next version) under the profile, with its
    `document-add` entry, together, at `clock()`: the version kept. Raises DocumentRefused
    (nothing written) and DatabaseUnavailable (Postgres)."""
    at = clock()

    def entry(added: AddedDocument) -> dict[str, Any]:
        row = asdict(document_entry(reviewer, added, note, at))
        return {k: v for k, v in row.items() if k != "id"}

    return store.add(profile_id, new, builtin_ids, reviewer.id, reviewer.name, at, entry)


def withdraw_document(
    store: AddedDocuments,
    doc_id: str,
    *,
    reviewer: Reviewer,
    profile_id: str,
    clock: Callable[[], datetime] = now,
) -> list[AddedDocument]:
    """The lead withdraws an added document of the profile (#586): every version marked
    withdrawn at `clock()`, with one `document-withdraw` entry naming its newest version,
    together; none written when it was withdrawn already. Its versions as kept now, none for an
    unknown id. Raises DatabaseUnavailable (Postgres)."""
    at = clock()

    def entry(newest: AddedDocument) -> dict[str, Any]:
        row = asdict(document_entry(reviewer, newest, "", at, "document-withdraw"))
        return {k: v for k, v in row.items() if k != "id"}

    return store.withdraw(profile_id, doc_id, at, entry)
