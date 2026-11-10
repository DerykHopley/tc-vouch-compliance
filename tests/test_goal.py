"""A compliance profile's Goal (#369): Audit readiness or Internal improvement, per user and
profile, changed by the lead with an audit entry each time.

The stores directly (vouch.goal's ProcessGoals and PostgresGoals), then through the HTTP API on
both backends: in memory (no database URL) and Postgres (CI's `database` job gives a server
through VOUCH_TEST_DATABASE_URL; without one those cases are skipped). The user is set per
request by a test header (test_memory's).
"""

import threading
from datetime import UTC, datetime
from typing import Any

import pytest
import test_memory
from fastapi.testclient import TestClient
from test_auth import FakeClerk, clerk_app
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture)
from test_memory import app_on

from vouch import goal
from vouch.audit import AuditEntry, ProcessTrail, Reviewer
from vouch.database import AUDIT_TABLE, GOALS_TABLE, SETUP_TABLES
from vouch.profile import DEFAULT_PROFILE

# test_memory's fixtures: each backend (memory, Postgres), a set-up Postgres database, two users.
database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

OTHER_PROFILE = "alpenwerk"
AT = datetime(2026, 11, 9, 10, 30, tzinfo=UTC)


def entry_for(user_id: str, profile_id: str):
    """A goal entry by `user_id` under the profile, for the stores' change."""

    def build(before: goal.Goal, after: goal.Goal) -> AuditEntry:
        return goal.goal_entry(Reviewer(user_id, "Dana Lead"), profile_id, before, after, AT)

    return build


# --- The stores ----------------------------------------------------------------------------


@pytest.fixture(params=["process", pytest.param("postgres", marks=needs_postgres)])
def goals(request) -> goal.ProfileGoals:
    if request.param == "process":
        kept = ProcessTrail()
        return goal.ProcessGoals(lambda: kept)
    return goal.PostgresGoals(request.getfixturevalue("postgres"))


def test_a_user_and_profile_with_no_goal_stored_read_audit_readiness(goals, users):
    alice, _ = users

    assert goals.goal(alice["X-Test-User"], DEFAULT_PROFILE) == "audit-readiness"


def test_a_change_is_read_back_for_that_user_and_profile_only(goals, users):
    alice, bob = users
    a = alice["X-Test-User"]

    changed = goals.change(
        a, DEFAULT_PROFILE, "internal-improvement", AT, entry_for(a, DEFAULT_PROFILE)
    )
    again = goals.change(
        a, DEFAULT_PROFILE, "internal-improvement", AT, entry_for(a, DEFAULT_PROFILE)
    )

    assert (changed, again) == (True, False)
    assert goals.goal(a, DEFAULT_PROFILE) == "internal-improvement"
    assert goals.goal(a, OTHER_PROFILE) == "audit-readiness"
    assert goals.goal(bob["X-Test-User"], DEFAULT_PROFILE) == "audit-readiness"


# --- Through the API -------------------------------------------------------------------------


def put_goal(client: TestClient, headers: dict[str, str], **body: Any) -> Any:
    return client.put("/api/goal", json=body, headers=headers)


