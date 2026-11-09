"""A compliance profile's Goal (#369): Audit readiness or Internal improvement.

The Goal is kept per user and profile, with the rest of the user's work under that profile; with
none kept it is Audit readiness, so a profile whose goal was never changed behaves as before.
The lead changes it, and each change is an audit trail entry (`goal`, with the goal before and
after), kept with it: both, or neither (Postgres: one transaction, vouch.database's
`change_goal`; without a database, this process's memory and trail, whose writes can't fail).
The goal already kept again writes nothing (a double click changes it once).

A fresh start (vouch.memory's `start_afresh`) leaves it as it is: it is a setting of the
profile, not progress. In this ticket the goal changes nothing else.
"""

import threading
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from functools import cache
from typing import Protocol

from pydantic import BaseModel

from vouch.audit import AuditEntry, ProcessTrail, Reviewer, now, process_trail
from vouch.database import Database, Goal

DEFAULT_GOAL: Goal = "audit-readiness"

Entry = Callable[[Goal, Goal], AuditEntry]
"""A change's audit entry, from the goal before and after."""


class GoalRequest(BaseModel):
    """The goal the lead changes the profile's to."""

    goal: Goal


class ProfileGoals(Protocol):
    """Where each user's goal under each profile is kept, each change with its audit entry."""

    def goal(self, user_id: str, profile_id: str) -> Goal:
        """The user's goal under the profile: DEFAULT_GOAL when none was kept."""
        ...

    def change(self, user_id: str, profile_id: str, goal: Goal, at: datetime, entry: Entry) -> bool:
        """Keep `goal` as the user's under the profile, changed at `at`, with `entry` of the
        goal before and after, together; whether it changed (the goal kept already writes nothing). Two at once
        take turns, so each real change writes one entry."""
        ...


class ProcessGoals:
    """Goals without a database: in this process, their entries in its audit trail
    (vouch.audit's, read at each change, as vouch.remediation's ProcessItems reads it)."""

    def __init__(self, trail: Callable[[], ProcessTrail] = process_trail) -> None:
        self._trail = trail
        self._goals: dict[tuple[str, str], Goal] = {}
        self._guard = threading.Lock()

    def goal(self, user_id: str, profile_id: str) -> Goal:
        with self._guard:
            return self._goals.get((user_id, profile_id), DEFAULT_GOAL)

    def change(self, user_id: str, profile_id: str, goal: Goal, at: datetime, entry: Entry) -> bool:
        with self._guard:
            before = self._goals.get((user_id, profile_id), DEFAULT_GOAL)
            if before == goal:
                return False
            self._goals[(user_id, profile_id)] = goal
            self._trail().keep([entry(before, goal)])
        return True


class PostgresGoals:
    """Goals in Postgres (vouch.database), each change in one transaction with its entry."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def goal(self, user_id: str, profile_id: str) -> Goal:
        return self.db.profile_goal(user_id, profile_id) or DEFAULT_GOAL

    def change(self, user_id: str, profile_id: str, goal: Goal, at: datetime, entry: Entry) -> bool:
        def row(before: Goal) -> dict[str, object]:
            return {k: v for k, v in asdict(entry(before, goal)).items() if k != "id"}

        return self.db.change_goal(user_id, profile_id, goal, DEFAULT_GOAL, at, row)


@cache
def process_goals() -> ProcessGoals:
    """Goals without a database: one store per process, so a restart forgets them with the
    assessments."""
    return ProcessGoals()


def goal_entry(
    reviewer: Reviewer, profile_id: str, before: Goal, after: Goal, at: datetime
) -> AuditEntry:
    """The audit entry of a goal change: who, when and under which profile, the goals before
    and after; no control, thread, requirement, note, verdict, evidence, model or prompt."""
    return AuditEntry(
        id=None,
        user_id=reviewer.id,
        reviewer_name=reviewer.name,
        at=at,
        profile_id=profile_id,
        control_id="",
        thread_id="",
        action="goal",
        requirement_id=None,
        note="",
        verdict_before=None,
        verdict_after=None,
        evidence=(),
        model="",
        prompt_version="",
        goal_before=before,
        goal_after=after,
    )


def change(
    goals: ProfileGoals,
    goal: Goal,
    *,
    reviewer: Reviewer,
    profile_id: str,
    clock: Callable[[], datetime] = now,
) -> bool:
    """The lead changes their goal under the profile to `goal`, with its audit entry, at
    `clock()`; whether it changed. Raises DatabaseUnavailable (Postgres)."""
    at = clock()

    def entry(before: Goal, after: Goal) -> AuditEntry:
        return goal_entry(reviewer, profile_id, before, after, at)

    return goals.change(reviewer.id, profile_id, goal, at, entry)
