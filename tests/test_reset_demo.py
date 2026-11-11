"""Reset demo (#605): a developer puts a company back to a clean demo state, the same every
time. Every user's progress and history under it is deleted (assessments, rounds, the audit
trail, open questions, remediation, the plan, the Goal, added documents and their versions),
the reset's own audit entry is written, and on Nordlicht the demo round (#579, vouch.demo_round)
is seeded for the developer, on the fake model, counting against no daily limit.

Through the HTTP API on both backends (memory, and Postgres under `scripts/test-db.sh`); the
Postgres triggers that allow the reset's delete and refuse every other one are
test_database's.
"""

import base64
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
import test_memory
from fastapi.testclient import TestClient
from test_auth import FakeClerk, bearer, clerk_app, token
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture: postgres's)
from test_memory import DEMO_CONTROL, app_on, restart, set_daily_limit
from test_remediation import confirmed_with_an_item, decide

from vouch.api import PROFILE_HEADER
from vouch.database import (
    ADDED_DOCUMENTS_TABLE,
    AUDIT_TABLE,
    REMEDIATION_TABLE,
    RESET_DEMO_SETTING,
    Database,
)
from vouch.demo_round import NEXT_ROUND, ROUND_NAME
from vouch.spending import DAILY_LIMIT

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

ALPENWERK = {PROFILE_HEADER: "alpenwerk"}


@pytest.fixture(autouse=True)
def _fresh_process() -> Iterator[None]:
    restart()
    yield
    restart()


def reset(client: TestClient, user: dict[str, str], company: str = "nordlicht") -> Any:
    return client.post(f"/api/developer/profiles/{company}/reset-demo", headers=user)


def reset_ok(client: TestClient, user: dict[str, str], company: str = "nordlicht") -> Any:
    response = reset(client, user, company)
    assert response.status_code == 200, response.text
    return response.json()


def get(client: TestClient, path: str, user: dict[str, str]) -> Any:
    response = client.get(path, headers=user)
    assert response.status_code == 200, response.text
    return response.json()


def state(client: TestClient, user: dict[str, str]) -> dict[str, Any]:
    """Everything the reset decides, without ids or times: rounds, assessments, the audit
    trail, questions, remediation, the Goal and the added documents."""
    rounds = get(client, "/api/rounds", user)["rounds"]
    assessed = get(client, "/api/assessments", user)["assessments"]
    trail = get(client, "/api/audit-trail?limit=200", user)["entries"]
    overview = get(client, "/api/overview", user)
    documents = get(client, "/api/documents", user)["documents"]
    return {
        "rounds": [
            (r["name"], r["closed_at"] is not None, r["before_fresh_start"]) for r in rounds
        ],
        "assessed": sorted(a["control_id"] for a in assessed),
        "trail": [(e["action"], e["control_id"], e.get("requirement_id")) for e in trail],
        "questions": {
            c: [{k: v for k, v in q.items() if not k.endswith(("id", "_at"))} for q in qs]
            for c, qs in overview["questions"].items()
        },
        "readiness": overview["readiness"],
        "goal": overview["goal"],
        "added": sorted(d["id"] for d in documents if d["origin"] == "added"),
    }


def worked(client: TestClient, user: dict[str, str], headers: dict[str, str] | None = None) -> None:
    """A user's own work under a company: an assessment, a closed round, the Goal changed and
    an added document."""
    h = user | (headers or {})
    assert client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=h).is_success
    if get(client, "/api/overview", h)["rounds"]["current"] is not None:
        assert client.post("/api/rounds/close", headers=h).is_success
    assert client.put("/api/goal", json={"goal": "internal-improvement"}, headers=h).is_success
    added = {
        "filename": "badges.md",
        "content": base64.b64encode(b"# Visitor badges\n\nEvery visitor wears a badge.\n").decode(),
        "title": "Visitor badge policy",
        "kind": "policy",
        "approval_date": "2026-10-01",
        "review_cycle": "yearly",
    }
    response = client.post("/api/documents", json=added, headers=h)
    assert response.status_code == 201, response.text


def test_reset_demo_on_nordlicht_lists_the_demo_rounds_and_compares_with_q2(database_url, users):
    developer, _ = users
    with TestClient(app_on(database_url)) as client:
        worked(client, developer)
        answer = reset_ok(client, developer)
        listed = get(client, "/api/rounds", developer)
        compared = get(client, f"/api/rounds/{listed['default']}/comparison", developer)

    assert answer["seeded"] is True
    assert [(r["name"], r["closed_at"] is not None) for r in listed["rounds"]] == [
        (ROUND_NAME, True),
        (NEXT_ROUND, False),
    ]
    assert not any(r["before_fresh_start"] for r in listed["rounds"])
    assert (compared["changes"]["better"], compared["changes"]["worse"]) == (3, 0)


def test_two_resets_give_the_identical_state(database_url, users):
    developer, _ = users
    with TestClient(app_on(database_url)) as client:
        reset_ok(client, developer)
        first = state(client, developer)
        worked(client, developer)
        reset_ok(client, developer)
        second = state(client, developer)

    assert first == second
    assert first["trail"][-1][0] == "demo-reset"  # the oldest entry: the reset, then the seed


