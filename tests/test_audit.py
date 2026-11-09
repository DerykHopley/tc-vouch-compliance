"""The audit trail (#39): each step of a review and each override recorded, append-only, with
the step it records (Confirm findings, then Accept or Reject, #372).

Through the HTTP API, on the bundled fake model, on both backends: in memory (no database URL)
and Postgres (CI's `database` job gives a server through VOUCH_TEST_DATABASE_URL; without one
those cases are skipped). The user is set per request by a test header (test_memory's).
"""

import threading
import time
from dataclasses import fields, replace
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg
import pytest
import test_memory
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore
from reviewing import confirm
from test_auth import SECRET, FakeClerk, api_routes, bearer, clerk_app, token
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture)
from test_memory import DEMO_CONTROL, app_on, assess

from vouch import audit, load_named_profile
from vouch.api import compliance_profile
from vouch.audit import MAX_PAGE, AuditEntry, EvidenceVersion, Reviewer, review_entries
from vouch.citations import Citation
from vouch.database import (
    AUDIT_COLUMNS,
    AUDIT_TABLE,
    CREATE_AUDIT_GUARD,
    CREATE_AUDIT_TRIGGERS,
    lock_id,
)
from vouch.graph import (
    Assessment,
    RequirementVerdict,
    ReviewDecision,
    ShouldScore,
    Verdict,
    thread_key,
)
from vouch.isa import Column
from vouch.memory import Memory
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


def accept_with_m1(client: TestClient, headers: dict[str, str], thread_id: str, note: str = ""):
    """Findings confirmed with M1 overridden to evidenced, which leaves no remediation item, so
    the control is accepted straight after: the Accept's response."""
    confirmed = confirm(client, thread_id, [OVERRIDE_M1], headers)
    assert confirmed.status_code == 200, confirmed.text
    return review(client, headers, thread_id, decision="accept", note=note)


def trail(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/api/audit-trail", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def test_confirm_findings_with_one_override_writes_it_then_confirm_and_accept_writes_one(
    database_url, users
):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = assess(client, alice)
        confirmed = confirm(client, thread_id, [OVERRIDE_M1], alice)
        after_confirm = trail(client, alice)
        response = review(client, alice, thread_id, decision="accept", note="Accepted.")
        assert response.status_code == 200, response.text
        reviewed = response.json()["assessment"]
        entries = trail(client, alice)

    assert confirmed.status_code == 200, confirmed.text
    common = {
        "user_id": alice["X-Test-User"],
        "reviewer_name": "Local user",  # local mode's default user's name
        "profile_id": reviewed["profile_id"],
        "control_id": DEMO_CONTROL,
        "thread_id": thread_id,
        "evidence": AUTH_STANDARD,
        "model": reviewed["model"],  # the thread's own, as the run recorded them
        "prompt_version": reviewed["prompt_version"],
    }
    confirm_entry, override = after_confirm  # newest first: the override, then the confirm
    assert entries[1:] == after_confirm
    assert override == common | {
        "id": override["id"],
        "at": override["at"],
        "action": "override",
        "requirement_id": "M1",
        "note": "MFA is enforced.",
        "verdict_before": "partly-evidenced",
        "verdict_after": "evidenced",
    }
    assert confirm_entry == common | {
        "id": confirm_entry["id"],
        "at": confirm_entry["at"],
        "action": "confirm",
        "requirement_id": None,
        "note": "",
        "verdict_before": "partly-evidenced",  # Vouch's roll-up
        "verdict_after": "evidenced",  # with the override
    }
    accept = entries[0]
    assert accept == common | {
        "id": accept["id"],
        "at": accept["at"],
        "action": "accept",
        "requirement_id": None,
        "note": "Accepted.",
        "verdict_before": "evidenced",  # the confirmed roll-up, both
        "verdict_after": "evidenced",
    }
    assert reviewed["model"] and reviewed["prompt_version"]
    assert len({accept["id"], confirm_entry["id"], override["id"]}) == 3
    assert confirm_entry["at"] == override["at"] <= accept["at"]
    datetime.fromisoformat(accept["at"])


