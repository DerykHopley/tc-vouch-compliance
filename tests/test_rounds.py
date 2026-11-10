"""Review rounds (#402): the lead starts and closes a round (the first opens on its own), each
close freezes the round's results, and the overview compares now with the last closed round.

`compare` and `freeze` directly; the stores (vouch.rounds' ProcessRounds and PostgresRounds);
then through the HTTP API on both backends: in memory (no database URL) and Postgres (CI's
`database` job gives a server through VOUCH_TEST_DATABASE_URL; without one those cases are
skipped). The user is set per request by a test header (test_memory's).
"""

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
import test_memory
from company_setup import MUSTERFIRMA, MUSTERFIRMA_ID
from fastapi.testclient import TestClient
from reviewing import accepted
from test_audit import _audit_table_shape
from test_auth import FakeClerk, clerk_app
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture)
from test_dossier import seeded
from test_memory import DEMO_CONTROL, app_on, assess
from test_report import built_report
from test_report_pdf import text

from vouch import api, report, report_pdf, rounds
from vouch.audit import AuditEntry, ProcessTrail, Reviewer
from vouch.database import (
    AUDIT_TABLE,
    CREATE_AUDIT_GUARD,
    CREATE_AUDIT_TRIGGERS,
    ROUNDS_TABLE,
    SETUP_TABLES,
    Database,
)
from vouch.dossier import CardStatus, readiness
from vouch.graph import Verdict
from vouch.isa import load_base_framework
from vouch.profile import DEFAULT_PROFILE
from vouch.rounds import FrozenControl, compare, freeze

# test_memory's fixtures: each backend (memory, Postgres), a set-up Postgres database, two users.
database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

CONTROLS = load_base_framework().controls
IDS = [c.id for c in CONTROLS]
AT = datetime(2026, 11, 9, 10, 30, tzinfo=UTC)
OTHER_CONTROL = "5.1.1"  # one the fake model assesses besides DEMO_CONTROL


def frozen_as(statuses: dict[str, tuple[CardStatus, Verdict | None]]) -> list[FrozenControl]:
    """Frozen results: each control not assessed but those named (status, verdict)."""
    return [
        FrozenControl(i, *statuses[i], f"t-{i}")
        if i in statuses
        else FrozenControl(i, "not-assessed", None, None)
        for i in IDS
    ]


# --- compare and freeze ------------------------------------------------------------------------


def test_compare_counts_accepted_verdicts_only_and_lists_each_changed_control_in_order():
    a, b, c, d = IDS[:4]
    frozen = frozen_as(
        {
            a: ("waiting-for-review", "evidenced"),  # proposed then, accepted now
            b: ("accepted", "partly-evidenced"),  # accepted then, rejected now
            c: ("accepted", "evidenced"),  # unchanged
            d: ("rejected", "not-evidenced"),  # not accepted then or now
        }
    )
    latest = {
        a: seeded(a, "evidenced", "accepted"),
        b: seeded(b, "partly-evidenced", "rejected"),
        c: seeded(c, "evidenced", "accepted"),
        d: seeded(d, "not-evidenced", "waiting-for-review"),
    }

    shown = compare(frozen, CONTROLS, latest)

    assert shown["accepted"] == {"now": 2, "then": 2}
    assert shown["by_verdict"] == {
        "evidenced": {"now": 2, "then": 1},
        "partly-evidenced": {"now": 0, "then": 1},
        "not-evidenced": {"now": 0, "then": 0},
        "not-applicable": {"now": 0, "then": 0},
    }
    assert shown["changed"] == [
        {"control_id": a, "before": None, "after": "evidenced"},
        {"control_id": b, "before": "partly-evidenced", "after": None},
    ]


def test_now_is_the_overviews_readiness():
    a, b = IDS[:2]
    latest = {
        a: seeded(a, "evidenced", "accepted"),
        b: seeded(b, "not-applicable", "accepted"),
        "9.9.9": seeded("9.9.9", "evidenced", "accepted"),  # not listed: not counted
    }

    shown = compare(frozen_as({}), CONTROLS, latest)
    standing = readiness(CONTROLS, latest)

    assert shown["accepted"]["now"] == standing["accepted"] == 2
    assert {v: n["now"] for v, n in shown["by_verdict"].items()} == standing["by_verdict"]