def test_reset_demo_on_another_company_leaves_every_users_progress_and_history_gone(
    database_url, users
):
    developer, lead = users
    with TestClient(app_on(database_url)) as client:
        worked(client, developer, ALPENWERK)
        worked(client, lead, ALPENWERK)
        worked(client, lead)  # Nordlicht's: kept
        answer = reset_ok(client, developer, "alpenwerk")
        theirs = state(client, lead | ALPENWERK)
        mine = state(client, developer | ALPENWERK)
        nordlicht = state(client, lead)
        report = client.get("/api/report/markdown", headers=developer | ALPENWERK)

    assert answer["seeded"] is False
    assert theirs["rounds"] == [("First round", False, False)]
    assert theirs["assessed"] == [] and theirs["trail"] == [] and theirs["added"] == []
    assert theirs["goal"] == "audit-readiness"
    assert mine["trail"] == [("demo-reset", "", None)]
    assert report.status_code == 200, report.text
    assert nordlicht["assessed"] == [DEMO_CONTROL]  # another company is left as it was


def test_the_reset_counts_against_no_daily_limit(database_url, users):
    developer, _ = users
    with TestClient(app_on(database_url)) as client:
        for _ in range(3):  # the day's profile passes, used up
            assert client.post("/api/profile-pass", headers=developer).status_code == 200
        set_daily_limit(client, developer, 1)
        try:
            used = client.post(
                "/api/assessments", json={"control_id": DEMO_CONTROL}, headers=developer
            )
            assert used.status_code == 200, used.text  # the day's one assessment, used up
            answer = reset_ok(client, developer)
            refused = client.post(
                "/api/assessments", json={"control_id": DEMO_CONTROL}, headers=developer
            )
        finally:
            set_daily_limit(client, developer, DAILY_LIMIT)

    assert answer["seeded"] is True
    assert refused.status_code == 429  # the developer's own limit still holds


def test_an_unknown_company_is_404(users):
    developer, _ = users
    with TestClient(app_on(None)) as client:
        assert reset(client, developer, "nobody").status_code == 404


def test_a_compliance_lead_is_refused_and_nothing_changes():
    app = clerk_app(FakeClerk({"user_alice": {}}))
    client = TestClient(app, raise_server_exceptions=False)

    refused = client.post(
        "/api/developer/profiles/nordlicht/reset-demo", headers=bearer(token("user_alice"))
    )

    assert refused.status_code == 403


# --- The Postgres guards ------------------------------------------------------------------------

GUARDED = (AUDIT_TABLE, REMEDIATION_TABLE, ADDED_DOCUMENTS_TABLE)
MAINS_GUARDS = (
    (
        "CREATE OR REPLACE FUNCTION vouch_audit_append_only() RETURNS trigger LANGUAGE plpgsql "
        "AS $$ BEGIN RAISE EXCEPTION 'the audit trail is append-only'; END $$"
    ),
    (
        "CREATE OR REPLACE FUNCTION vouch_remediation_final() RETURNS trigger LANGUAGE plpgsql "
        "AS $$ BEGIN RAISE EXCEPTION 'a remediation decision is final'; END $$"
    ),
)
"""main's guards before #605 (the added documents' is replaced the same way): every delete
refused, whatever is set."""


def seeded_rows(db: Database) -> None:
    """One row in each guarded table, written through the app as a lead would."""
    with TestClient(app_on(db.url)) as client:
        lead = {"X-Test-User": "user_rows"}
        worked(client, lead)
        decide(client, lead, confirmed_with_an_item(client, lead), decision="discard")


def counts(db: Database) -> dict[str, int]:
    with db.connection() as conn:
        return {
            t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]  # type: ignore[index]
            for t in GUARDED
        }


@needs_postgres
@pytest.mark.parametrize("table", GUARDED)
def test_a_delete_without_the_resets_setting_is_refused(postgres, table):
    seeded_rows(postgres)
    with pytest.raises(psycopg.errors.RaiseException), postgres.connection() as conn:
        conn.execute(f"DELETE FROM {table}")
    with (
        pytest.raises(psycopg.errors.RaiseException),
        postgres.connection() as conn,
        conn.transaction(),
    ):
        conn.execute(f"SET LOCAL {RESET_DEMO_SETTING} = 'on'")
        conn.execute(f"TRUNCATE {table}")  # a truncate stays refused, set or not


@needs_postgres
def test_the_resets_setting_allows_the_delete_in_its_own_transaction_only(postgres):
    seeded_rows(postgres)
    assert all(counts(postgres).values())
    with postgres.connection() as conn:
        with conn.transaction():
            conn.execute(f"SET LOCAL {RESET_DEMO_SETTING} = 'on'")
            for table in GUARDED:
                conn.execute(f"DELETE FROM {table}")
        seeded_rows(postgres)
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(f"DELETE FROM {AUDIT_TABLE}")  # the setting ended with its transaction


@needs_postgres
def test_setup_twice_on_mains_data_keeps_it_and_allows_the_resets_delete(postgres):
    with postgres.connection() as conn:
        for guard in MAINS_GUARDS:
            conn.execute(guard)
    seeded_rows(postgres)
    before = counts(postgres)

    postgres.setup()
    postgres.setup()

    assert counts(postgres) == before
    with pytest.raises(psycopg.errors.RaiseException), postgres.connection() as conn:
        conn.execute(f"DELETE FROM {AUDIT_TABLE}")
    with postgres.connection() as conn, conn.transaction():
        conn.execute(f"SET LOCAL {RESET_DEMO_SETTING} = 'on'")
        conn.execute(f"DELETE FROM {AUDIT_TABLE}")
    assert counts(postgres)[AUDIT_TABLE] == 0
