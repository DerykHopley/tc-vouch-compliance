"""The audit trail (#39): the lead's every action, append-only, written with its decision.

One entry per action of the compliance lead: the review's accept or reject, each override in
it, and (#43) each remediation item approved or discarded. An entry says who, when, under which
profile, on which control (and requirement), the note, the verdict before and after, the
versions of the evidence documents it rests on, and the model and prompt version of the run,
read from the assessment's thread, never assumed.

A review is recorded or not at all (`review`): with a database, the decision (LangGraph's
checkpoint) and its entries are written in one transaction on one connection
(vouch.database's `Database.reviewing`), so a failed entry rolls the decision back. Without one,
the trail is a list in this process, appended once the decision is made: the append can't fail.

Nothing offers an update or a delete: the table refuses both too (vouch-db-setup's trigger).
"""

import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from functools import cache
from typing import Any, Literal, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver

from vouch.database import Database
from vouch.graph import Assessment, ReviewDecision, Verdict, resume, roll_up, state_serializer
from vouch.memory import process_checkpointer

Action = Literal["accept", "reject", "override", "approve", "discard"]


@dataclass(frozen=True)
class EvidenceVersion:
    """One evidence document, at the version an entry's verdicts rest on."""

    doc_id: str
    version: str


@dataclass(frozen=True)
class AuditEntry:
    """One action of the compliance lead. `requirement_id` is None for the control's own."""

    user_id: str
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

    def __post_init__(self) -> None:
        # Read back from a store, the evidence is a list of mappings; keep it a tuple of these.
        versions = tuple(
            e if isinstance(e, EvidenceVersion) else EvidenceVersion(**e) for e in self.evidence
        )
        object.__setattr__(self, "evidence", versions)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["at"] = self.at.isoformat()
        return data


def _now() -> datetime:
    return datetime.now(UTC)


def _versions(citations: Iterable[Any]) -> tuple[EvidenceVersion, ...]:
    """Each cited document's version once, sorted."""
    return tuple(sorted({EvidenceVersion(c.doc_id, c.version) for c in citations}, key=_key))


def _key(version: EvidenceVersion) -> tuple[str, str]:
    return version.doc_id, version.version


def review_entries(reviewed: Assessment, user_id: str, at: datetime) -> list[AuditEntry]:
    """A reviewed assessment's entries: one per override, then the decision itself (so read
    newest first, the decision comes before its overrides)."""
    review = reviewed.review
    if review is None:
        raise ValueError(f"assessment {reviewed.thread_id} has no review to record")

    def entry(**fields: Any) -> AuditEntry:
        return AuditEntry(
            user_id=user_id,
            at=at,
            profile_id=reviewed.profile_id,
            control_id=reviewed.control_id,
            thread_id=reviewed.thread_id,
            model=reviewed.model,
            prompt_version=reviewed.prompt_version,
            **fields,
        )

    by_id = {r.id: r for r in reviewed.requirements}
    entries = [
        entry(
            action="override",
            requirement_id=o.requirement_id,
            note=o.reason,
            verdict_before=o.agent_verdict,
            verdict_after=o.verdict,
            evidence=_versions(by_id[o.requirement_id].citations),
        )
        for o in review.overrides
    ]
    agents = {o.requirement_id: o.agent_verdict for o in review.overrides}
    proposed = [replace(r, verdict=agents.get(r.id, r.verdict)) for r in reviewed.requirements]
    entries.append(
        entry(
            action=review.decision,
            requirement_id=None,
            note=review.note,
            verdict_before=roll_up(proposed),
            verdict_after=reviewed.verdict,
            evidence=_versions(c for r in reviewed.requirements for c in r.citations),
        )
    )
    return entries


Record = Callable[[list[AuditEntry]], None]


class AuditTrail(Protocol):
    """Where entries are kept, and how a decision is written with them."""

    def recording(
        self, lock_key: str
    ) -> AbstractContextManager[tuple[BaseCheckpointSaver, Record]]:
        """A checkpointer to decide on, and a way to record entries: both kept, or neither.
        `lock_key` names the thread decided on: two recordings of one thread run one after the
        other."""
        ...

    def entries(self, user_id: str, profile_id: str) -> list[AuditEntry]:
        """The user's entries under the profile, newest first."""
        ...


class MemoryTrail:
    """The trail without a database: a list in this process, decided on the process's
    checkpointer (vouch.memory's, read at each review, as the API's other routes read it)."""

    def __init__(
        self, checkpointer: Callable[[], BaseCheckpointSaver] = process_checkpointer
    ) -> None:
        self._checkpointer = checkpointer
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()

    @contextmanager
    def recording(self, lock_key: str) -> Iterator[tuple[BaseCheckpointSaver, Record]]:
        # The review lock (vouch.graph) orders two reviews of one thread; the saver writes at once.
        yield self._checkpointer(), self._append

    def _append(self, entries: list[AuditEntry]) -> None:
        with self._lock:
            self._entries.extend(entries)

    def entries(self, user_id: str, profile_id: str) -> list[AuditEntry]:
        with self._lock:
            mine = [e for e in self._entries if (e.user_id, e.profile_id) == (user_id, profile_id)]
        return mine[::-1]


class PostgresTrail:
    """The trail in Postgres (vouch.database): a decision and its entries in one transaction."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @contextmanager
    def recording(self, lock_key: str) -> Iterator[tuple[BaseCheckpointSaver, Record]]:
        with self.db.reviewing(lock_key, state_serializer()) as (saver, append):

            def record(entries: list[AuditEntry]) -> None:
                append([asdict(entry) for entry in entries])

            yield saver, record

    def entries(self, user_id: str, profile_id: str) -> list[AuditEntry]:
        return [AuditEntry(**row) for row in self.db.audit_entries(user_id, profile_id)]


@cache
def process_trail() -> MemoryTrail:
    """The trail without a database: one per process, so a restart forgets it with the
    decisions."""
    return MemoryTrail()


def review(
    trail: AuditTrail,
    thread_id: str,
    decision: ReviewDecision,
    *,
    user_id: str,
    scope: str = "",
    clock: Callable[[], datetime] = _now,
) -> Assessment:
    """`resume` the thread with the lead's decision and record its entries, together: when the
    entries can't be written, the decision isn't either. Raises what `resume` raises, and
    DatabaseUnavailable (Postgres)."""
    with trail.recording(f"{scope}{thread_id}") as (saver, record):
        reviewed = resume(thread_id, decision, saver, scope=scope)
        record(review_entries(reviewed, user_id, clock()))
    return reviewed
