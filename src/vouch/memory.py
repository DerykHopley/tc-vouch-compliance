"""Long-term memory per user and compliance profile (#38).

LangGraph's store keeps, per user and profile, which assessments they started and when they
last worked. Each assessment is its own thread in the checkpointer, keyed under the same user
and profile (`Memory.scope`), so one user's thread id names nothing for another.

The store is only an index: the thread is the one record of where an assessment stands
(waiting for review, accepted, rejected), so the two can't disagree. An index entry whose run
never reached the review is left out of every list.

With a database (vouch.database), both live in Postgres and outlast a restart; without one, in
this process's memory.
"""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from vouch.graph import (
    WAITING,
    Assessment,
    UnknownAssessmentError,
    load_assessment,
    memory_checkpointer,
)

ASSESSMENTS = "assessments"
SESSION = "session"
MAX_ASSESSMENTS = 1000
"""The most assessments one user lists under one profile: about 80 runs of each control."""


@cache
def memory_saver() -> BaseCheckpointSaver:
    """Where assessments wait for review without a database: one saver per process, so a
    restart forgets them."""
    return memory_checkpointer()


@cache
def memory_store() -> BaseStore:
    """Long-term memory without a database: one store per process."""
    return InMemoryStore()


def _now() -> datetime:
    return datetime.now(UTC)


class Memory:
    """One user's memory under one compliance profile."""

    def __init__(
        self,
        store: BaseStore,
        user_id: str,
        profile_id: str,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.store = store
        self.user_id = user_id
        self.profile_id = profile_id
        self._clock = clock

    @property
    def scope(self) -> str:
        """What the checkpointer's thread key starts with (vouch.graph's `scope`): the user and
        the profile, written so no two pairs give the same text."""
        return json.dumps([self.user_id, self.profile_id])

    def _namespace(self, *rest: str) -> tuple[str, ...]:
        return ("vouch", self.user_id, self.profile_id, *rest)

    def remember_assessment(self, thread_id: str, control_id: str) -> None:
        """Note a new assessment of `control_id` on `thread_id`, before its run starts, and the
        session's time. Raises DatabaseUnavailable (Postgres)."""
        now = self._clock().isoformat()
        self.store.put(
            self._namespace(ASSESSMENTS), thread_id, {"control_id": control_id, "started_at": now}
        )
        self.note_session()

    def note_session(self) -> None:
        """Note that the user worked under this profile now."""
        self.store.put(self._namespace(), SESSION, {"at": self._clock().isoformat()})

    def last_session(self) -> datetime | None:
        """When the user last worked under this profile; None if never."""
        item = self.store.get(self._namespace(), SESSION)
        return datetime.fromisoformat(item.value["at"]) if item else None

    def thread_ids(self) -> list[str]:
        """The assessments' thread ids, newest first."""
        items = self.store.search(self._namespace(ASSESSMENTS), limit=MAX_ASSESSMENTS)
        items.sort(key=lambda item: item.value["started_at"], reverse=True)
        return [item.key for item in items]

    def assessments(self, saver: BaseCheckpointSaver) -> list[Assessment]:
        """The user's assessments under this profile, newest first; a run that never reached
        the review is left out."""
        found = []
        for thread_id in self.thread_ids():
            try:
                found.append(load_assessment(thread_id, saver, scope=self.scope))
            except UnknownAssessmentError:
                continue
        return found


@dataclass(frozen=True)
class Progress:
    """Where a user stands under a profile: each assessed control's latest assessment, and when
    they last worked."""

    controls: dict[str, Assessment]
    last_session: datetime | None

    def counts(self) -> dict[str, int]:
        """How many controls' latest assessments are accepted, rejected, or waiting."""
        counted = Counter(a.status for a in self.controls.values())
        return {status: counted[status] for status in ("accepted", "rejected", WAITING)}


def progress(memory: Memory, saver: BaseCheckpointSaver) -> Progress:
    """The user's progress under the memory's profile, read from the store and the threads."""
    latest: dict[str, Assessment] = {}
    for assessment in memory.assessments(saver):  # newest first: the first of each control
        latest.setdefault(assessment.control_id, assessment)
    return Progress(controls=latest, last_session=memory.last_session())
