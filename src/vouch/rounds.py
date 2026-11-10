"""Review rounds (#402): the lead's work under a profile in rounds, each closed one frozen, so the
overview compares where the lead stands now with where they stood when the last round closed.

Rounds are kept per user and profile, in both goals. At most one is open. With none kept, the
current round is the first one, "First round", open since the start: it opens on its own, so
nothing is written (and no audit entry) until it is closed. Closing the open round freezes its
results, one FrozenControl per control of the profile as each control's newest assessment
stands then, and a closed round never changes again: a later assessment, review, remediation
decision or fresh start leaves it as it was. The lead then starts the next round by name.

Each start and close is an audit trail entry (`round-start`, `round-close`, with the round's
name), kept with it: both, or neither (Postgres: one transaction, vouch.database's
`start_round` and `close_round`; without a database, this process's memory and trail, whose
writes can't fail). Two at once take turns, so one goes through and the other is refused.

The comparison (`compare`) is of accepted verdicts only, as readiness is (GLOSSARY: Readiness):
now against the last round closed since the fresh start (a fresh start leaves the rounds as
they are, but a round closed before it is not compared with). It is never called progress.
"""

import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from functools import cache
from typing import Annotated, Any, Protocol, TypedDict

from pydantic import BaseModel, StringConstraints

from vouch.audit import AuditEntry, ProcessTrail, Reviewer, now, process_trail
from vouch.database import AuditAction, Database
from vouch.dossier import NOT_ASSESSED, VERDICTS, CardStatus, readiness
from vouch.graph import Assessment, Verdict
from vouch.isa import IsaControl

FIRST_ROUND = "First round"
"""The round open while none is kept: open since the start."""
NAME_LENGTH = 60
"""A round's name, trimmed, is 1 to NAME_LENGTH characters."""
ROUND_OPEN = "Close the current review round before starting the next."
NO_ROUND_OPEN = "No review round is open."


class RoundOpen(Exception):
    """A round is open (the first one too): close it before starting the next."""


class NoRoundOpen(Exception):
    """No round is open, so there is none to close."""


class RoundRequest(BaseModel):
    """The name of the round the lead starts."""

    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_LENGTH)
    ]


@dataclass(frozen=True)
class FrozenControl:
    """One control of the profile as it stood when its round closed: its card's review state,
    and its newest assessment's verdict and thread (None when not assessed)."""

    control_id: str
    status: CardStatus
    verdict: Verdict | None
    thread_id: str | None


@dataclass(frozen=True)
class Round:
    """A review round: open while `closed_at` is None; `opened_at` None for the first round.
    `results` are frozen at close (empty while open)."""

    name: str
    opened_at: datetime | None
    closed_at: datetime | None
    results: tuple[FrozenControl, ...] = ()


FIRST = Round(FIRST_ROUND, None, None)

CloseEntry = Callable[[str], AuditEntry]
"""A close's audit entry, from the closed round's name."""


class ReviewRounds(Protocol):
    """Where each user's review rounds under each profile are kept, each start and close with
    its audit entry."""

    def current(self, user_id: str, profile_id: str) -> Round | None:
        """The open round: FIRST while none is kept; None when the last one was closed."""
        ...

    def last_closed(self, user_id: str, profile_id: str, since: datetime | None) -> Round | None:
        """The last round closed at or after `since` (the fresh start; any when None)."""
        ...

    def start(
        self, user_id: str, profile_id: str, name: str, at: datetime, entry: AuditEntry
    ) -> Round:
        """Open a round named `name` at `at`, with `entry`, together. RoundOpen while one is."""
        ...

    def close(
        self,
        user_id: str,
        profile_id: str,
        at: datetime,
        results: Sequence[FrozenControl],
        entry: CloseEntry,
    ) -> Round:
        """Close the open round at `at` with its frozen `results` and `entry` of its name,
        together: the closed round. NoRoundOpen when none is."""
        ...


