"""Remediation items the lead approves or discards (#43): kept per user and profile, each decision
with its audit entry (recorded or not at all), a past due date refused, a second approval no
duplicate, and the overview counting the open ones.

Through the HTTP API, on the bundled fake model, on both backends: in memory (no database URL)
and Postgres (CI's `database` job gives a server through VOUCH_TEST_DATABASE_URL; without one
those cases are skipped). The user is set per request by a test header (test_memory's).

The fake model's 4.1.2 run judges M1 partly evidenced and the rest evidenced, so it drafts one
item, for M1 (#212). The lead's overrides of M1 to evidenced (its draft dropped) and of S1 to not
evidenced give S1 a "not drafted" item to fill in (graph._reviewed_remediation), which is the item
these cases decide on.
"""

import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest
import test_memory
from fastapi.testclient import TestClient
from profiles_dir import SECOND, make_profiles
from test_auth import api_routes
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture: postgres's)
from test_memory import app_on, assess, restart
from test_profile_switch import app_with

from vouch import api, remediation
from vouch.api import PROFILE_HEADER
from vouch.database import AUDIT_TABLE, REMEDIATION_TABLE
from vouch.graph import MAX_DUE_DAYS, NO_OWNER, NOT_DRAFTED
from vouch.profile import DEFAULT_PROFILE

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

ITEM = "S1"
OVERRIDES = [
    {"requirement_id": "M1", "verdict": "evidenced", "reason": "The exposure record exists."},
    {"requirement_id": ITEM, "verdict": "not-evidenced", "reason": "No per-system list."},
]
OWNER = "IT security officer"


def later(days: int = 30) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def accepted_with_an_item(client: TestClient, user: dict[str, str]) -> str:
    """An accepted 4.1.2 assessment with M1 overridden to evidenced and S1 to not evidenced:
    its thread id."""
    thread_id = assess(client, user)
    response = client.post(
        f"/api/assessments/{thread_id}/review",
        json={"decision": "accept", "overrides": OVERRIDES},
        headers=user,
    )
    assert response.status_code == 200, response.text
    return thread_id


