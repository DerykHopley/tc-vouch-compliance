"""Reset demo (#605; GLOSSARY: Reset demo): a developer puts one company back to a clean demo
state, the same every time. Every user's progress and history under the company is deleted
(stored memory and checkpointed threads, the audit trail, remediation decisions and the plan,
the Goal, review rounds, open questions, added documents with every version), then the reset's
own audit entry is written, who reset which company and when. On Nordlicht the demo round
(#579, vouch.demo_round's `seed`) is then played for the developer through the app's own
routes, on the bundled fake model, counting against no daily limit and using no profile pass.

Postgres deletes in one transaction under the guarded tables' reset setting
(vouch.database's `reset_company`, RESET_DEMO_SETTING); without a database, this process's
stores forget the company. A real deployment would drop this delete exception.

One reset of a company runs at a time in this process (#619, `hold`): a second one
while it runs is refused, rather than finding the seed's assessment already running.

FastAPI's TestClient is imported at the top on purpose: the seed's client is the app's own
routes, in this process, for the seeding app (`seed_demo`), not a test.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from vouch import audit, demo_round, goal, plan, questions, remediation, rounds
from vouch.added_documents import added_index, process_added_documents
from vouch.audit import AuditEntry, Reviewer
from vouch.database import Database
from vouch.memory import process_checkpointer, process_store, store_label
from vouch.profile import DEFAULT_PROFILE
from vouch.runs import RunningAssessments
from vouch.spending import SpendingGuard

PAGE = 100
"""How many namespaces, or items, the wipe reads from the process's store at a time."""
DEMO_COMPANY = DEFAULT_PROFILE
"""The company whose Reset demo seeds the demo round; every other one is left empty."""
ANOTHER_RESET = (
    "Another reset of {company} is running. Nothing was reset; try again when it has finished."
)
"""The refusal of a reset while another of the same company runs (#619)."""


class ResetRunning(RuntimeError):
    """Another reset of the company is running in this process."""


@contextmanager
def hold(resets: RunningAssessments, profile_id: str) -> Iterator[None]:
    """Held for the reset of `profile_id`, released however it ends; ResetRunning, with nothing
    held, when another reset of it is running. `resets` is a guard of its own (app.state.resets),
    vouch.runs' one-each lock keyed by company here rather than by memory scope."""
    if not resets.try_hold(profile_id):
        raise ResetRunning(profile_id)
    try:
        yield
    finally:
        resets.release(profile_id)


def reset_entry(reviewer: Reviewer, profile_id: str, at: datetime) -> AuditEntry:
    """The reset's audit entry: who, when and which company; nothing else (as a goal change's)."""
    return AuditEntry(
        id=None,
        user_id=reviewer.id,
        reviewer_name=reviewer.name,
        at=at,
        profile_id=profile_id,
        control_id="",
        thread_id="",
        action="demo-reset",
        requirement_id=None,
        note="",
        verdict_before=None,
        verdict_after=None,
        evidence=(),
        model="",
        prompt_version="",
    )


def wipe(db: Database | None, profile_id: str, reviewer: Reviewer, at: datetime) -> None:
    """Every user's progress and history under `profile_id` deleted, then the reset's entry
    written; on the demo company, the developer's first round opened as "Q2 2026", so the
    seed's rounds are the only ones listed. Raises DatabaseUnavailable."""
    entry = reset_entry(reviewer, profile_id, at)
    first = (reviewer.id, demo_round.ROUND_NAME, at) if profile_id == DEMO_COMPANY else None
    if db is not None:
        row = {k: v for k, v in asdict(entry).items() if k != "id"}
        db.reset_company(profile_id, store_label(profile_id), row, first)
    else:
        _forget_in_process(profile_id)
        audit.process_trail().keep([entry])
        if first is not None:
            rounds.process_rounds().open_first(first[0], profile_id, first[1], first[2])
    added_index().put(profile_id, ())


def _forget_in_process(profile_id: str) -> None:
    store = process_store()
    company = ("vouch", "*", store_label(profile_id))  # every user's namespaces under it
    page, offset = PAGE, 0
    while namespaces := store.list_namespaces(prefix=company, limit=page, offset=offset):
        for namespace in namespaces:
            while items := store.search(namespace, limit=page):
                for item in items:  # a search finds the namespaces under it too
                    store.delete(tuple(item.namespace), item.key)
        offset += page  # an emptied namespace stays listed, so the next page is further on
    saver = process_checkpointer()
    if isinstance(saver, InMemorySaver):
        for thread in [t for t in list(saver.storage) if _scoped_to(t, profile_id)]:
            saver.delete_thread(thread)
    for kept in (
        audit.process_trail(),
        remediation.process_items(),
        plan.process_plan(),
        goal.process_goals(),
        rounds.process_rounds(),
        questions.process_questions(),
        process_added_documents(),
    ):
        kept.forget(profile_id)


def _scoped_to(thread: str, profile_id: str) -> bool:
    """The checkpointed thread is under `profile_id`: its key starts with vouch.memory's scope,
    the JSON list [user id, profile id]."""
    try:
        scope, _ = json.JSONDecoder().raw_decode(thread)
    except ValueError:
        return False
    return isinstance(scope, list) and len(scope) == 2 and scope[1] == profile_id


class Uncounted(SpendingGuard):
    """The seed's spending guard (#605): its fake-model assessments and profile pass count
    against no daily limit; usage is still recorded."""

    def start(self, user_id: str, paid: bool) -> None:
        if paid:  # never: the seeding app has no key
            super().start(user_id, paid)

    def start_profile_pass(self, user_id: str, paid: bool) -> None:
        if paid:
            super().start_profile_pass(user_id, paid)


SHARED_STATE = ("database", "running", "current_company", "added_documents")
"""What the seeding app shares with the app serving the developer: its stores, so the seed's
work lands where everyone reads it."""


def seed_demo(app: FastAPI, user_id: str, name: str) -> demo_round.Seeded:
    """The demo round played for the developer inside `app` (vouch.demo_round's `seed`), on a
    seeding app of its own (as that user, on the bundled fake model, named `name` on the audit
    entries), its spending Uncounted. A server error is a 500, so a SeedError."""
    seeding = demo_round.seeding_app(None, user_id, name)
    for key in SHARED_STATE:
        setattr(seeding.state, key, getattr(app.state, key))
    guard: SpendingGuard = app.state.spending
    seeding.state.spending = Uncounted(guard.store, guard.clock)
    # Never entered: its lifespan would close `app`'s stores.
    return demo_round.seed(TestClient(seeding, raise_server_exceptions=False))