def test_a_control_frozen_but_no_longer_listed_is_left_out_and_a_new_one_reads_none_then():
    gone = FrozenControl("9.9.9", "accepted", "evidenced", "t-gone")
    listed = CONTROLS[:2]
    new = listed[1].id
    frozen = [FrozenControl(listed[0].id, "not-assessed", None, None), gone]  # `new` not frozen
    latest = {new: seeded(new, "evidenced", "accepted")}

    shown = compare(frozen, listed, latest)

    assert shown["changed"] == [{"control_id": new, "before": None, "after": "evidenced"}]
    # Counted as `changed` lists: the control no longer listed counts neither then nor now.
    assert shown["accepted"] == {"now": 1, "then": 0}
    assert shown["by_verdict"]["evidenced"] == {"now": 1, "then": 0}


def test_nothing_changed_lists_nothing():
    a = IDS[0]
    frozen = frozen_as({a: ("accepted", "evidenced")})

    shown = compare(frozen, CONTROLS, {a: seeded(a, "evidenced", "accepted")})

    assert shown["changed"] == []
    assert shown["accepted"] == {"now": 1, "then": 1}


def test_freeze_keeps_one_frozen_control_per_control_with_its_state_verdict_and_thread():
    a, b = IDS[:2]
    latest = {
        a: replace(seeded(a, "evidenced", "accepted"), thread_id="t-a"),
        b: replace(seeded(b, "partly-evidenced", "waiting-for-review"), thread_id="t-b"),
    }

    frozen = freeze(CONTROLS, latest)

    assert [f.control_id for f in frozen] == IDS
    assert frozen[0] == FrozenControl(a, "accepted", "evidenced", "t-a")
    assert frozen[1] == FrozenControl(b, "waiting-for-review", "partly-evidenced", "t-b")
    assert all(f == FrozenControl(f.control_id, "not-assessed", None, None) for f in frozen[2:])


# --- The stores ----------------------------------------------------------------------------


@pytest.fixture(params=["process", pytest.param("postgres", marks=needs_postgres)])
def store(request) -> rounds.ReviewRounds:
    if request.param == "process":
        kept = ProcessTrail()
        return rounds.ProcessRounds(lambda: kept)
    return rounds.PostgresRounds(request.getfixturevalue("postgres"))


def reviewer(user: dict[str, str]) -> Reviewer:
    return Reviewer(user["X-Test-User"], "Dana Lead")


def test_with_no_round_kept_the_current_one_is_the_first_open_since_the_start(store, users):
    alice, _ = users

    current = store.current(alice["X-Test-User"], DEFAULT_PROFILE)

    assert current == rounds.Round("First round", None, None)
    assert store.last_closed(alice["X-Test-User"], DEFAULT_PROFILE, None) is None


def test_a_closed_round_is_read_back_frozen_and_a_started_one_is_current(store, users):
    alice, bob = users
    a = alice["X-Test-User"]
    frozen = frozen_as({IDS[0]: ("accepted", "evidenced")})

    closed = rounds.close(
        store, frozen, reviewer=reviewer(alice), profile_id=DEFAULT_PROFILE, clock=lambda: AT
    )
    nothing_open = store.current(a, DEFAULT_PROFILE)
    later = AT + timedelta(hours=1)
    rounds.start(
        store, "Q4 2026", reviewer=reviewer(alice), profile_id=DEFAULT_PROFILE, clock=lambda: later
    )

    assert (closed.name, closed.closed_at) == ("First round", AT)
    assert nothing_open is None
    assert store.current(a, DEFAULT_PROFILE) == rounds.Round("Q4 2026", later, None)
    back = store.last_closed(a, DEFAULT_PROFILE, None)
    assert back is not None
    assert (back.name, back.closed_at, list(back.results)) == ("First round", AT, frozen)
    assert store.current(bob["X-Test-User"], DEFAULT_PROFILE) == rounds.FIRST


