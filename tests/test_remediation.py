"""Remediation items the lead approves or discards (#43): kept per user and profile, each decision
with its audit entry (recorded or not at all), a past due date refused, a second approval no
duplicate, and the overview counting the open ones.

Through the HTTP API, on the bundled fake model, on both backends: in memory (no database URL)
and Postgres (CI's `database` job gives a server through VOUCH_TEST_DATABASE_URL; without one
those cases are skipped). The user is set per request by a test header (test_memory's).

The fake model's 4.1.2 run judges M1 partly evidenced and the rest evidenced, so it drafts no
item; the lead's override of M1 to not evidenced gives M1 a "not drafted" item to fill in
(graph._reviewed_remediation), which is the item these cases decide on.
"""

import threading
from datetime import date, timedelta
from typing import Any

import pytest
import test_memory
from fastapi.testclient import TestClient
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture: postgres's)
from test_memory import app_on, assess, restart

from vouch import remediation
from vouch.database import AUDIT_TABLE, REMEDIATION_TABLE
from vouch.graph import NO_OWNER, NOT_DRAFTED

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

M1_NOT_EVIDENCED = {
    "requirement_id": "M1",
    "verdict": "not-evidenced",
    "reason": "No record shows the exposure assessment.",
}
OWNER = "IT security officer"


def later(days: int = 30) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def accepted_with_an_item(client: TestClient, user: dict[str, str]) -> str:
    """An accepted 4.1.2 assessment with M1 overridden to not evidenced: its thread id."""
    thread_id = assess(client, user)
    response = client.post(
        f"/api/assessments/{thread_id}/review",
        json={"decision": "accept", "overrides": [M1_NOT_EVIDENCED]},
        headers=user,
    )
    assert response.status_code == 200, response.text
    return thread_id


def decide(
    client: TestClient, user: dict[str, str], thread_id: str, requirement_id: str = "M1", **body: Any
) -> Any:
    return client.post(
        f"/api/assessments/{thread_id}/remediation/{requirement_id}", json=body, headers=user
    )


