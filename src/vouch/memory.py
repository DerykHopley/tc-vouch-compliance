"""Long-term memory per user and compliance profile (#38).

LangGraph's store keeps, per user and profile, which assessments they started (an index) and
when they last reviewed one. Each assessment is its own thread in the checkpointer, keyed under
the same user and profile (`Memory.scope`), so one user's thread id names nothing for another.

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
from typing import NamedTuple, get_args
from urllib.parse import quote

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from vouch.graph import Assessment, Status, assessment_loader, memory_checkpointer

ASSESSMENTS = "assessments"
LAST_REVIEW = "last-review"
CHAT_LINES = "chat"
"""The chat's kept conversations (#251): each turn's lines, as the chat showed them."""
CURRENT_CHAT = "current-chat"
"""The conversation the chat opens on, under this user and profile (#251)."""
MAX_ASSESSMENTS = 1000
"""The most assessments one user lists under one profile (about 80 runs of each control): the
newest are kept."""
PAGE_SIZE = 200
"""How many index entries one read of the store takes."""


@cache
def process_checkpointer() -> BaseCheckpointSaver:
    """Where assessments wait for review without a database: one saver per process, so a
    restart forgets them."""
    return memory_checkpointer()


@cache
def process_store() -> BaseStore:
    """Long-term memory without a database: one store per process."""
    return InMemoryStore()


def _now() -> datetime:
    return datetime.now(UTC)


def _label(value: str) -> str:
    """`value` as a store namespace label: LangGraph refuses a label with a period, so it's
    percent-encoded, periods too. Different values stay different."""
    return quote(value, safe="").replace(".", "%2E")


class Entry(NamedTuple):
    """One assessment in a user's index."""

    thread_id: str
    control_id: str
    started_at: datetime


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
        return ("vouch", _label(self.user_id), _label(self.profile_id), *rest)

    def remember_assessment(self, thread_id: str, control_id: str) -> None:
        """Note a new assessment of `control_id` on `thread_id`, before its run starts: one
        write. Raises DatabaseUnavailable (Postgres)."""
        value = {"control_id": control_id, "started_at": self._clock().isoformat()}
        self.store.put(self._namespace(ASSESSMENTS), thread_id, value)

    def forget_assessment(self, thread_id: str) -> None:
        """Remove `thread_id` from the index: its run never started."""
        self.store.delete(self._namespace(ASSESSMENTS), thread_id)

    def current_chat(self) -> str | None:
        """The chat thread the user's chat opens on under this profile (#251); None for none.
        Raises DatabaseUnavailable (Postgres)."""
        item = self.store.get(self._namespace(), CURRENT_CHAT)
        return item.value.get("thread_id") if item is not None else None

    def set_current_chat(self, thread_id: str | None) -> None:
        """Make `thread_id` the conversation the chat opens on; None: a new conversation, the
        old one kept. Raises DatabaseUnavailable (Postgres)."""
        if thread_id is None:
            self.store.delete(self._namespace(), CURRENT_CHAT)
        else:
            self.store.put(self._namespace(), CURRENT_CHAT, {"thread_id": thread_id})

    def add_chat_turn(self, thread_id: str, lines: list[dict], started_ns: int) -> None:
        """Keep one chat turn's lines on `thread_id`. Its key is when the turn began, so turns
        read back in the order they were asked, even when one ends before another that began
        first. Raises DatabaseUnavailable (Postgres)."""
        key = f"{started_ns:020d}"
        self.store.put(self._namespace(CHAT_LINES, thread_id), key, {"lines": lines})

    def chat_lines(self, thread_id: str) -> list[dict]:
        """`thread_id`'s kept lines, every turn in order (#251); none for a thread of another
        user or profile, as the namespace is this user's and profile's. Raises
        DatabaseUnavailable (Postgres)."""
        turns = sorted(self._all(self._namespace(CHAT_LINES, thread_id)), key=lambda t: t.key)
        return [line for turn in turns for line in turn.value["lines"]]

    def _all(self, namespace: tuple[str, ...]) -> list:
        """Every item under `namespace`, read PAGE_SIZE at a time."""
        items: list = []
        while page := self.store.search(namespace, limit=PAGE_SIZE, offset=len(items)):
            items += page
            if len(page) < PAGE_SIZE:
                break
        return items

    def note_worked(self) -> None:
        """Note that the user reviewed an assessment under this profile now."""
        self.store.put(self._namespace(), LAST_REVIEW, {"at": self._clock().isoformat()})

    def last_worked(self) -> datetime | None:
        """When the user last started or reviewed an assessment under this profile; None if
        never."""
        entries = self.entries()
        review = self.store.get(self._namespace(), LAST_REVIEW)
        times = [entries[0].started_at] if entries else []
        if review is not None:
            times.append(datetime.fromisoformat(review.value["at"]))
        return max(times, default=None)

    def entries(self) -> list[Entry]:
        """The index, newest first, at most MAX_ASSESSMENTS. Every page is read first: the
        stores return entries in different orders (Postgres's by last write, memory's by first)."""
        items = self._all(self._namespace(ASSESSMENTS))
        entries = [
            Entry(
                item.key,
                item.value["control_id"],
                datetime.fromisoformat(item.value["started_at"]),
            )
            for item in items
        ]
        entries.sort(key=lambda entry: entry.started_at, reverse=True)
        return entries[:MAX_ASSESSMENTS]

    def thread_ids(self) -> list[str]:
        """The assessments' thread ids, newest first."""
        return [entry.thread_id for entry in self.entries()]

    def assessments(self, saver: BaseCheckpointSaver) -> list[Assessment]:
        """The user's assessments under this profile, newest first; a run that never reached
        the review is left out."""
        load = assessment_loader(saver, scope=self.scope)
        loaded = (load(entry.thread_id) for entry in self.entries())
        return [assessment for assessment in loaded if assessment is not None]


STATUSES: tuple[Status, ...] = get_args(Status)


@dataclass(frozen=True)
class Progress:
    """Where a user stands under a profile: each assessed control's latest assessment, and when
    they last worked. Unlike readiness, it counts proposed and rejected verdicts too."""

    controls: dict[str, Assessment]
    last_worked: datetime | None

    def counts(self) -> dict[str, int]:
        """How many controls' latest assessments are in each status."""
        counted = Counter(a.status for a in self.controls.values())
        return {status: counted[status] for status in STATUSES}


def newest_by_control(memory: Memory, saver: BaseCheckpointSaver) -> dict[str, Assessment]:
    """Each assessed control's newest assessment under the memory's profile, by control id. From
    the index, newest first, only each control's newest is loaded (an older one only when the
    newest never reached the review)."""
    load = assessment_loader(saver, scope=memory.scope)
    latest: dict[str, Assessment] = {}
    for entry in memory.entries():
        if entry.control_id in latest:
            continue
        assessment = load(entry.thread_id)
        if assessment is not None:
            latest[entry.control_id] = assessment
    return latest


def progress(memory: Memory, saver: BaseCheckpointSaver) -> Progress:
    """The user's progress under the memory's profile: `newest_by_control`, and when they last
    worked."""
    return Progress(controls=newest_by_control(memory, saver), last_worked=memory.last_worked())


def newest_assessment(
    memory: Memory, saver: BaseCheckpointSaver, control_id: str
) -> Assessment | None:
    """The control's newest assessment under the memory's profile, from the index: only that
    control's runs are loaded, newest first, and one that never reached the review (a new run,
    say) is passed over. None when it has none."""
    load = assessment_loader(saver, scope=memory.scope)
    for entry in memory.entries():
        if entry.control_id == control_id and (assessment := load(entry.thread_id)) is not None:
            return assessment
    return None


def rejection_note(memory: Memory, saver: BaseCheckpointSaver, control_id: str) -> str:
    """The lead's note on the control's newest assessment when it was rejected, else nothing:
    what a new run of a rejected control carries to the model however it was started (#42)."""
    assessment = newest_assessment(memory, saver, control_id)
    if assessment is None or assessment.status != "rejected" or assessment.review is None:
        return ""
    return assessment.review.note