def test_a_review_in_local_mode_keeps_the_local_users_name(database_url, users):
    alice, _ = users
    app = app_on(database_url)
    app.state.settings = replace(app.state.settings, local_user_name="Dana Lead")
    with TestClient(app) as client:
        thread_id = assess(client, alice)
        accept_with_m1(client, alice, thread_id)
        entries = trail(client, alice)

    assert [(e["action"], e["reviewer_name"]) for e in entries] == [
        ("accept", "Dana Lead"),
        ("confirm", "Dana Lead"),
        ("override", "Dana Lead"),
    ]


# --- Clerk mode: the reviewer's name from Clerk's user lookup, Backend API faked over HTTP ------

ALICE = "user_alice"


def clerk_trail(clerk: FakeClerk, *decisions: str) -> tuple[list[int], list[dict[str, Any]]]:
    """Alice assesses and reviews once per decision in clerk mode; each review's status, and
    her trail. Her token is a real one, checked offline as #32 does."""
    signed_in = bearer(token(ALICE))
    with TestClient(clerk_app(clerk), raise_server_exceptions=False) as client:
        codes = [
            review(client, signed_in, assess(client, signed_in), decision=d).status_code
            for d in decisions
        ]
        return codes, trail(client, signed_in)


@pytest.fixture
def clerk() -> FakeClerk:
    audit.process_trail.cache_clear()  # Alice's trail is new to each test
    return FakeClerk({ALICE: {}})


def test_in_clerk_mode_the_name_comes_from_clerk_looked_up_once_per_cache_time(clerk):
    clerk.profiles[ALICE] = {"first_name": "Alice", "last_name": "Lindqvist"}

    codes, entries = clerk_trail(clerk, "reject", "reject")

    assert codes == [200, 200]
    assert [e["reviewer_name"] for e in entries] == ["Alice Lindqvist", "Alice Lindqvist"]
    assert [e["user_id"] for e in entries] == [ALICE, ALICE]
    assert [call.url.path for call in clerk.calls] == [f"/v1/users/{ALICE}"]


EMAILS = {
    "primary_email_address_id": "idn_2",
    "email_addresses": [
        {"id": "idn_1", "object": "email_address", "email_address": "old@nordlicht.example"},
        {
            "id": "idn_2",
            "object": "email_address",
            "email_address": "a.lindqvist@nordlicht.example",
        },
    ],
}
NAMES = {
    "first and last": (
        {"first_name": "Alice", "last_name": "Lindqvist"} | EMAILS,
        "Alice Lindqvist",
    ),
    "first only": ({"first_name": "Alice", "last_name": ""} | EMAILS, "Alice"),
    "last only": ({"first_name": None, "last_name": "Lindqvist"}, "Lindqvist"),
    "blank names: the primary e-mail's local part": (
        {"first_name": " ", "last_name": None} | EMAILS,
        "a.lindqvist",
    ),
    "no name, primary e-mail gone: the user id": (
        EMAILS | {"primary_email_address_id": "idn_gone"},
        ALICE,
    ),
    "nothing at all: the user id": ({}, ALICE),
}


@pytest.mark.parametrize(("profile", "name"), NAMES.values(), ids=NAMES.keys())
def test_the_name_falls_back_to_the_primary_emails_local_part_then_the_user_id(
    clerk, profile, name
):
    clerk.profiles[ALICE] = profile

    _, (entry,) = clerk_trail(clerk, "reject")

    assert entry["reviewer_name"] == name


def test_a_failing_name_lookup_still_records_the_review_under_the_user_id(clerk, caplog):
    clerk.fail = lambda request: httpx.Response(500, json={"errors": []})

    codes, entries = clerk_trail(clerk, "reject")

    assert codes == [200]
    assert [(e["action"], e["reviewer_name"]) for e in entries] == [("reject", ALICE)]
    assert "name lookup failed" in caplog.text
    assert SECRET not in caplog.text


def test_a_user_clerk_no_longer_knows_cannot_review(clerk):
    """A deleted user's token verifies until it expires: Clerk's 404 signs them out, as where
    the role is needed (#32)."""
    clerk.metadata.clear()

    codes, entries = clerk_trail(clerk, "reject")

    assert codes == [401]
    assert entries == []