def decide(
    client: TestClient,
    user: dict[str, str],
    thread_id: str,
    requirement_id: str = ITEM,
    **body: Any,
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
        "requirement_id": ITEM,
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
        response = decide(
            client, alice, thread_id, decision="approve", owner_role=OWNER, due_date=due
        )
        shown = items(client, alice, thread_id)
        entry = trail(client, alice)[0]
        reviewed = client.get(f"/api/assessments/{thread_id}", headers=alice).json()["assessment"]

    approved = {
        "requirement_id": ITEM,
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
        "requirement_id": ITEM,
        "note": f"{NOT_DRAFTED} Owner: {OWNER}. Due {due}.",
        "verdict_before": "not-evidenced",
        "verdict_after": "not-evidenced",
        "evidence": [{"doc_id": "authentication-standard", "version": "3.1"}],  # S1's quotes
        "model": reviewed["model"],
        "prompt_version": reviewed["prompt_version"],
    }


def test_approve_with_only_an_owner_keeps_the_drafts_due_date(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        (draft,) = items(client, alice, thread_id)
        response = decide(client, alice, thread_id, decision="approve", owner_role=OWNER)

    assert response.status_code == 200, response.text
    assert response.json()["item"] == draft | {"status": "approved", "owner_role": OWNER}


def test_a_not_drafted_item_needs_an_owner_role_to_be_approved(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        refused = decide(client, alice, thread_id, decision="approve")
        placeholder = decide(client, alice, thread_id, decision="approve", owner_role=NO_OWNER)
        shown = items(client, alice, thread_id)

    assert (refused.status_code, placeholder.status_code) == (422, 422)
    assert refused.json()["detail"] == remediation.OWNER_NEEDED
    assert [i["status"] for i in shown] == ["draft"]


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
    assert [(e["action"], e["requirement_id"]) for e in entries[:1]] == [("discard", ITEM)]


def test_an_approved_item_stays_in_the_open_count(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        decide(client, alice, thread_id, decision="approve", owner_role=OWNER)
        assert open_items(client, alice) == 1


def test_a_due_date_in_the_past_is_refused_with_a_hint_and_nothing_is_kept(database_url, users):
    alice, _ = users
    yesterday = (date.today() - timedelta(days=2)).isoformat()  # past in every time zone
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        before = trail(client, alice)
        refused = decide(
            client, alice, thread_id, decision="approve", owner_role=OWNER, due_date=yesterday
        )
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
            client,
            alice,
            thread_id,
            decision="approve",
            owner_role=OWNER,
            due_date=date.today().isoformat(),
        )

    assert response.status_code == 200, response.text


def test_approving_twice_keeps_one_item_and_one_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        first = decide(client, alice, thread_id, decision="approve", owner_role=OWNER)
        second = decide(client, alice, thread_id, decision="approve", owner_role=OWNER)
        approvals = [e for e in trail(client, alice) if e["action"] == "approve"]

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()
    assert len(approvals) == 1


def test_a_second_approval_with_other_values_is_refused_naming_the_kept_ones(database_url, users):
    alice, _ = users
    due = later()
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        decide(client, alice, thread_id, decision="approve", owner_role=OWNER, due_date=due)
        other_owner = decide(
            client, alice, thread_id, decision="approve", owner_role="Someone else"
        )
        other_date = decide(
            client, alice, thread_id, decision="approve", owner_role=OWNER, due_date=later(40)
        )
        shown = items(client, alice, thread_id)
        approvals = [e for e in trail(client, alice) if e["action"] == "approve"]

    assert (other_owner.status_code, other_date.status_code) == (409, 409)
    assert other_owner.json()["detail"] == (
        f"This remediation item was approved already, with owner {OWNER} and due {due}."
    )
    assert [(i["owner_role"], i["due_date"]) for i in shown] == [(OWNER, due)]
    assert len(approvals) == 1


def test_repeating_an_approval_after_its_due_date_gives_the_kept_item(
    database_url, users, monkeypatch
):
    """#4: an existing decision is looked up before the due date is checked."""
    alice, _ = users
    due = date.today().isoformat()
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        first = decide(client, alice, thread_id, decision="approve", owner_role=OWNER, due_date=due)
        later_on = datetime.now(UTC) + timedelta(days=3)
        monkeypatch.setattr(remediation, "now", lambda: later_on)
        again = decide(client, alice, thread_id, decision="approve", owner_role=OWNER, due_date=due)
        approvals = [e for e in trail(client, alice) if e["action"] == "approve"]

    assert (first.status_code, again.status_code) == (200, 200)
    assert again.json() == first.json()
    assert len(approvals) == 1


def test_a_due_date_beyond_the_horizon_is_refused_with_a_hint(database_url, users):
    alice, _ = users
    too_far = (date.today() + timedelta(days=MAX_DUE_DAYS + 2)).isoformat()
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        refused = decide(
            client, alice, thread_id, decision="approve", owner_role=OWNER, due_date=too_far
        )
        within = decide(
            client,
            alice,
            thread_id,
            decision="approve",
            owner_role=OWNER,
            due_date=later(MAX_DUE_DAYS),
        )

    assert refused.status_code == 422
    assert refused.json()["detail"] == remediation.TOO_FAR.format(due=too_far, days=MAX_DUE_DAYS)
    assert within.status_code == 200, within.text


# The due date's bounds are calendar days current somewhere on Earth (#3): a lead west of UTC
# may still be on yesterday's date, one east of it on tomorrow's.
AT = datetime(2026, 11, 5, 5, 0, tzinfo=UTC)  # 2026-11-04 17:00 in UTC-12
LATE = datetime(2026, 11, 5, 13, 0, tzinfo=UTC)  # 2026-11-05 01:00 in UTC-12


@pytest.mark.parametrize(
    "at, due, refused",
    [
        (AT, "2026-11-04", False),  # still today in UTC-12
        (AT, "2026-11-03", True),
        (LATE, "2026-11-05", False),
        (LATE, "2026-11-04", True),  # past everywhere once UTC-12 reached the 5th
    ],
)
def test_a_due_date_is_past_only_when_it_is_past_everywhere(at, due, refused):
    if refused:
        with pytest.raises(remediation.DueDateError, match="has passed"):
            remediation.check_due(date.fromisoformat(due), at)
    else:
        remediation.check_due(date.fromisoformat(due), at)


def test_the_horizon_counts_from_the_latest_calendar_day_current_anywhere():
    at = datetime(2026, 11, 5, 11, 0, tzinfo=UTC)  # 2026-11-06 01:00 in UTC+14
    last = date(2026, 11, 6) + timedelta(days=MAX_DUE_DAYS)
    remediation.check_due(last, at)
    with pytest.raises(remediation.DueDateError, match="days away"):
        remediation.check_due(last + timedelta(days=1), at)


def test_two_approvals_at_once_keep_one_item_and_one_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        start = threading.Barrier(2)
        codes: list[int] = []

        def approve() -> None:
            start.wait()
            response = decide(client, alice, thread_id, decision="approve", owner_role=OWNER)
            codes.append(response.status_code)

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
        refused = decide(client, alice, thread_id, decision="approve", owner_role=OWNER)
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
            json={"decision": "reject", "overrides": OVERRIDES},
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
            conn.execute(f"ALTER TABLE {AUDIT_TABLE} ADD CONSTRAINT no CHECK (action != 'approve')")
        failed = decide(client, alice, thread_id, decision="approve", owner_role=OWNER)
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
    routes = {(method, path) for method, path in api_routes(app) if "remediation" in path}
    with TestClient(app) as client:
        thread_id = accepted_with_an_item(client, alice)
        url = f"/api/assessments/{thread_id}/remediation/{ITEM}"
        changed = [
            getattr(client, verb)(url, headers=alice).status_code for verb in ("put", "delete")
        ]

    assert routes == {("POST", "/api/assessments/{thread_id}/remediation/{requirement_id}")}
    assert changed == [405, 405]


def test_one_profiles_items_and_decisions_do_not_show_under_another(tmp_path, users):
    """#47: the profile comes from X-Vouch-Profile; items are kept per user and profile."""
    alice, _ = users
    nordlicht, testwerk = (alice | {PROFILE_HEADER: p} for p in ("nordlicht", SECOND))
    with app_with(make_profiles(tmp_path / "profiles")) as client:
        thread_id = accepted_with_an_item(client, nordlicht)
        approved = decide(client, nordlicht, thread_id, decision="approve", owner_role=OWNER)
        elsewhere = decide(client, testwerk, thread_id, decision="discard")
        opened_elsewhere = client.get(f"/api/assessments/{thread_id}", headers=testwerk)

        assert approved.status_code == 200, approved.text
        assert elsewhere.status_code == 404
        assert opened_elsewhere.status_code == 404
        assert open_items(client, nordlicht) == 1
        assert open_items(client, testwerk) == 0
        assert [i["status"] for i in items(client, nordlicht, thread_id)] == ["approved"]


@needs_postgres
def test_before_vouch_db_setup_adds_the_table_reads_see_no_decisions_and_writes_say_so(
    postgres, users, caplog
):
    """#2: a deploy onto a database set up before #43 keeps its Dossier; deciding waits for
    vouch-db-setup."""
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        thread_id = accepted_with_an_item(client, alice)
        with postgres.connection() as conn:
            conn.execute(f"DROP TABLE {REMEDIATION_TABLE}")
        overview = client.get("/api/overview", headers=alice)
        opened = client.get(f"/api/assessments/{thread_id}", headers=alice)
        refused = decide(client, alice, thread_id, decision="discard")

    assert overview.status_code == 200
    assert overview.json()["readiness"]["remediation_items"] == 1
    assert opened.status_code == 200
    assert [i["status"] for i in opened.json()["assessment"]["remediation"]] == ["draft"]
    assert refused.status_code == 503
    assert "run vouch-db-setup" in caplog.text


@needs_postgres
def test_the_items_table_refuses_an_update_a_delete_and_a_truncate(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url)) as client:
        thread_id = accepted_with_an_item(client, alice)
        decide(client, alice, thread_id, decision="discard")

    for statement in (
        f"UPDATE {REMEDIATION_TABLE} SET status = 'approved'",
        f"DELETE FROM {REMEDIATION_TABLE}",
        f"TRUNCATE {REMEDIATION_TABLE}",
    ):
        with postgres.connection() as conn, pytest.raises(psycopg.errors.RaiseException) as error:
            conn.execute(statement)
        assert "final" in str(error.value)
    with postgres.connection() as conn:
        kept = conn.execute(f"SELECT status FROM {REMEDIATION_TABLE}").fetchall()
    assert kept == [{"status": "discarded"}]


def test_the_overview_asks_only_for_the_counted_threads_discarded_items(database_url, users):
    """#9: the store answers for the threads named, discarded items only."""
    alice, _ = users
    app = app_on(database_url)
    with TestClient(app) as client:
        counted = accepted_with_an_item(client, alice)
        other = accepted_with_an_item(client, alice)
        decide(client, alice, counted, decision="discard")
        decide(client, alice, other, decision="discard")
        approved = accepted_with_an_item(client, alice)
        decide(client, alice, approved, decision="approve", owner_role=OWNER)
        store = api.remediation_items(app.state.database)
        user, profile = alice["X-Test-User"], DEFAULT_PROFILE

        assert store.discarded(user, profile, [counted, approved]) == {(counted, ITEM)}
        assert store.discarded(user, profile, []) == set()