class FailingTrail(ProcessTrail):
    """A trail whose write fails, to show a round is kept only with its entry."""

    def keep(self, entries: list[AuditEntry]) -> None:
        raise RuntimeError("the trail could not be written")


def test_in_process_a_failing_entry_leaves_the_round_open_and_no_round_started(users):
    alice, _ = users
    a = alice["X-Test-User"]
    working = ProcessTrail()
    failing = FailingTrail()
    trail = working
    store = rounds.ProcessRounds(lambda: trail)
    who = {"reviewer": reviewer(alice), "profile_id": DEFAULT_PROFILE}

    trail = failing
    with pytest.raises(RuntimeError):
        rounds.close(store, [], clock=lambda: AT, **who)
    assert store.current(a, DEFAULT_PROFILE) == rounds.FIRST  # still open
    trail = working
    rounds.close(store, [], clock=lambda: AT, **who)
    rounds.start(store, "Q4 2026", clock=lambda: AT + timedelta(hours=1), **who)
    trail = failing
    with pytest.raises(RuntimeError):
        rounds.close(store, [], clock=lambda: AT + timedelta(hours=2), **who)
    assert store.current(a, DEFAULT_PROFILE) == rounds.Round(
        "Q4 2026", AT + timedelta(hours=1), None
    )
    trail = working
    rounds.close(store, [], clock=lambda: AT + timedelta(hours=3), **who)
    trail = failing
    with pytest.raises(RuntimeError):
        rounds.start(store, "Q1 2027", **who)
    assert store.current(a, DEFAULT_PROFILE) is None


def test_start_while_one_is_open_and_close_with_none_open_are_refused(store, users):
    alice, _ = users
    who = {"reviewer": reviewer(alice), "profile_id": DEFAULT_PROFILE}

    with pytest.raises(rounds.RoundOpen):
        rounds.start(store, "Too soon", **who)  # the first round is open
    rounds.close(store, [], **who)
    with pytest.raises(rounds.NoRoundOpen):
        rounds.close(store, [], **who)


def test_the_last_closed_round_is_the_newest_closed_since_the_given_time(store, users):
    alice, _ = users
    a = alice["X-Test-User"]
    who = {"reviewer": reviewer(alice), "profile_id": DEFAULT_PROFILE}
    rounds.close(store, [], clock=lambda: AT, **who)
    rounds.start(store, "Q4 2026", clock=lambda: AT + timedelta(hours=1), **who)
    second = rounds.close(store, [], clock=lambda: AT + timedelta(hours=2), **who)
    assert second == rounds.Round("Q4 2026", AT + timedelta(hours=1), AT + timedelta(hours=2))

    newest = store.last_closed(a, DEFAULT_PROFILE, None)
    since_between = store.last_closed(a, DEFAULT_PROFILE, AT + timedelta(minutes=1))
    since_after = store.last_closed(a, DEFAULT_PROFILE, AT + timedelta(hours=3))

    assert newest is not None and newest.name == "Q4 2026"
    assert since_between is not None and since_between.name == "Q4 2026"
    assert since_after is None


# --- Through the API -------------------------------------------------------------------------


