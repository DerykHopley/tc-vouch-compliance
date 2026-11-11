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

The round comparison (#571, `round_comparison`; GLOSSARY: Round comparison) is of any closed
round the lead chooses from the listing (`listed`, `number_rounds`: each round numbered by its
position from 1 in the order opened, as no id is kept on them), control by control: better,
worse, new or unchanged, and the scope changes since, with readiness then and now, and the
remediation items done and open questions answered since it closed (`done_since`,
`answered_since`).
"""

import threading
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from functools import cache
from typing import Annotated, Any, Literal, Protocol, TypedDict

from pydantic import BaseModel, StringConstraints

from vouch.audit import AuditEntry, ProcessTrail, Reviewer, now, process_trail
from vouch.database import AuditAction, Database
from vouch.dossier import NOT_ASSESSED, VERDICTS, CardStatus, readiness
from vouch.graph import Assessment, Verdict
from vouch.isa import IsaControl
from vouch.questions import OpenQuestion
from vouch.remediation import Mark

FIRST_ROUND = "First round"
"""The round open while none is kept: open since the start."""
NAME_LENGTH = 60
"""A round's name, trimmed, is 1 to NAME_LENGTH characters."""
ROUND_OPEN = "Close the current review round before starting the next."
NO_ROUND_OPEN = "No review round is open."
STILL_OPEN = "Close this review round to compare with it."
BEFORE_FRESH_START = "This review round closed before the fresh start, so it can't be compared."


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


@dataclass(frozen=True)
class ListedRound:
    """A round as the listing shows it (#571): `number` its position from 1 in the order opened;
    `before_fresh_start` when it closed before the fresh start, so it can't be compared with;
    `results` frozen at close (empty while open), which a comparison is read from."""

    number: int
    name: str
    opened_at: datetime | None
    closed_at: datetime | None
    before_fresh_start: bool
    results: tuple[FrozenControl, ...] = ()


def number_rounds(kept: Sequence[Round], fresh_start: datetime | None) -> list[ListedRound]:
    """The kept rounds, in the order opened, as listed; with none kept, the first round, open."""
    return [
        ListedRound(
            number,
            r.name,
            r.opened_at,
            r.closed_at,
            fresh_start is not None and r.closed_at is not None and r.closed_at < fresh_start,
            r.results,
        )
        for number, r in enumerate(kept or [FIRST], start=1)
    ]


def default_choice(listed: Sequence[ListedRound]) -> int | None:
    """The number of the last round closed since the fresh start; None when there is none."""
    closed = [r.number for r in listed if r.closed_at is not None and not r.before_fresh_start]
    return closed[-1] if closed else None


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

    def listed(
        self, user_id: str, profile_id: str, fresh_start: datetime | None
    ) -> list[ListedRound]:
        """Every round kept, oldest first (the open one last), each with its frozen results;
        the first round alone, open, while none is kept (#571)."""
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
        kept = self._kept(user_id, profile_id)
        if not kept:
            return FIRST
        return kept[-1] if kept[-1].closed_at is None else None

    def last_closed(self, user_id: str, profile_id: str, since: datetime | None) -> Round | None:
        closed = [r for r in self._kept(user_id, profile_id) if r.closed_at is not None]
        if since is not None:
            closed = [r for r in closed if r.closed_at is not None and r.closed_at >= since]
        return closed[-1] if closed else None

    def _kept(self, user_id: str, profile_id: str) -> list[Round]:
        with self._guard:
            return list(self._rounds.get((user_id, profile_id), []))

    def listed(
        self, user_id: str, profile_id: str, fresh_start: datetime | None
    ) -> list[ListedRound]:
        return number_rounds(self._kept(user_id, profile_id), fresh_start)

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

    def listed(
        self, user_id: str, profile_id: str, fresh_start: datetime | None
    ) -> list[ListedRound]:
        kept = [_round(r) for r in self.db.listed_rounds(user_id, profile_id)]
        return number_rounds(kept, fresh_start)

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


def counted_now(
    controls: Iterable[IsaControl],
    latest: Mapping[str, Assessment],
    earlier: Mapping[str, Assessment] | None,
) -> tuple[set[str], dict[str, Assessment]]:
    """The listed controls re-assessing (#438: the newest assessment waits for review, an earlier
    one in `earlier` was accepted), and each control's assessment counted now: the earlier
    accepted one while re-assessing, else its newest in `latest`."""
    earlier = earlier or {}
    reassessing = {
        c.id
        for c in controls
        if (a := latest.get(c.id)) is not None
        and a.status == "waiting-for-review"
        and (prior := earlier.get(c.id)) is not None
        and prior.status == "accepted"
    }
    return reassessing, {**latest, **{i: earlier[i] for i in reassessing}}


def compare(
    frozen: Sequence[FrozenControl],
    controls: Iterable[IsaControl],
    latest: Mapping[str, Assessment],
    earlier: Mapping[str, Assessment] | None = None,
) -> Comparison:
    """Now (the listed controls' newest assessments in `latest`) against a closed round's
    `frozen` results (then), counted the same way: accepted verdicts only. A control whose
    newest assessment waits for review while an earlier one was accepted (`earlier`: each
    control's newest accepted assessment before its newest; read only while the newest waits)
    counts now under that accepted verdict and is re-assessing (#438), rather than reading as a
    regression; otherwise now is the overview's readiness. `changed` lists, in the controls'
    order (the base framework's), each listed control whose accepted verdict differs (None when
    not accepted) and each one re-assessing; a control frozen but no longer listed is left out,
    of the counts too, and one new to the profile reads None then."""
    controls = list(controls)
    earlier = earlier or {}
    listed = {c.id for c in controls}
    reassessing, counted = counted_now(controls, latest, earlier)
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


ChangeKind = Literal["better", "worse", "new", "unchanged", "new-since", "out-of-scope-since"]
COUNTED_CHANGES: tuple[ChangeKind, ...] = ("better", "worse", "new", "unchanged")
"""The changes counted in the summary; the scope changes are left out of it."""
VERDICT_RANK: dict[Verdict, int] = {
    "not-evidenced": 0,
    "partly-evidenced": 1,
    "evidenced": 2,
    "not-applicable": 2,
}
"""Not evidenced < Partly evidenced < Evidenced; a control's Not applicable verdict ranks with
Evidenced (it asks nothing more), so a change between the two is unchanged."""


class ControlChange(TypedDict):
    """One control in a round comparison: how its accepted verdict changed since the round."""

    control_id: str
    chapter: str
    """The ISA chapter: the first part of the control's id."""
    change: ChangeKind
    before: Verdict | None
    """The accepted verdict when the round closed; None when not accepted."""
    after: Verdict | None
    """The accepted verdict now (the earlier one while a newer assessment waits)."""
    proposed: bool
    """Its newest assessment waits for review ("Proposed, waiting for review")."""


class Readiness(TypedDict):
    """Readiness then and now: accepted controls, and accepted controls by verdict."""

    accepted: Count
    by_verdict: dict[Verdict, Count]


class RoundComparison(TypedDict):
    """Now against a chosen closed round (#571), control by control."""

    changes: dict[ChangeKind, int]
    readiness: Readiness
    controls: list[ControlChange]


def _change(before: Verdict | None, after: Verdict | None) -> ChangeKind:
    """A control in scope then and now, by accepted verdict (None: not accepted): new when
    accepted now only; worse when accepted then and not now, or lower by VERDICT_RANK; better
    when higher; else unchanged."""
    if before is None:
        return "unchanged" if after is None else "new"
    if after is None or VERDICT_RANK[after] < VERDICT_RANK[before]:
        return "worse"
    return "better" if VERDICT_RANK[after] > VERDICT_RANK[before] else "unchanged"


def _control_change(
    control_id: str,
    kept: FrozenControl | None,
    counted: Assessment | None,
    newest: Assessment | None,
    in_scope: bool,
) -> ControlChange:
    """One control's row: `kept` as frozen then (None: not frozen), `counted` the assessment it
    counts under now and `newest` its newest one (both read only while `in_scope` now)."""
    before = kept.verdict if kept is not None and kept.status == "accepted" else None
    if not in_scope:
        after, proposed = None, False
        change: ChangeKind = "out-of-scope-since"
    else:
        after = counted.verdict if counted and counted.status == "accepted" else None
        proposed = newest is not None and newest.status == "waiting-for-review"
        change = "new-since" if kept is None else _change(before, after)
    return {
        "control_id": control_id,
        "chapter": control_id.split(".")[0],
        "change": change,
        "before": before,
        "after": after,
        "proposed": proposed,
    }


def round_comparison(
    frozen: Sequence[FrozenControl],
    controls: Iterable[IsaControl],
    applicable: Iterable[IsaControl],
    latest: Mapping[str, Assessment],
    earlier: Mapping[str, Assessment] | None = None,
) -> RoundComparison:
    """Now against a closed round's `frozen` results, each control of `controls` (the base
    framework's order) that is applicable now (`applicable`) or was frozen then: better, worse,
    new (no accepted verdict then, one now) or unchanged by accepted verdict; new since (applicable
    now, not frozen then) or out of scope since (frozen then, not applicable now), which the
    counts leave out. A control whose newest assessment waits for review is proposed, and counts
    under its earlier accepted verdict (`earlier`, as `compare` reads it, #438). Readiness now
    is the overview's, of the controls in scope now; readiness then is the round's as it closed,
    every frozen accepted control (those out of scope now too)."""
    controls = list(controls)
    applicable = list(applicable)
    now_ids = {c.id for c in applicable}
    then = {f.control_id: f for f in frozen}
    _, counted = counted_now(applicable, latest, earlier)
    now = readiness(applicable, counted)
    then_accepted = Counter(f.verdict for f in frozen if f.status == "accepted" and f.verdict)
    ordered = [c.id for c in controls]
    in_edition = set(ordered)
    ordered += [i for i in then if i not in in_edition]  # frozen, no longer in the edition
    rows = [
        _control_change(i, then.get(i), counted.get(i), latest.get(i), i in now_ids)
        for i in ordered
        if i in now_ids or i in then
    ]
    tally = Counter(c["change"] for c in rows)
    return {
        "changes": {k: tally[k] for k in COUNTED_CHANGES},
        "readiness": {
            "accepted": {"now": now["accepted"], "then": then_accepted.total()},
            "by_verdict": {
                v: {"now": now["by_verdict"][v], "then": then_accepted[v]} for v in VERDICTS
            },
        },
        "controls": rows,
    }


class Done(TypedDict):
    """A remediation item marked done since a round closed."""

    control_id: str
    requirement_id: str
    at: datetime


class Answered(TypedDict):
    """An open question answered since a round closed."""

    control_id: str
    question_id: int
    at: datetime


def _after[T](stamped: Iterable[tuple[datetime | None, T]], closed_at: datetime) -> list[T]:
    """The items stamped after `closed_at` (None: not yet), oldest first."""
    kept = [(at, item) for at, item in stamped if at is not None and at > closed_at]
    return [item for _, item in sorted(kept, key=lambda pair: pair[0])]


def done_since(
    control_of: Mapping[str, str],
    marked: Mapping[tuple[str, str], Mark],
    closed_at: datetime,
    in_scope: Collection[str],
) -> list[Done]:
    """The remediation items marked done after `closed_at` (`marked`: by thread and requirement;
    `control_of`: each listed thread's control) on controls `in_scope` now, oldest first."""
    return _after(
        (
            (m.done_at, Done(control_id=control_of[t], requirement_id=r, at=m.done_at))
            for (t, r), m in marked.items()
            if m.done_at is not None and control_of.get(t) in in_scope
        ),
        closed_at,
    )


def answered_since(
    asked: Iterable[OpenQuestion], closed_at: datetime, in_scope: Collection[str]
) -> list[Answered]:
    """The open questions answered after `closed_at` on controls `in_scope` now, oldest first."""
    return _after(
        (
            (q.answered_at, Answered(control_id=q.control_id, question_id=q.id, at=q.answered_at))
            for q in asked
            if q.answered_at is not None and q.control_id in in_scope
        ),
        closed_at,
    )