def overview_goal(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get("/api/overview", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["goal"]


def trail(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/api/audit-trail", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def test_the_overview_says_audit_readiness_before_any_change(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        assert overview_goal(client, alice) == "audit-readiness"


def test_a_change_is_shown_on_the_overview_for_that_user_and_profile_only(database_url, users):
    alice, bob = users
    other = alice | {"X-Vouch-Profile": OTHER_PROFILE}
    with TestClient(app_on(database_url)) as client:
        changed = put_goal(client, alice, goal="internal-improvement")
        mine = overview_goal(client, alice)
        bobs = overview_goal(client, bob)
        under_other = overview_goal(client, other)

    assert changed.status_code == 200, changed.text
    assert changed.json() == {"goal": "internal-improvement"}
    assert (mine, bobs, under_other) == (
        "internal-improvement",
        "audit-readiness",
        "audit-readiness",
    )


def test_a_change_writes_one_goal_entry_with_empty_control_fields(database_url, users):
    alice, _ = users
    before = datetime.now(UTC)
    with TestClient(app_on(database_url)) as client:
        put_goal(client, alice, goal="internal-improvement")
        put_goal(client, alice, goal="audit-readiness")
        entries = trail(client, alice)

    back, first = entries  # newest first
    assert first == {
        "id": first["id"],
        "at": first["at"],
        "user_id": alice["X-Test-User"],
        "reviewer_name": "Local user",  # local mode's default user's name
        "profile_id": DEFAULT_PROFILE,
        "control_id": "",
        "thread_id": "",
        "action": "goal",
        "requirement_id": None,
        "note": "",
        "verdict_before": None,
        "verdict_after": None,
        "evidence": [],
        "model": "",
        "prompt_version": "",
        "goal_before": "audit-readiness",
        "goal_after": "internal-improvement",
        "round_name": None,
    }
    assert (back["goal_before"], back["goal_after"]) == ("internal-improvement", "audit-readiness")
    assert before <= datetime.fromisoformat(first["at"]) <= datetime.fromisoformat(back["at"])


def test_the_goal_already_stored_answers_200_and_writes_nothing(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        same_as_default = put_goal(client, alice, goal="audit-readiness")
        put_goal(client, alice, goal="internal-improvement")
        double_click = put_goal(client, alice, goal="internal-improvement")
        entries = trail(client, alice)

    assert same_as_default.status_code == 200
    assert same_as_default.json() == {"goal": "audit-readiness"}
    assert double_click.status_code == 200
    assert double_click.json() == {"goal": "internal-improvement"}
    assert [e["goal_after"] for e in entries] == ["internal-improvement"]


@pytest.mark.parametrize(
    "send",
    [
        {"json": {"goal": "audit"}},
        {"json": {"goal": None}},
        {"json": {}},
        {},  # no body at all
    ],
    ids=["unknown", "null", "missing", "no-body"],
)
def test_any_other_goal_is_a_422_and_writes_nothing(database_url, users, send):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        refused = client.put("/api/goal", headers=alice, **send)
        shown = overview_goal(client, alice)
        entries = trail(client, alice)

    assert refused.status_code == 422
    assert shown == "audit-readiness"
    assert entries == []


def test_a_fresh_start_leaves_the_goal_as_it_is(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        put_goal(client, alice, goal="internal-improvement")
        started = client.post("/api/progress/fresh-start", headers=alice)
        shown = overview_goal(client, alice)

    assert started.status_code == 200, started.text
    assert shown == "internal-improvement"


def test_signed_out_a_change_is_a_401():
    """In clerk mode with no token (test_auth's every-route check lists this route too)."""
    with TestClient(clerk_app(FakeClerk({}))) as client:
        response = client.put("/api/goal", json={"goal": "internal-improvement"})

    assert response.status_code == 401


# --- Postgres: one transaction, turns on the row, before setup ---------------------------------


@needs_postgres
def test_a_failing_trail_insert_is_a_503_and_keeps_the_goal(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        with postgres.connection() as conn:  # the entry's insert breaks a constraint
            conn.execute(
                f"ALTER TABLE {AUDIT_TABLE} ADD CONSTRAINT no_goal CHECK (action <> 'goal')"
            )
        failed = put_goal(client, alice, goal="internal-improvement")
        shown = overview_goal(client, alice)
        entries = trail(client, alice)

    assert failed.status_code == 503
    assert failed.json()["unavailable"] == "database"
    assert shown == "audit-readiness"
    assert entries == []


@needs_postgres
def test_two_changes_to_one_goal_at_once_write_one_entry(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url)) as client:
        start = threading.Barrier(2)
        codes: list[int] = []

        def change() -> None:
            start.wait()
            codes.append(put_goal(client, alice, goal="internal-improvement").status_code)

        threads = [threading.Thread(target=change) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        entries = trail(client, alice)
        shown = overview_goal(client, alice)

    assert codes == [200, 200]
    assert [(e["action"], e["goal_after"]) for e in entries] == [("goal", "internal-improvement")]
    assert shown == "internal-improvement"


@needs_postgres
def test_before_setup_made_the_goals_table_the_goal_reads_audit_readiness_and_a_change_is_a_503(
    postgres, users, caplog
):
    alice, _ = users
    with postgres.connection() as conn:  # a deploy that adds the table, before setup runs
        conn.execute(f"DROP TABLE {GOALS_TABLE}")
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        shown = overview_goal(client, alice)
        failed = put_goal(client, alice, goal="internal-improvement")
        entries = trail(client, alice)

    assert shown == "audit-readiness"
    assert failed.status_code == 503
    assert failed.json()["unavailable"] == "database"
    assert entries == []
    assert f"no {GOALS_TABLE} table" in caplog.text


def test_setup_makes_the_goals_table_and_health_checks_it():
    assert GOALS_TABLE in SETUP_TABLES