def overview_rounds(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/overview", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["rounds"]


def trail(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/api/audit-trail", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def close(client: TestClient, headers: dict[str, str]) -> Any:
    return client.post("/api/rounds/close", headers=headers)


def start(client: TestClient, headers: dict[str, str], name: str) -> Any:
    return client.post("/api/rounds", json={"name": name}, headers=headers)


def kept_rounds(url: Any, user: dict[str, str]) -> rounds.ReviewRounds:
    """The rounds store the app reads, read the way another process would."""
    if url is None:
        return rounds.process_rounds()

    return rounds.PostgresRounds(Database(url))


def test_with_no_round_kept_the_overview_shows_the_first_round_and_no_comparison(
    database_url, users
):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        shown = overview_rounds(client, alice)

    assert shown == {"current": {"name": "First round", "opened_at": None}, "comparison": None}


def test_closing_the_first_round_answers_it_writes_one_entry_and_leaves_none_open(
    database_url, users
):
    alice, _ = users
    before = datetime.now(UTC)
    with TestClient(app_on(database_url)) as client:
        closed = close(client, alice)
        shown = overview_rounds(client, alice)
        entries = trail(client, alice)

    assert closed.status_code == 200, closed.text
    assert closed.json()["closed"]["name"] == "First round"
    assert before <= datetime.fromisoformat(closed.json()["closed"]["closed_at"])
    assert shown["current"] is None
    (entry,) = entries
    assert entry == {
        "id": entry["id"],
        "at": closed.json()["closed"]["closed_at"],
        "user_id": alice["X-Test-User"],
        "reviewer_name": "Local user",  # local mode's default user's name
        "profile_id": DEFAULT_PROFILE,
        "control_id": "",
        "thread_id": "",
        "action": "round-close",
        "requirement_id": None,
        "note": "",
        "verdict_before": None,
        "verdict_after": None,
        "evidence": [],
        "model": "",
        "prompt_version": "",
        "goal_before": None,
        "goal_after": None,
        "round_name": "First round",
    }


def test_a_close_freezes_each_control_as_it_stands_and_later_work_leaves_it_so(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        accepted_thread = assess(client, alice)
        accepted(client, accepted_thread, headers=alice)
        proposed_thread = assess(client, alice, OTHER_CONTROL)
        assert close(client, alice).status_code == 200
        store = kept_rounds(database_url, alice)
        at_close = store.last_closed(alice["X-Test-User"], DEFAULT_PROFILE, None)
        # Later: a remediation decision, the proposed control accepted, a fresh start.
        shown = client.get(f"/api/assessments/{proposed_thread}", headers=alice).json()
        drafts = [i for i in shown["assessment"]["remediation"] if i["status"] == "draft"]
        assert drafts  # so accepting it below takes a remediation decision first
        accepted(client, proposed_thread, headers=alice)
        assert client.post("/api/progress/fresh-start", headers=alice).status_code == 200
        after = store.last_closed(alice["X-Test-User"], DEFAULT_PROFILE, None)

    assert at_close is not None and after is not None
    by_id = {f.control_id: f for f in at_close.results}
    assert [f.control_id for f in at_close.results] == IDS
    assert by_id[DEMO_CONTROL].status == "accepted"
    assert by_id[DEMO_CONTROL].thread_id == accepted_thread
    assert by_id[DEMO_CONTROL].verdict is not None
    assert by_id[OTHER_CONTROL] == FrozenControl(
        OTHER_CONTROL, "waiting-for-review", by_id[OTHER_CONTROL].verdict, proposed_thread
    )
    assert by_id[OTHER_CONTROL].verdict is not None
    assert all(
        f.status == "not-assessed" and f.verdict is None and f.thread_id is None
        for f in at_close.results
        if f.control_id not in (DEMO_CONTROL, OTHER_CONTROL)
    )
    assert after == at_close


def test_starting_a_round_opens_it_with_one_entry_and_names_it_on_the_overview(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        close(client, alice)
        started = start(client, alice, "  Q4 2026 ")
        shown = overview_rounds(client, alice)
        entries = trail(client, alice)

    assert started.status_code == 200, started.text
    opened = started.json()["current"]
    assert opened["name"] == "Q4 2026"
    assert shown["current"] == opened
    assert datetime.fromisoformat(opened["opened_at"]) == datetime.fromisoformat(entries[0]["at"])
    assert [(e["action"], e["round_name"], e["control_id"]) for e in entries] == [
        ("round-start", "Q4 2026", ""),
        ("round-close", "First round", ""),
    ]


def test_starting_while_a_round_is_open_is_a_409_and_writes_nothing(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        first_open = start(client, alice, "Too soon")
        close(client, alice)
        start(client, alice, "Q4 2026")
        second_open = start(client, alice, "Q1 2027")
        shown = overview_rounds(client, alice)
        entries = trail(client, alice)

    for refused in (first_open, second_open):
        assert refused.status_code == 409
        assert (
            refused.json()["detail"] == "Close the current review round before starting the next."
        )
    assert shown["current"]["name"] == "Q4 2026"
    assert [e["action"] for e in entries] == ["round-start", "round-close"]


def test_closing_with_none_open_is_a_409_and_writes_nothing(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        close(client, alice)
        refused = close(client, alice)
        entries = trail(client, alice)

    assert refused.status_code == 409
    assert refused.json()["detail"] == "No review round is open."
    assert [e["action"] for e in entries] == ["round-close"]


@pytest.mark.parametrize(
    "send",
    [{"json": {"name": ""}}, {"json": {"name": "   "}}, {"json": {"name": "x" * 61}}, {}],
    ids=["empty", "blank", "61-characters", "no-body"],
)
def test_a_blank_or_longer_name_is_a_422_and_writes_nothing(database_url, users, send):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        close(client, alice)
        refused = client.post("/api/rounds", headers=alice, **send)
        shown = overview_rounds(client, alice)
        entries = trail(client, alice)

    assert refused.status_code == 422
    assert shown["current"] is None
    assert [e["action"] for e in entries] == ["round-close"]


def test_a_name_of_60_characters_is_taken(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        close(client, alice)
        started = start(client, alice, "x" * 60)

    assert started.status_code == 200, started.text


def test_the_comparison_is_with_the_newest_closed_round_and_names_what_changed(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        close(client, alice)  # First round: nothing accepted
        start(client, alice, "Q4 2026")
        thread_id = assess(client, alice)
        verdict = accepted(client, thread_id, headers=alice)["verdict"]
        between = overview_rounds(client, alice)["comparison"]
        close(client, alice)  # Q4 2026: DEMO_CONTROL accepted
        start(client, alice, "Q1 2027")
        newest = overview_rounds(client, alice)["comparison"]

    assert between["against"]["name"] == "First round"
    assert between["accepted"] == {"now": 1, "then": 0}
    assert between["by_verdict"][verdict] == {"now": 1, "then": 0}
    assert between["changed"] == [{"control_id": DEMO_CONTROL, "before": None, "after": verdict}]
    assert newest["against"]["name"] == "Q4 2026"
    assert newest["accepted"] == {"now": 1, "then": 1}
    assert newest["changed"] == []


def test_a_round_closed_before_the_fresh_start_is_not_compared_with(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        close(client, alice)
        start(client, alice, "Q4 2026")
        assert client.post("/api/progress/fresh-start", headers=alice).status_code == 200
        shown = overview_rounds(client, alice)

    assert shown["comparison"] is None
    assert shown["current"]["name"] == "Q4 2026"  # the open round stays open


def test_rounds_are_kept_per_user_and_profile(database_url, users):
    alice, bob = users
    other = alice | {"X-Vouch-Profile": "alpenwerk"}
    with TestClient(app_on(database_url)) as client:
        close(client, alice)
        bobs = overview_rounds(client, bob)
        under_other = overview_rounds(client, other)

    assert bobs["current"] == {"name": "First round", "opened_at": None}
    assert under_other["current"] == {"name": "First round", "opened_at": None}


@pytest.mark.parametrize("path", ["/api/rounds", "/api/rounds/close"])
def test_signed_out_a_start_or_close_is_a_401(path):
    with TestClient(clerk_app(FakeClerk({}))) as client:
        response = client.post(path, json={"name": "Q4 2026"})

    assert response.status_code == 401


# --- Postgres: one transaction, turns on the lock, before setup --------------------------------


@needs_postgres
@pytest.mark.parametrize("step", ["close", "start"])
def test_a_failing_trail_insert_is_a_503_and_leaves_the_rounds_as_they_were(postgres, users, step):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        if step == "start":
            close(client, alice)
        before = overview_rounds(client, alice)
        with postgres.connection() as conn:  # the entry's insert breaks a constraint
            conn.execute(
                f"ALTER TABLE {AUDIT_TABLE} ADD CONSTRAINT no_rounds "
                "CHECK (action NOT IN ('round-start', 'round-close')) NOT VALID"
            )
        failed = close(client, alice) if step == "close" else start(client, alice, "Q4 2026")
        after = overview_rounds(client, alice)

    assert failed.status_code == 503
    assert failed.json()["unavailable"] == "database"
    assert after == before


@needs_postgres
def test_two_closes_at_once_close_one_round_and_write_one_entry(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url)) as client:
        go = threading.Barrier(2)
        codes: list[int] = []

        def closing() -> None:
            go.wait()
            codes.append(close(client, alice).status_code)

        threads = [threading.Thread(target=closing) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        entries = trail(client, alice)

    assert sorted(codes) == [200, 409]
    assert [(e["action"], e["round_name"]) for e in entries] == [("round-close", "First round")]
    with postgres.connection() as conn:
        kept = conn.execute(f"SELECT count(*) AS n FROM {ROUNDS_TABLE}").fetchone()
    assert kept is not None and kept["n"] == 1


@needs_postgres
def test_two_starts_at_once_open_one_round(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url)) as client:
        close(client, alice)
        go = threading.Barrier(2)
        codes: list[int] = []

        def starting(name: str) -> None:
            go.wait()
            codes.append(start(client, alice, name).status_code)

        threads = [threading.Thread(target=starting, args=(n,)) for n in ("A", "B")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        entries = trail(client, alice)

    assert sorted(codes) == [200, 409]
    assert [e["action"] for e in entries] == ["round-start", "round-close"]


@needs_postgres
def test_before_setup_made_the_rounds_table_the_first_round_shows_and_a_close_is_a_503(
    postgres, users, caplog
):
    alice, _ = users
    with postgres.connection() as conn:  # a deploy that adds the table, before setup runs
        conn.execute(f"DROP TABLE {ROUNDS_TABLE}")
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        shown = overview_rounds(client, alice)
        failed = close(client, alice)
        entries = trail(client, alice)

    assert shown == {"current": {"name": "First round", "opened_at": None}, "comparison": None}
    assert failed.status_code == 503
    assert failed.json()["unavailable"] == "database"
    assert entries == []
    assert f"no {ROUNDS_TABLE} table" in caplog.text


def test_setup_makes_the_rounds_table_and_health_checks_it():
    assert ROUNDS_TABLE in SETUP_TABLES


CREATE_AUDIT_BEFORE_402 = (
    f"CREATE TABLE {AUDIT_TABLE} (id bigserial PRIMARY KEY, "
    "user_id text NOT NULL, reviewer_name text NOT NULL, at timestamptz NOT NULL, "
    "profile_id text NOT NULL, control_id text NOT NULL, thread_id text NOT NULL, "
    "action text NOT NULL "
    "CHECK (action IN ('accept', 'reject', 'override', 'confirm', 'approve', 'discard', 'goal')), "
    "requirement_id text, note text NOT NULL, verdict_before text, verdict_after text, "
    "evidence jsonb NOT NULL, model text NOT NULL, prompt_version text NOT NULL, "
    "goal_before text, goal_after text)"
)
"""The trail as #369's setup made it."""


@needs_postgres
def test_setup_lets_a_trail_made_before_402_keep_round_entries_and_changes_nothing_on_a_second_run(
    request, users
):
    alice, _ = users
    db = request.getfixturevalue("empty_database")  # not set up: made as #369's setup made it
    with db.connection() as conn:
        conn.execute(CREATE_AUDIT_BEFORE_402)
        conn.execute(CREATE_AUDIT_GUARD)
        for trigger in CREATE_AUDIT_TRIGGERS:
            conn.execute(trigger)
        conn.execute(
            f"INSERT INTO {AUDIT_TABLE} (user_id, reviewer_name, at, profile_id, control_id, "
            "thread_id, action, note, evidence, model, prompt_version, goal_before, goal_after) "
            "VALUES (%s, 'Dana', now() - interval '1 day', %s, '', '', 'goal', '', '[]', '', '', "
            "'audit-readiness', 'internal-improvement')",
            (alice["X-Test-User"], DEFAULT_PROFILE),
        )

    db.setup()
    shape = _audit_table_shape(db)
    db.setup()  # safe to run again: nothing changes
    assert _audit_table_shape(db) == shape
    assert ("round_name", "YES") in shape[0]
    with TestClient(app_on(db.url)) as client:
        closed = close(client, alice)
        started = start(client, alice, "Q4 2026")
        entries = trail(client, alice)

    assert (closed.status_code, started.status_code) == (200, 200)
    assert [(e["action"], e["goal_after"], e["round_name"]) for e in entries] == [
        ("round-start", None, "Q4 2026"),
        ("round-close", None, "First round"),
        ("goal", "internal-improvement", None),  # the old row kept as it was
    ]
    refused = []
    for statement in (
        f"UPDATE {AUDIT_TABLE} SET round_name = 'Renamed'",
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


# --- The report's trail ------------------------------------------------------------------------

ROUND_ROWS = {  # an entry and its action's words, by the profile's language
    "en": ("round-close", "First round", "Review round closed: First round"),
    "de": ("round-start", "Q4 2026", "Prüfrunde begonnen: Q4 2026"),
}


def round_change(language: str) -> AuditEntry:
    action, name, _ = ROUND_ROWS[language]
    entry = rounds.round_entry(Reviewer("u", "Dana Lead"), "nordlicht", action, name, AT)  # type: ignore[arg-type]
    return replace(entry, id=1)


@pytest.mark.parametrize("language", ["en", "de"])
def test_a_round_entrys_trail_row_says_it_in_the_reports_words_its_control_cells_empty(
    language,
):
    built = built_report(entries=[round_change(language)])

    rows = report.trail_rows(built["trail"]["entries"], report.words_for(language))

    assert rows == [
        ["2026-11-09 10:30 UTC", "Dana Lead", "", "", ROUND_ROWS[language][2], *[""] * 6]
    ]


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_pdf_builds_with_a_round_entry_saying_it_in_the_reports_words(language):
    built = built_report(entries=[round_change(language)])

    read = text(report_pdf.pdf(built, report.words_for(language)))

    assert " ".join(ROUND_ROWS[language][2].split()) in " ".join(read.split())


def test_a_control_not_applicable_as_a_whole_is_neither_frozen_nor_compared(users, monkeypatch):
    # #454: a company made in setup with 5.2.9 not applicable, and an accepted assessment of
    # 5.2.9 all the same (read as the newest): the close freezes the 11 others, and the
    # comparison counts and lists none of it.
    alice, _ = users
    headers = alice | {api.PROFILE_HEADER: MUSTERFIRMA_ID}
    body = MUSTERFIRMA | {
        "not_applicable": [{"control": "5.2.9", "requirement": None, "reason": "Not ours."}]
    }
    newest = {"5.2.9": seeded("5.2.9", "evidenced", "accepted", profile_id=MUSTERFIRMA_ID)}
    monkeypatch.setattr(api, "newest_by_control", lambda *_: newest)
    with TestClient(app_on(None)) as client:
        made = client.post("/api/developer/profiles", json=body, headers=alice)
        assert made.status_code == 201, made.text
        assert close(client, headers).status_code == 200
        assert start(client, headers, "Q1 2027").status_code == 200
        comparison = overview_rounds(client, headers)["comparison"]
    closed = rounds.process_rounds().last_closed(alice["X-Test-User"], MUSTERFIRMA_ID, None)

    assert closed is not None
    assert [f.control_id for f in closed.results] == [i for i in IDS if i != "5.2.9"]
    assert comparison["accepted"] == {"now": 0, "then": 0}
    assert comparison["by_verdict"]["evidenced"] == {"now": 0, "then": 0}
    assert comparison["changed"] == []