class ProcessRounds:
    """Rounds without a database: in this process, their entries in its audit trail
    (vouch.audit's, read at each write, as vouch.goal's ProcessGoals reads it)."""

    def __init__(self, trail: Callable[[], ProcessTrail] = process_trail) -> None:
        self._trail = trail
        self._rounds: dict[tuple[str, str], list[Round]] = {}
        self._guard = threading.Lock()

    def current(self, user_id: str, profile_id: str) -> Round | None:
        with self._guard:
            kept = self._rounds.get((user_id, profile_id), [])
        if not kept:
            return FIRST
        return kept[-1] if kept[-1].closed_at is None else None

    def last_closed(self, user_id: str, profile_id: str, since: datetime | None) -> Round | None:
        with self._guard:
            kept = list(self._rounds.get((user_id, profile_id), []))
        closed = [r for r in kept if r.closed_at is not None]
        if since is not None:
            closed = [r for r in closed if r.closed_at is not None and r.closed_at >= since]
        return closed[-1] if closed else None

    def start(
        self, user_id: str, profile_id: str, name: str, at: datetime, entry: AuditEntry
    ) -> Round:
        with self._guard:
            kept = self._rounds.get((user_id, profile_id), [])
            if not kept or kept[-1].closed_at is None:
                raise RoundOpen(ROUND_OPEN)
            started = Round(name, at, None)
            self._trail().keep([entry])
            kept.append(started)
        return started

    def close(
        self,
        user_id: str,
        profile_id: str,
        at: datetime,
        results: Sequence[FrozenControl],
        entry: CloseEntry,
    ) -> Round:
        with self._guard:
            kept = self._rounds.setdefault((user_id, profile_id), [])
            if kept and kept[-1].closed_at is not None:
                raise NoRoundOpen(NO_ROUND_OPEN)
            open_round = kept[-1] if kept else FIRST
            closed = replace(open_round, closed_at=at, results=tuple(results))
            # The entry first: when it can't be written, the open round stays as it was.
            self._trail().keep([entry(closed.name)])
            if kept:
                kept[-1] = closed
            else:
                kept.append(closed)
        return closed


class PostgresRounds:
    """Rounds in Postgres (vouch.database), each start and close in one transaction with its
    entry."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def current(self, user_id: str, profile_id: str) -> Round | None:
        kept, open_round, _ = self.db.review_rounds(user_id, profile_id, None)
        if not kept:
            return FIRST
        return _round(open_round) if open_round is not None else None

    def last_closed(self, user_id: str, profile_id: str, since: datetime | None) -> Round | None:
        _, _, closed = self.db.review_rounds(user_id, profile_id, since)
        return _round(closed) if closed is not None else None

    def start(
        self, user_id: str, profile_id: str, name: str, at: datetime, entry: AuditEntry
    ) -> Round:
        if not self.db.start_round(user_id, profile_id, name, at, _row(entry)):
            raise RoundOpen(ROUND_OPEN)
        return Round(name, at, None)

    def close(
        self,
        user_id: str,
        profile_id: str,
        at: datetime,
        results: Sequence[FrozenControl],
        entry: CloseEntry,
    ) -> Round:
        frozen = [asdict(f) for f in results]
        closed = self.db.close_round(
            user_id, profile_id, FIRST_ROUND, at, frozen, lambda n: _row(entry(n))
        )
        if closed is None:
            raise NoRoundOpen(NO_ROUND_OPEN)
        name, opened_at = closed
        return Round(name, opened_at, at, tuple(results))


def _row(entry: AuditEntry) -> dict[str, Any]:
    """An entry as a mapping of vouch.database's AUDIT_COLUMNS."""
    return {k: v for k, v in asdict(entry).items() if k != "id"}


def _round(row: Mapping[str, Any]) -> Round:
    results = tuple(FrozenControl(**f) for f in row["results"] or ())
    return Round(row["name"], row["opened_at"], row["closed_at"], results)


@cache
def process_rounds() -> ProcessRounds:
    """Rounds without a database: one store per process, so a restart forgets them with the
    assessments."""
    return ProcessRounds()


def round_entry(
    reviewer: Reviewer, profile_id: str, action: AuditAction, name: str, at: datetime
) -> AuditEntry:
    """The audit entry of a round started or closed (`action`): who, when and under which
    profile, and the round's name; no control, thread, requirement, note, verdict, evidence,
    model or prompt (as a goal change's)."""
    return AuditEntry(
        id=None,
        user_id=reviewer.id,
        reviewer_name=reviewer.name,
        at=at,
        profile_id=profile_id,
        control_id="",
        thread_id="",
        action=action,
        requirement_id=None,
        note="",
        verdict_before=None,
        verdict_after=None,
        evidence=(),
        model="",
        prompt_version="",
        round_name=name,
    )