def items(client: TestClient, user: dict[str, str], thread_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/api/assessments/{thread_id}", headers=user)
    assert response.status_code == 200, response.text
    return response.json()["assessment"]["remediation"]


def trail(client: TestClient, user: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/api/audit-trail", headers=user)
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def open_items(client: TestClient, user: dict[str, str]) -> int:
    response = client.get("/api/overview", headers=user)
    assert response.status_code == 200, response.text
    return response.json()["readiness"]["remediation_items"]


def test_a_reviewed_assessment_shows_its_items_as_drafts(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        shown = items(client, alice, thread_id)

    (item,) = shown
    assert item | {"due_date": ""} == {
        "requirement_id": "M1",
        "missing": NOT_DRAFTED,
        "owner_role": NO_OWNER,
        "due_date": "",
        "status": "draft",
    }


def test_approve_keeps_the_item_with_the_leads_owner_and_date_and_writes_one_entry(
    database_url, users
):
    alice, _ = users
    due = later()
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        response = decide(client, alice, thread_id, decision="approve", owner_role=OWNER, due_date=due)
        shown = items(client, alice, thread_id)
        entry = trail(client, alice)[0]
        reviewed = client.get(f"/api/assessments/{thread_id}", headers=alice).json()["assessment"]

    approved = {
        "requirement_id": "M1",
        "missing": NOT_DRAFTED,
        "owner_role": OWNER,
        "due_date": due,
        "status": "approved",
    }
    assert response.status_code == 200, response.text
    assert response.json() == {"item": approved}
    assert shown == [approved]
    assert entry | {"id": 0, "at": ""} == {
        "id": 0,
        "at": "",
        "user_id": alice["X-Test-User"],
        "reviewer_name": "Local user",
        "profile_id": reviewed["profile_id"],
        "control_id": "4.1.2",
        "thread_id": thread_id,
        "action": "approve",
        "requirement_id": "M1",
        "note": f"{NOT_DRAFTED} Owner: {OWNER}. Due {due}.",
        "verdict_before": "not-evidenced",
        "verdict_after": "not-evidenced",
        "evidence": [],
        "model": reviewed["model"],
        "prompt_version": reviewed["prompt_version"],
    }


def test_approve_without_changes_keeps_the_draft_as_it_was(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        (draft,) = items(client, alice, thread_id)
        response = decide(client, alice, thread_id, decision="approve")

    assert response.status_code == 200, response.text
    assert response.json()["item"] == draft | {"status": "approved"}


def test_discard_takes_the_item_out_of_the_open_count_and_writes_one_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        before = open_items(client, alice)
        response = decide(client, alice, thread_id, decision="discard")
        after = open_items(client, alice)
        shown = items(client, alice, thread_id)
        entries = trail(client, alice)

    assert response.status_code == 200, response.text
    assert (before, after) == (1, 0)
    assert [i["status"] for i in shown] == ["discarded"]
    assert [(e["action"], e["requirement_id"]) for e in entries[:1]] == [("discard", "M1")]


def test_an_approved_item_stays_in_the_open_count(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        decide(client, alice, thread_id, decision="approve")
        assert open_items(client, alice) == 1


def test_a_due_date_in_the_past_is_refused_with_a_hint_and_nothing_is_kept(database_url, users):
    alice, _ = users
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        before = trail(client, alice)
        refused = decide(client, alice, thread_id, decision="approve", due_date=yesterday)
        shown = items(client, alice, thread_id)
        after = trail(client, alice)

    assert refused.status_code == 422
    assert refused.json()["detail"] == remediation.PAST_DUE.format(due=yesterday)
    assert [i["status"] for i in shown] == ["draft"]
    assert after == before


def test_a_due_date_of_today_is_accepted(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        response = decide(
            client, alice, thread_id, decision="approve", due_date=date.today().isoformat()
        )

    assert response.status_code == 200, response.text


def test_approving_twice_keeps_one_item_and_one_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        first = decide(client, alice, thread_id, decision="approve", owner_role=OWNER)
        second = decide(client, alice, thread_id, decision="approve", owner_role="Someone else")
        shown = items(client, alice, thread_id)
        approvals = [e for e in trail(client, alice) if e["action"] == "approve"]

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()  # the kept item, not the second request's
    assert [i["owner_role"] for i in shown] == [OWNER]
    assert len(approvals) == 1


def test_two_approvals_at_once_keep_one_item_and_one_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        start = threading.Barrier(2)
        codes: list[int] = []

        def approve() -> None:
            start.wait()
            codes.append(decide(client, alice, thread_id, decision="approve").status_code)

        threads = [threading.Thread(target=approve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        approvals = [e for e in trail(client, alice) if e["action"] == "approve"]

    assert codes == [200, 200]
    assert len(approvals) == 1


def test_a_decided_item_cannot_be_decided_the_other_way(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        decide(client, alice, thread_id, decision="discard")
        refused = decide(client, alice, thread_id, decision="approve")
        shown = items(client, alice, thread_id)
        actions = [e["action"] for e in trail(client, alice)]

    assert refused.status_code == 409
    assert "discarded" in refused.json()["detail"]
    assert [i["status"] for i in shown] == ["discarded"]
    assert actions.count("approve") == 0


def test_items_of_a_control_not_accepted_wait_for_the_review(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        waiting = assess(client, alice)
        rejected = assess(client, alice)
        client.post(
            f"/api/assessments/{rejected}/review",
            json={"decision": "reject", "overrides": [M1_NOT_EVIDENCED]},
            headers=alice,
        )
        codes = [
            decide(client, alice, thread_id, decision="approve").status_code
            for thread_id in (waiting, rejected)
        ]
        entries = trail(client, alice)

    assert codes == [409, 409]
    assert [e["action"] for e in entries if e["action"] in ("approve", "discard")] == []


@pytest.mark.parametrize("thread", ["unknown", "bobs"])
def test_an_unknown_or_another_users_assessment_is_a_404(database_url, users, thread):
    alice, bob = users
    with TestClient(app_on(database_url)) as client:
        bobs = accepted_with_an_item(client, bob)
        thread_id = "no-such-thread" if thread == "unknown" else bobs
        response = decide(client, alice, thread_id, decision="approve")
        bobs_items = items(client, bob, bobs)

    assert response.status_code == 404
    assert [i["status"] for i in bobs_items] == ["draft"]


def test_a_requirement_with_no_item_is_a_404(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        response = decide(client, alice, thread_id, "M2", decision="approve")  # evidenced

    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "keep"},
        {"decision": "approve", "owner_role": "  "},
        {"decision": "approve", "owner_role": "CISO\x00"},
        {"decision": "approve", "due_date": "next week"},
    ],
    ids=["decision", "blank-owner", "nul-owner", "date"],
)
def test_a_malformed_decision_is_refused_on_every_backend(database_url, users, body):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        refused = decide(client, alice, thread_id, **body)
        shown = items(client, alice, thread_id)

    assert refused.status_code == 422
    assert [i["status"] for i in shown] == ["draft"]


def test_each_user_keeps_their_own_decisions(database_url, users):
    alice, bob = users
    with TestClient(app_on(database_url)) as client:
        alices = accepted_with_an_item(client, alice)
        bobs = accepted_with_an_item(client, bob)
        decide(client, alice, alices, decision="discard")

        assert open_items(client, alice) == 0
        assert open_items(client, bob) == 1
        assert [i["status"] for i in items(client, bob, bobs)] == ["draft"]


@needs_postgres
def test_decisions_outlast_a_restart_on_postgres(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        decide(client, alice, thread_id, decision="approve", owner_role=OWNER)

    restart()
    with TestClient(app_on(postgres.url)) as client:
        shown = items(client, alice, thread_id)

    assert [(i["status"], i["owner_role"]) for i in shown] == [("approved", OWNER)]


@needs_postgres
def test_a_failing_trail_write_keeps_no_item(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        thread_id = accepted_with_an_item(client, alice)
        with postgres.connection() as conn:  # the entry breaks a constraint
            conn.execute(f"ALTER TABLE {AUDIT_TABLE} ADD CONSTRAINT short CHECK (length(note) < 3)")
        failed = decide(client, alice, thread_id, decision="approve")
        shown = items(client, alice, thread_id)
        with postgres.connection() as conn:
            kept = conn.execute(f"SELECT count(*) AS n FROM {REMEDIATION_TABLE}").fetchone()

    assert failed.status_code == 503
    assert failed.json()["unavailable"] == "database"
    assert [i["status"] for i in shown] == ["draft"]
    assert kept == {"n": 0}


def test_no_route_lists_changes_or_removes_a_decision_but_the_decision_route(users):
    alice, _ = users
    app = app_on(None)
    paths = {route.path for route in app.routes if "remediation" in getattr(route, "path", "")}
    with TestClient(app) as client:
        thread_id = accepted_with_an_item(client, alice)
        url = f"/api/assessments/{thread_id}/remediation/M1"
        changed = [getattr(client, verb)(url, headers=alice).status_code for verb in ("put", "delete")]

    assert paths == {"/api/assessments/{thread_id}/remediation/{requirement_id}"}
    assert changed == [405, 405]
