"""A fresh start of a lead's progress under a profile (#285), for a repeat demo: listed from a time
on, nothing deleted, the audit trail whole.

Through the HTTP API, on the bundled fake model, on both backends: in memory (no database URL)
and Postgres (CI's `database` job gives a server through VOUCH_TEST_DATABASE_URL; without one
those cases are skipped). The user is set per request by a test header (test_memory's). The
memory's own rules (the instant, a second fresh start, removing the note) are test_memory's.
"""

from typing import Any

import test_memory
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore
from test_api import read_events
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture: postgres's)
from test_memory import app_on, assess, failing_write, set_daily_limit, user_memory
from test_remediation import OWNER, accept, confirmed_with_an_item, decide, trail

from vouch import memory
from vouch.api import PROFILE_HEADER
from vouch.profile import DEFAULT_PROFILE

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

FRESH_START = "/api/progress/fresh-start"
OTHER_PROFILE = "alpenwerk"


def start_afresh(client: TestClient, user: dict[str, str]) -> Any:
    return client.post(FRESH_START, headers=user)


def overview(client: TestClient, user: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/overview", headers=user)
    assert response.status_code == 200, response.text
    return response.json()


def statuses(shown: dict[str, Any]) -> set[str]:
    return {card["status"] for card in shown["controls"]}


def test_the_overview_starts_from_nothing_after_a_fresh_start(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = confirmed_with_an_item(client, alice)
        decide(client, alice, thread_id, decision="approve", owner_role=OWNER)
        accept(client, alice, thread_id)
        before = overview(client, alice)

        answer = start_afresh(client, alice)
        after = overview(client, alice)

    assert answer.status_code == 200, answer.text
    assert answer.json()["fresh_start"]
    assert before["readiness"]["accepted"] == 1
    assert before["readiness"]["remediation_approved"] == 1
    assert statuses(after) == {"not-assessed"}
    assert after["readiness"]["accepted"] == 0
    assert after["readiness"]["remediation_items"] == 0
    assert after["readiness"]["remediation_approved"] == 0


def test_a_fresh_start_deletes_nothing_the_old_thread_still_opens(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = confirmed_with_an_item(client, alice)
        decide(client, alice, thread_id, decision="discard")
        accept(client, alice, thread_id)
        start_afresh(client, alice)
        listed = client.get("/api/assessments", headers=alice)
        opened = client.get(f"/api/assessments/{thread_id}", headers=alice)

    assert listed.json()["assessments"] == []
    assert opened.status_code == 200
    assert opened.json()["assessment"]["status"] == "accepted"


def test_the_report_lists_every_control_open_and_keeps_the_earlier_accept_in_its_trail(
    database_url, users
):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = confirmed_with_an_item(client, alice)
        decide(client, alice, thread_id, decision="approve", owner_role=OWNER)
        accept(client, alice, thread_id)
        start_afresh(client, alice)
        response = client.get("/api/report", headers=alice)

    shown = response.json()
    assert {row["status"] for row in shown["controls"]} == {"open"}
    assert shown["remediation"] == []
    assert shown["readiness"]["accepted"] == 0
    accepts = [e for e in shown["trail"]["entries"] if e["action"] == "accept"]
    assert [e["thread_id"] for e in accepts] == [thread_id]


def test_the_audit_trail_is_the_same_before_and_after_a_fresh_start(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = confirmed_with_an_item(client, alice)
        decide(client, alice, thread_id, decision="approve", owner_role=OWNER)
        accept(client, alice, thread_id)
        before = trail(client, alice)
        start_afresh(client, alice)
        after = trail(client, alice)

    assert before  # the steps of the review and the approval
    assert after == before


def test_the_chat_opens_a_new_conversation_and_the_old_ones_lines_are_kept(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        asked = client.post(
            "/api/chat", json={"page": "dossier", "message": "Where do I stand?"}, headers=alice
        )
        read_events(asked)
        old = client.get("/api/chat/current", headers=alice).json()
        start_afresh(client, alice)
        current = client.get("/api/chat/current", headers=alice).json()

    assert old["thread_id"] and old["lines"]
    assert current == {"thread_id": None, "lines": []}
    with user_memory(database_url, alice) as mine:
        assert mine.chat_lines(old["thread_id"]) == old["lines"]


def test_another_users_progress_and_the_leads_other_profile_are_untouched(database_url, users):
    alice, bob = users
    alice_elsewhere = alice | {PROFILE_HEADER: OTHER_PROFILE}
    with TestClient(app_on(database_url)) as client:
        for user in (alice, bob, alice_elsewhere):
            assess(client, user)
        bobs = overview(client, bob)
        elsewhere = overview(client, alice_elsewhere)

        start_afresh(client, alice)

        assert statuses(overview(client, alice)) == {"not-assessed"}
        assert overview(client, bob) == bobs
        assert overview(client, alice_elsewhere) == elsewhere
    assert "waiting-for-review" in statuses(bobs) and "waiting-for-review" in statuses(elsewhere)


def test_while_an_assessment_runs_a_fresh_start_is_409_until_it_ends(database_url, users):
    alice, _ = users
    app = app_on(database_url)
    with TestClient(app) as client:
        assess(client, alice)
        before = overview(client, alice)
        scope = memory.Memory(memory.process_store(), alice["X-Test-User"], DEFAULT_PROFILE).scope
        assert app.state.running.try_hold(scope)  # the lead's run, going

        refused = start_afresh(client, alice)
        during = overview(client, alice)
        app.state.running.release(scope)
        accepted = start_afresh(client, alice)

    assert refused.status_code == 409
    assert during == before
    assert accepted.status_code == 200
    assert app.state.running.try_hold(scope)  # the fresh start let go of the lead's scope


def test_with_the_store_unavailable_a_fresh_start_is_503_and_nothing_changes(users, monkeypatch):
    alice, _ = users
    app = app_on(None)
    with TestClient(app, raise_server_exceptions=False) as client:
        assess(client, alice)
        before = overview(client, alice)
        monkeypatch.setattr(InMemoryStore, "put", failing_write)  # the database, down
        refused = start_afresh(client, alice)
        monkeypatch.undo()
        after = overview(client, alice)
        retried = start_afresh(client, alice)  # the hold was let go on the failure

    assert refused.status_code == 503
    assert refused.json()["unavailable"] == "database"
    assert after == before
    assert retried.status_code == 200


def test_a_fresh_start_counts_nothing_against_the_daily_limit_nor_gives_any_back(
    database_url, users
):
    """With a limit of 2: one assessment before the fresh start, one after, and a third refused.
    A fresh start that counted would refuse the second; one that reset the day's count would let
    the third through."""
    alice, _ = users
    with TestClient(app_on(database_url), raise_server_exceptions=False) as client:
        set_daily_limit(client, alice, 2)
        assess(client, alice)
        assert start_afresh(client, alice).status_code == 200
        assess(client, alice)
        third = client.post(
            "/api/assessments", json={"control_id": test_memory.DEMO_CONTROL}, headers=alice
        )

    assert third.status_code == 429