def test_entries_are_read_per_profile_newest_first(database_url, users):
    alice, bob = users
    app = app_on(database_url)
    with TestClient(app) as client:
        first = assess(client, alice)
        review(client, alice, first, decision="reject", note="See the records folder.")
        second = assess(client, alice)
        accept_with_m1(client, alice, second)
        mine = trail(client, alice)
        bobs = trail(client, bob)
        another = load_named_profile(DEFAULT_PROFILE).model_copy(update={"id": "another-profile"})
        app.dependency_overrides[compliance_profile] = lambda: another  # M4 brings a real one
        other_profile = trail(client, alice)

    assert [(e["thread_id"], e["action"]) for e in mine] == [
        (second, "accept"),
        (second, "confirm"),
        (second, "override"),
        (first, "reject"),
    ]
    assert mine[0]["at"] >= mine[3]["at"]
    assert mine[3]["note"] == "See the records folder."
    assert bobs == []
    assert other_profile == []


def test_the_trail_is_read_a_page_at_a_time(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        first = assess(client, alice)
        review(client, alice, first, decision="reject")
        second = assess(client, alice)
        accept_with_m1(client, alice, second)
        page = client.get("/api/audit-trail?limit=2", headers=alice).json()
        middle = client.get(f"/api/audit-trail?limit=1&before={page['next']}", headers=alice)
        rest_from = middle.json()["next"]
        rest = client.get(f"/api/audit-trail?limit=2&before={rest_from}", headers=alice)
        refused = [
            client.get(f"/api/audit-trail?limit={n}", headers=alice).status_code
            for n in (0, MAX_PAGE + 1)
        ]
        whole = client.get(f"/api/audit-trail?limit={MAX_PAGE}", headers=alice).json()

    assert [e["action"] for e in page["entries"]] == ["accept", "confirm"]
    assert page["next"] == page["entries"][-1]["id"]
    assert [e["action"] for e in middle.json()["entries"]] == ["override"]
    assert [(e["thread_id"], e["action"]) for e in rest.json()["entries"]] == [(first, "reject")]
    assert rest.json()["next"] is None
    assert refused == [422, 422]
    assert [e["id"] for e in whole["entries"]] == [
        e["id"] for e in [*page["entries"], *middle.json()["entries"], *rest.json()["entries"]]
    ]
    assert whole["next"] is None


@pytest.mark.parametrize("where", ["note", "reason"])
def test_a_nul_in_the_note_or_a_reason_is_refused_on_every_backend(database_url, users, where):
    alice, _ = users
    with TestClient(app_on(database_url), raise_server_exceptions=False) as client:
        thread_id = assess(client, alice)
        if where == "note":
            refused = review(client, alice, thread_id, decision="reject", note="Fine.\x00")
        else:
            nul = OVERRIDE_M1 | {"reason": "MFA\x00 is on."}
            refused = confirm(client, thread_id, [nul], alice)
        still = client.get(f"/api/assessments/{thread_id}", headers=alice).json()["assessment"]
        entries = trail(client, alice)

    assert refused.status_code == 422
    assert still["status"] == "waiting-for-review"
    assert still["review_step"] == "confirm-findings"
    assert entries == []


@pytest.mark.parametrize("step", ["confirm", "reject"])
def test_entries_that_cannot_be_built_leave_no_step_taken(database_url, users, monkeypatch, step):
    alice, _ = users

    def broken(*args: Any) -> None:
        raise RuntimeError("an entry couldn't be built")

    with TestClient(app_on(database_url), raise_server_exceptions=False) as client:
        thread_id = assess(client, alice)
        if step == "confirm":
            monkeypatch.setattr(audit, "confirm_entries", broken)
            failed = confirm(client, thread_id, [OVERRIDE_M1], alice)
        else:
            monkeypatch.setattr(audit, "review_entries", broken)
            failed = review(client, alice, thread_id, decision="reject")
        monkeypatch.undo()
        still = client.get(f"/api/assessments/{thread_id}", headers=alice).json()["assessment"]
        entries = trail(client, alice)

    assert failed.status_code == 500
    assert still["status"] == "waiting-for-review"
    assert still["review_step"] == "confirm-findings"
    assert entries == []


@needs_postgres
def test_any_failing_trail_write_is_a_503_and_rolls_back(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        thread_id = assess(client, alice)
        with postgres.connection() as conn:  # the insert breaks a constraint
            conn.execute(f"ALTER TABLE {AUDIT_TABLE} ADD CONSTRAINT short CHECK (length(note) < 3)")
        failed = review(client, alice, thread_id, decision="reject", note="Too long a note.")
        still = client.get(f"/api/assessments/{thread_id}", headers=alice).json()["assessment"]

    assert failed.status_code == 503
    assert failed.json()["unavailable"] == "database"
    assert still["status"] == "waiting-for-review"


@needs_postgres
def test_a_review_of_a_thread_being_reviewed_is_refused_at_once(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        thread_id = assess(client, alice)
        scope = Memory(InMemoryStore(), alice["X-Test-User"], DEFAULT_PROFILE).scope
        with postgres.connection() as conn, conn.transaction():  # another review holds the lock
            conn.execute(
                "SELECT pg_advisory_xact_lock(%s)", (lock_id(thread_key(thread_id, scope)),)
            )
            started = time.monotonic()
            busy = confirm(client, thread_id, headers=alice)
            busy_review = review(client, alice, thread_id, decision="reject")
            waited = time.monotonic() - started
        after = confirm(client, thread_id, headers=alice)

    assert (busy.status_code, busy_review.status_code) == (409, 409)
    assert waited < 2
    assert after.status_code == 200


@needs_postgres
def test_a_failing_trail_write_rolls_back_confirm_findings_with_a_503(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        thread_id = assess(client, alice)
        with postgres.connection() as conn:  # the insert fails: its table is gone
            conn.execute(f"DROP TABLE {AUDIT_TABLE}")
        failed = confirm(client, thread_id, [OVERRIDE_M1], alice)
        still = client.get(f"/api/assessments/{thread_id}", headers=alice).json()["assessment"]
        postgres.setup()  # the table back, empty
        again = confirm(client, thread_id, [OVERRIDE_M1], alice)
        entries = trail(client, alice)

    assert failed.status_code == 503
    assert failed.json()["unavailable"] == "database"
    assert still["status"] == "waiting-for-review"
    assert still["review_step"] == "confirm-findings"
    assert still["confirmed"] is None
    assert [r["verdict"] for r in still["requirements"] if r["id"] == "M1"] == ["partly-evidenced"]
    assert again.status_code == 200  # the step wasn't kept: it can be taken now
    assert [(e["action"], e["verdict_after"]) for e in entries] == [
        ("confirm", "evidenced"),
        ("override", "evidenced"),
    ]


@needs_postgres
def test_a_failing_trail_write_rolls_back_the_decision(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        thread_id = assess(client, alice)
        confirm(client, thread_id, [OVERRIDE_M1], alice)
        with postgres.connection() as conn:  # the insert breaks a constraint
            conn.execute(f"ALTER TABLE {AUDIT_TABLE} ADD CONSTRAINT no CHECK (action != 'accept')")
        failed = review(client, alice, thread_id, decision="accept")
        still = client.get(f"/api/assessments/{thread_id}", headers=alice).json()["assessment"]

    assert failed.status_code == 503
    assert still["status"] == "waiting-for-review"
    assert still["review_step"] == "accept"
    assert still["review"] is None


def test_two_reviews_at_once_make_one_decision_and_one_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = assess(client, alice)
        confirm(client, thread_id, [OVERRIDE_M1], alice)  # no item left: Accept may go
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
    assert [e["action"] for e in entries] == [final["review"]["decision"], "confirm", "override"]


def test_two_confirm_findings_at_once_confirm_once(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = assess(client, alice)
        start = threading.Barrier(2)
        codes: list[int] = []

        def confirming() -> None:
            start.wait()
            codes.append(confirm(client, thread_id, headers=alice).status_code)

        threads = [threading.Thread(target=confirming) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        entries = trail(client, alice)

    assert sorted(codes) == [200, 409]
    assert [e["action"] for e in entries] == ["confirm"]


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

    assert audit_routes == {("GET", "/api/audit-trail")}
    assert changed == [405, 405, 405]
    assert posted == 405


def test_the_tables_columns_are_the_entrys_fields():
    assert ("id", *AUDIT_COLUMNS) == tuple(f.name for f in fields(AuditEntry))


def citation(doc_id: str, version: str) -> Citation:
    return Citation(
        doc_id, version, "1 Scope", "A quote of enough words.", "documented", False, None
    )


def test_the_decisions_evidence_is_what_the_control_verdict_rests_on():
    """Only must, high, very high and overlay requirements that apply count towards the control
    verdict (graph.roll_up): a should requirement's or a not-applicable one's documents don't."""

    def requirement(id_: str, column: Column, verdict: Verdict, doc: str) -> RequirementVerdict:
        return RequirementVerdict(id_, column, "Text.", verdict, "Why.", (citation(doc, "1"),))

    waiting = Assessment(
        control_id="4.1.2",
        profile_id=DEFAULT_PROFILE,
        verdict="evidenced",
        should_score=ShouldScore(1, 1),
        requirements=[
            requirement("M1", "must", "evidenced", "counted"),
            requirement("M2", "must", "not-applicable", "not-applicable-doc"),
            requirement("S1", "should", "evidenced", "should-doc"),
        ],
        answer="",
        thread_id="t",
    )
    decision = ReviewDecision(decision="accept")

    (entry,) = review_entries(
        waiting, decision, Reviewer("u", "U"), datetime(2026, 11, 5, tzinfo=UTC)
    )

    assert entry.evidence == (EvidenceVersion("counted", "1"),)


@needs_postgres
def test_the_table_refuses_an_update_a_delete_and_a_truncate(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url)) as client:
        review(client, alice, assess(client, alice), decision="reject")

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


# The table as #39's vouch-db-setup made it, before entries kept the reviewer's name.
CREATE_AUDIT_BEFORE_154 = (
    f"CREATE TABLE {AUDIT_TABLE} (id bigserial PRIMARY KEY, "
    "user_id text NOT NULL, at timestamptz NOT NULL, profile_id text NOT NULL, "
    "control_id text NOT NULL, thread_id text NOT NULL, action text NOT NULL "
    "CHECK (action IN ('accept', 'reject', 'override', 'approve', 'discard')), "
    "requirement_id text, note text NOT NULL, verdict_before text, verdict_after text, "
    "evidence jsonb NOT NULL, model text NOT NULL, prompt_version text NOT NULL)"
)


@needs_postgres
def test_setup_adds_the_name_to_an_existing_trail_keeping_its_rows_and_its_guard(request, users):
    alice, _ = users
    db = request.getfixturevalue("empty_database")  # not set up: made as #39's setup made it
    with db.connection() as conn:
        conn.execute(CREATE_AUDIT_BEFORE_154)
        conn.execute(CREATE_AUDIT_GUARD)
        for trigger in CREATE_AUDIT_TRIGGERS:
            conn.execute(trigger)
        conn.execute(
            f"INSERT INTO {AUDIT_TABLE} (user_id, at, profile_id, control_id, thread_id, action, "
            "note, evidence, model, prompt_version) VALUES (%s, now(), %s, '4.1.2', 't-old', "
            "'accept', 'Before #154.', '[]', 'fake', 'v1')",
            (alice["X-Test-User"], DEFAULT_PROFILE),
        )

    db.setup()
    db.setup()  # safe to run again
    with TestClient(app_on(db.url)) as client:
        accept_with_m1(client, alice, assess(client, alice))
        entries = trail(client, alice)

    thread = entries[0]["thread_id"]
    assert [(e["thread_id"], e["action"], e["reviewer_name"]) for e in entries] == [
        (thread, "accept", "Local user"),
        (thread, "confirm", "Local user"),  # the CHECK replaced, so `confirm` is kept (#372)
        (thread, "override", "Local user"),
        ("t-old", "accept", alice["X-Test-User"]),  # no way to know the name then: the user id
    ]
    refused = []
    for statement in (
        f"UPDATE {AUDIT_TABLE} SET reviewer_name = 'changed'",
        f"DELETE FROM {AUDIT_TABLE}",
        f"TRUNCATE {AUDIT_TABLE}",
    ):
        with (
            db.connection() as conn,
            pytest.raises(psycopg.errors.RaiseException) as error,
        ):
            conn.execute(statement)
        refused.append("append-only" in str(error.value))
    assert refused == [True, True, True]
    with db.connection() as conn:
        nullable = conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = 'reviewer_name'",
            (AUDIT_TABLE,),
        ).fetchone()
    assert nullable == {"is_nullable": "NO"}
