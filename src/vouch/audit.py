"""The audit trail (#39): the lead's every action, append-only, written with its decision.

One entry per action of the compliance lead: the review's accept or reject, each override in
it, and (#43) each remediation item approved or discarded. An entry says who, when, under which
profile, on which control (and requirement), the note, the verdict before and after, the
versions of the evidence documents it rests on, and the model and prompt version of the run,
read from the assessment's thread, never assumed.

A review is recorded or not at all (`review`). Its entries are built from the waiting
assessment and the decision before anything is written, and the trail keeps the two together:
with a database, the entries and the decision (LangGraph's checkpoint) go in one transaction on
one connection (vouch.database's `Database.reviewing`), so a failed entry rolls the decision
back; without one, the entries are kept in this process once the decision is written, which
can't fail.

The trail also keeps two reviews of one thread apart: the second is refused at once
(NotWaitingForReviewError, a 409) while the first holds the thread's lock.

Nothing offers an update or a delete: the table refuses both too (vouch-db-setup's trigger).
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver

from vouch.database import AuditAction, Database, ReviewInProgress
from vouch.graph import (
    WAITING,
    Assessment,
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


def _now() -> datetime:
    return datetime.now(UTC)


def _versions(requirements: list[RequirementVerdict]) -> tuple[EvidenceVersion, ...]:
    """Each document the requirements cite, at its version, once, sorted."""
    cited = {EvidenceVersion(c.doc_id, c.version) for r in requirements for c in r.citations}
    return tuple(sorted(cited))


def review_entries(
    waiting: Assessment, decision: ReviewDecision, reviewer: Reviewer, at: datetime
) -> list[AuditEntry]:
    """The entries the lead's decision on a waiting assessment makes: one per override, then
    the decision itself (so read newest first, the decision comes before its overrides). The
    control's evidence is that of the requirements its verdict rests on (graph.roll_up's).
    InvalidReviewError for an override graph.resume would refuse."""
    reviewed, overrides = apply_overrides(waiting.requirements, decision.overrides)

    def entry(**fields: Any) -> AuditEntry:
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

    by_id = {r.id: r for r in reviewed}
    entries = [
        entry(
            action="override",
            requirement_id=o.requirement_id,
            note=o.reason,
            verdict_before=o.agent_verdict,
            verdict_after=o.verdict,
            evidence=_versions([by_id[o.requirement_id]]),
        )
        for o in overrides
    ]
    entries.append(
        entry(
            action=decision.decision,
            requirement_id=None,
            note=decision.note,
            verdict_before=waiting.verdict,
            verdict_after=roll_up(reviewed),
            evidence=_versions([r for r in reviewed if counts_towards_roll_up(r)]),
        )
    )
    return entries


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


def _busy(key: str) -> NotWaitingForReviewError:
    return NotWaitingForReviewError(f"assessment {key} is being reviewed")


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
        self._reviewing: set[str] = set()

    @contextmanager
    def recording(self, key: str) -> Iterator[tuple[BaseCheckpointSaver, Record]]:
        with self._guard:
            if key in self._reviewing:
                raise _busy(key)
            self._reviewing.add(key)
        try:
            staged: list[AuditEntry] = []
            yield self._checkpointer(), staged.extend
            with self._guard:
                first = len(self._entries) + 1
                self._entries.extend(
                    AuditEntry(**(asdict(e) | {"id": first + n})) for n, e in enumerate(staged)
                )
        finally:
            with self._guard:
                self._reviewing.discard(key)

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


def review(
    trail: AuditTrail,
    thread_id: str,
    decision: ReviewDecision,
    *,
    reviewer: Reviewer,
    scope: str = "",
    clock: Callable[[], datetime] = _now,
) -> Assessment:
    """`resume` the thread with the lead's decision and record its entries, together: the
    entries are built first, and when they can't be built or written, the decision isn't
    written either. Raises what `resume` raises (NotWaitingForReviewError also while another
    review of the thread runs), and DatabaseUnavailable (Postgres)."""
    with trail.recording(thread_key(thread_id, scope)) as (saver, record):
        waiting = load_assessment(thread_id, saver, scope=scope)
        if waiting.status != WAITING:
            raise NotWaitingForReviewError(f"assessment {thread_id} is not waiting for review")
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
        )