def freeze(controls: Iterable[IsaControl], latest: Mapping[str, Assessment]) -> list[FrozenControl]:
    """One FrozenControl per listed control, from its newest assessment in `latest` (as
    vouch.memory's `newest_by_control` reads them): what a round's close keeps."""
    frozen = []
    for control in controls:
        assessment = latest.get(control.id)
        if assessment is None:
            frozen.append(FrozenControl(control.id, NOT_ASSESSED, None, None))
        else:
            frozen.append(
                FrozenControl(
                    control.id, assessment.status, assessment.verdict, assessment.thread_id
                )
            )
    return frozen


def start(
    rounds: ReviewRounds,
    name: str,
    *,
    reviewer: Reviewer,
    profile_id: str,
    clock: Callable[[], datetime] = now,
) -> Round:
    """The lead starts a round named `name` (trimmed already), with its audit entry, at
    `clock()`. Raises RoundOpen while one is open, and DatabaseUnavailable (Postgres)."""
    at = clock()
    entry = round_entry(reviewer, profile_id, "round-start", name, at)
    return rounds.start(reviewer.id, profile_id, name, at, entry)


def close(
    rounds: ReviewRounds,
    results: Sequence[FrozenControl],
    *,
    reviewer: Reviewer,
    profile_id: str,
    clock: Callable[[], datetime] = now,
) -> Round:
    """The lead closes the open round, its `results` frozen, with its audit entry, at
    `clock()`. Raises NoRoundOpen when none is, and DatabaseUnavailable (Postgres)."""
    at = clock()

    def entry(name: str) -> AuditEntry:
        return round_entry(reviewer, profile_id, "round-close", name, at)

    return rounds.close(reviewer.id, profile_id, at, results, entry)


class Count(TypedDict):
    now: int
    then: int


class Change(TypedDict):
    control_id: str
    before: Verdict | None
    after: Verdict | None
    reassessing: bool
    """Counted under its earlier accepted verdict while its newest assessment waits (#438)."""


class Comparison(TypedDict):
    """Now against a closed round: accepted verdicts only."""

    accepted: Count
    by_verdict: dict[Verdict, Count]
    changed: list[Change]


def compare(
    frozen: Sequence[FrozenControl],
    controls: Iterable[IsaControl],
    latest: Mapping[str, Assessment],
    earlier: Mapping[str, Assessment] | None = None,
) -> Comparison:
    """Now (the listed controls' newest assessments in `latest`) against a closed round's
    `frozen` results (then), counted the same way: accepted verdicts only. A control whose
    newest assessment waits for review while an earlier one was accepted (`earlier`: each
    control's newest accepted assessment before its newest, vouch.api's `_accepted_earlier`)
    counts now under that accepted verdict and is re-assessing (#438), rather than reading as a
    regression; otherwise now is the overview's readiness. `changed` lists, in the controls'
    order (the base framework's), each listed control whose accepted verdict differs (None when
    not accepted) and each one re-assessing; a control frozen but no longer listed is left out,
    of the counts too, and one new to the profile reads None then."""
    controls = list(controls)
    earlier = earlier or {}
    listed = {c.id for c in controls}
    reassessing = {
        c.id
        for c in controls
        if (a := latest.get(c.id)) is not None
        and a.status == "waiting-for-review"
        and (prior := earlier.get(c.id)) is not None
        and prior.status == "accepted"
    }
    counted = {**latest, **{i: earlier[i] for i in reassessing}}
    standing = readiness(controls, counted)
    then_accepted = {
        f.control_id: f.verdict for f in frozen if f.status == "accepted" and f.control_id in listed
    }
    then_verdicts = Counter(then_accepted.values())
    changed: list[Change] = []
    for control in controls:
        assessment = counted.get(control.id)
        after = assessment.verdict if assessment and assessment.status == "accepted" else None
        before = then_accepted.get(control.id)
        if before != after or control.id in reassessing:
            changed.append(
                {
                    "control_id": control.id,
                    "before": before,
                    "after": after,
                    "reassessing": control.id in reassessing,
                }
            )
    return {
        "accepted": {"now": standing["accepted"], "then": len(then_accepted)},
        "by_verdict": {
            v: {"now": standing["by_verdict"][v], "then": then_verdicts[v]} for v in VERDICTS
        },
        "changed": changed,
    }
