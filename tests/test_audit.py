"""The audit trail (#39): each review and override recorded, append-only, with its decision.

Through the HTTP API, on the bundled fake model, on both backends: in memory (no database URL)
and Postgres (CI's `database` job gives a server through VOUCH_TEST_DATABASE_URL; without one
those cases are skipped). The user is set per request by a test header (test_memory's).
"""

import threading
from datetime import datetime
from typing import Any

import psycopg
import pytest
import test_memory
from fastapi.testclient import TestClient
from test_auth import api_routes
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture)
from test_memory import DEMO_CONTROL, app_on, assess

from vouch import load_named_profile
from vouch.api import compliance_profile
from vouch.database import AUDIT_TABLE
from vouch.profile import DEFAULT_PROFILE

# test_memory's fixtures: each backend (memory, Postgres), a set-up Postgres database, two users.
database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

# What the fake model's 4.1.2 run says (test_memory's DEMO_CONTROL): M1 partly evidenced, the
# rest evidenced, every quote from version 3.1 of the authentication standard.
AUTH_STANDARD = [{"doc_id": "authentication-standard", "version": "3.1"}]
OVERRIDE_M1 = {"requirement_id": "M1", "verdict": "evidenced", "reason": "MFA is enforced."}


def review(client: TestClient, headers: dict[str, str], thread_id: str, **decision: Any) -> Any:
    return client.post(f"/api/assessments/{thread_id}/review", json=decision, headers=headers)


def trail(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/api/audit-trail", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def test_an_accept_with_one_override_writes_two_entries(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = assess(client, alice)
        response = review(
            client, alice, thread_id, decision="accept", note="Accepted.", overrides=[OVERRIDE_M1]
        )
        assert response.status_code == 200, response.text
        reviewed = response.json()["assessment"]
        entries = trail(client, alice)

    accept, override = entries
    common = {
        "user_id": alice["X-Test-User"],
        "profile_id": reviewed["profile_id"],
        "control_id": DEMO_CONTROL,
        "thread_id": thread_id,
        "evidence": AUTH_STANDARD,
        "model": reviewed["model"],  # the thread's own, as the run recorded them
        "prompt_version": reviewed["prompt_version"],
    }
    assert accept == common | {
        "at": accept["at"],
        "action": "accept",
        "requirement_id": None,
        "note": "Accepted.",
        "verdict_before": "partly-evidenced",
        "verdict_after": "evidenced",
    }
    assert override == common | {
        "at": override["at"],
        "action": "override",
        "requirement_id": "M1",
        "note": "MFA is enforced.",
        "verdict_before": "partly-evidenced",
        "verdict_after": "evidenced",
    }
    assert reviewed["model"] and reviewed["prompt_version"]
    assert accept["at"] == override["at"]
    datetime.fromisoformat(accept["at"])


def test_entries_are_read_per_profile_newest_first(database_url, users):
    alice, bob = users
    app = app_on(database_url)
    with TestClient(app) as client:
        first = assess(client, alice)
        review(client, alice, first, decision="reject", note="See the records folder.")
        second = assess(client, alice)
        review(client, alice, second, decision="accept", overrides=[OVERRIDE_M1])
        mine = trail(client, alice)
        bobs = trail(client, bob)
        another = load_named_profile(DEFAULT_PROFILE).model_copy(update={"id": "another-profile"})
        app.dependency_overrides[compliance_profile] = lambda: another  # M4 brings a real one
        other_profile = trail(client, alice)

    assert [(e["thread_id"], e["action"]) for e in mine] == [
        (second, "accept"),
        (second, "override"),
        (first, "reject"),
    ]
    assert mine[0]["at"] >= mine[2]["at"]
    assert mine[2]["note"] == "See the records folder."
    assert bobs == []
    assert other_profile == []


@needs_postgres
def test_a_failing_trail_write_rolls_back_the_decision(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        thread_id = assess(client, alice)
        with postgres.connection() as conn:  # the insert fails: its table is gone
            conn.execute(f"DROP TABLE {AUDIT_TABLE}")
        failed = review(client, alice, thread_id, decision="accept", overrides=[OVERRIDE_M1])
        still = client.get(f"/api/assessments/{thread_id}", headers=alice).json()["assessment"]
        postgres.setup()  # the table back, empty
        again = review(client, alice, thread_id, decision="accept")
        entries = trail(client, alice)

    assert failed.status_code == 503
    assert still["status"] == "waiting-for-review"
    assert still["review"] is None
    assert [r["verdict"] for r in still["requirements"] if r["id"] == "M1"] == ["partly-evidenced"]
    assert again.status_code == 200  # the decision wasn't kept: it can be made now
    assert [(e["action"], e["verdict_after"]) for e in entries] == [("accept", "partly-evidenced")]


def test_two_reviews_at_once_make_one_decision_and_one_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = assess(client, alice)
        start = threading.Barrier(2)
        codes: list[int] = []

        def decide(decision: str) -> None:
            start.wait()
            codes.append(review(client, alice, thread_id, decision=decision).status_code)

        threads = [threading.Thread(target=decide, args=(d,)) for d in ("accept", "reject")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        entries = trail(client, alice)
        final = client.get(f"/api/assessments/{thread_id}", headers=alice).json()["assessment"]

    assert sorted(codes) == [200, 409]
    assert [e["action"] for e in entries] == [final["review"]["decision"]]


def test_no_route_updates_or_deletes_an_entry(users):
    alice, _ = users
    app = app_on(None)
    routes = api_routes(app)
    audit_routes = {(method, path) for method, path in routes if "audit" in path}
    with TestClient(app) as client:
        changed = [
            getattr(client, verb)("/api/audit-trail", headers=alice).status_code
            for verb in ("put", "patch", "delete")
        ]
        posted = client.post("/api/audit-trail", json={}, headers=alice).status_code

    assert ("POST", "/api/assessments/{thread_id}/review") in routes  # the listing sees routes
    assert audit_routes == {("GET", "/api/audit-trail")}
    assert {m for m, _ in routes} <= {"GET", "POST", "PUT"}  # no PATCH or DELETE anywhere
    assert {p for m, p in routes if m == "PUT"} == {
        "/api/developer/daily-limit",
        "/api/developer/model",
    }
    assert changed == [405, 405, 405]
    assert posted == 405


@needs_postgres
def test_the_table_refuses_an_update_a_delete_and_a_truncate(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url)) as client:
        review(client, alice, assess(client, alice), decision="accept")

    refused = []
    for statement in (
        f"UPDATE {AUDIT_TABLE} SET note = 'changed'",
        f"DELETE FROM {AUDIT_TABLE}",
        f"TRUNCATE {AUDIT_TABLE}",
    ):
        with postgres.connection() as conn, pytest.raises(psycopg.errors.RaiseException) as error:
            conn.execute(statement)
        refused.append("append-only" in str(error.value))

    assert refused == [True, True, True]
    with TestClient(app_on(postgres.url)) as client:
        assert [e["note"] for e in trail(client, alice)] == [""]
