"""Assessments and progress kept per user and compliance profile (#38).

Through the HTTP API, on the bundled fake model. Each test runs on both backends: in memory (no
database URL) and Postgres (CI's `database` job gives a server through VOUCH_TEST_DATABASE_URL;
without one those cases are skipped). The user is set per request by a test header, standing
in for the sign-in token: #32 proves each token gives its own user id.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from pydantic import SecretStr
from reviewing import accepted, confirm, plan_fixes
from test_api import read_events
from test_database import UNREACHABLE_URL, empty_database, needs_postgres  # noqa: F401 (a fixture)

from vouch import added_documents, audit, company_profiles, graph, memory, remediation
from vouch.api import create_app, current_user
from vouch.database import Database, DatabaseUnavailable
from vouch.graph import state_serializer
from vouch.profile import DEFAULT_PROFILE, read_profile_once
from vouch.settings import Settings, User

DEMO_CONTROL = "4.1.2"


def as_user(request: Request) -> User:
    return User(id=request.headers["X-Test-User"])


def restart() -> None:
    """What a restart of the process forgets: its in-memory saver, store and audit trail, and the
    companies made in it with the profiles read from them, and its added documents."""
    memory.process_checkpointer.cache_clear()
    memory.process_store.cache_clear()
    audit.process_trail.cache_clear()
    remediation.process_items.cache_clear()
    company_profiles.process_company_profiles.cache_clear()
    company_profiles.read_stored_once.cache_clear()
    added_documents.process_added_documents.cache_clear()
    added_documents.added_index().clear()


def app_on(url: SecretStr | None) -> Any:
    """A new app (a new process, as far as the database is concerned) on `url`'s database."""
    read_profile_once.cache_clear()
    app = create_app(Settings(database_url=url))
    app.dependency_overrides[current_user] = as_user
    return app


@pytest.fixture
def postgres(request) -> Database:
    """A new Postgres database (test_database's `empty_database`), set up by vouch-db-setup."""
    db = request.getfixturevalue("empty_database")
    db.setup()
    return db


@pytest.fixture(params=["memory", pytest.param("postgres", marks=needs_postgres)])
def database_url(request) -> SecretStr | None:
    """No database (memory), or a new, set-up Postgres database."""
    return None if request.param == "memory" else request.getfixturevalue("postgres").url


@pytest.fixture
def users() -> tuple[dict[str, str], dict[str, str]]:
    """Two users, new to this test (the in-memory backend is one per process)."""
    a, b = ({"X-Test-User": f"user_{who}_{uuid.uuid4().hex[:8]}"} for who in ("a", "b"))
    return a, b


@contextmanager
def user_memory(
    url: SecretStr | None, headers: dict[str, str], profile: str = DEFAULT_PROFILE
) -> Iterator[memory.Memory]:
    """What the app keeps for the user under the profile, read the way another process would."""
    user_id = headers["X-Test-User"]
    if url is None:
        yield memory.Memory(memory.process_store(), user_id, profile)
        return
    with Database(url) as db, db.store() as store:
        yield memory.Memory(store, user_id, profile)


@pytest.fixture(params=["memory", pytest.param("postgres", marks=needs_postgres)])
def any_store(request) -> Iterator[BaseStore]:
    """A new, empty store: in memory, or Postgres's."""
    if request.param == "memory":
        yield InMemoryStore()
        return
    with request.getfixturevalue("postgres").store() as store:
        yield store


def failing_write(self, *args: Any) -> None:
    raise DatabaseUnavailable("the database connection failed: test")


def set_daily_limit(client: TestClient, headers: dict[str, str], limit: int) -> None:
    response = client.put(
        "/api/developer/daily-limit", json={"daily_limit": limit}, headers=headers
    )
    assert response.status_code == 200, response.text


def assess(client: TestClient, headers: dict[str, str], control_id: str = DEMO_CONTROL) -> str:
    """Start an assessment up to the review; its thread id."""
    response = client.post("/api/assessments", json={"control_id": control_id}, headers=headers)
    assert response.status_code == 200, response.text
    name, data = read_events(response)[-1]
    assert name == "result", data
    return data["assessment"]["thread_id"]


# --- Each user sees only their own ----------------------------------------------------------


def test_each_user_lists_only_their_own_assessments(database_url, users):
    alice, bob = users
    with TestClient(app_on(database_url)) as client:
        alices = assess(client, alice)
        bobs = assess(client, bob)

        listed_a = client.get("/api/assessments", headers=alice)
        listed_b = client.get("/api/assessments", headers=bob)

    assert listed_a.status_code == 200
    assert [a["thread_id"] for a in listed_a.json()["assessments"]] == [alices]
    assert [a["thread_id"] for a in listed_b.json()["assessments"]] == [bobs]
    entry = listed_a.json()["assessments"][0]
    assert entry["control_id"] == DEMO_CONTROL
    assert entry["status"] == "waiting-for-review"
    assert entry["verdict"] == "partly-evidenced"


def test_a_user_opens_their_own_assessment_and_another_users_is_404(database_url, users):
    alice, bob = users
    with TestClient(app_on(database_url)) as client:
        thread_id = assess(client, alice)

        own = client.get(f"/api/assessments/{thread_id}", headers=alice)
        others = client.get(f"/api/assessments/{thread_id}", headers=bob)

    assert own.status_code == 200
    assert own.json()["assessment"]["thread_id"] == thread_id
    assert own.json()["assessment"]["status"] == "waiting-for-review"
    assert others.status_code == 404


def test_reviewing_another_users_thread_is_404_and_leaves_it_waiting(database_url, users):
    alice, bob = users
    with TestClient(app_on(database_url)) as client:
        thread_id = assess(client, alice)
        url = f"/api/assessments/{thread_id}/review"

        refused = client.post(url, json={"decision": "reject"}, headers=bob)
        not_confirmed = confirm(client, thread_id, headers=bob)
        reviewed = accepted(client, thread_id, headers=alice)

    assert refused.status_code == 404
    assert not_confirmed.status_code == 404
    assert reviewed["status"] == "accepted"


def test_the_list_is_newest_first_and_shows_each_review(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        first = assess(client, alice)
        second = assess(client, alice)
        accepted(client, first, headers=alice)

        listed = client.get("/api/assessments", headers=alice).json()["assessments"]

    assert [(a["thread_id"], a["status"]) for a in listed] == [
        (second, "waiting-for-review"),
        (first, "accepted"),
    ]


# --- A restart (Postgres) -----------------------------------------------------------------


@needs_postgres
def test_a_run_waiting_for_review_survives_a_restart_then_resumes_and_completes(postgres, users):
    alice, _ = users
    url = postgres.url

    with TestClient(app_on(url)) as before:
        thread_id = assess(before, alice)
        confirm(before, thread_id, headers=alice)  # the restart comes at Plan fixes (#372)
    restart()
    with TestClient(app_on(url)) as after:  # the first app is shut down: its pool is closed
        plan_fixes(after, thread_id, headers=alice)
        reviewed = after.post(
            f"/api/assessments/{thread_id}/review", json={"decision": "accept"}, headers=alice
        )
        opened = after.get(f"/api/assessments/{thread_id}", headers=alice)

    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["assessment"]["status"] == "accepted"
    assert reviewed.json()["assessment"]["control_id"] == DEMO_CONTROL
    assert opened.json()["assessment"]["status"] == "accepted"


@needs_postgres
def test_progress_written_before_a_restart_is_read_after_it(postgres, users):
    alice, _ = users
    url = postgres.url
    with TestClient(app_on(url)) as before:
        thread_id = assess(before, alice)
        accepted(before, thread_id, headers=alice)
    restart()

    with Database(url) as db, db.store() as store, db.checkpointer(state_serializer()) as saver:
        progress = memory.progress(
            memory.Memory(store, alice["X-Test-User"], DEFAULT_PROFILE), saver
        )

    assert progress.controls[DEMO_CONTROL].status == "accepted"
    assert progress.controls[DEMO_CONTROL].thread_id == thread_id
    assert progress.counts() == {"accepted": 1, "rejected": 0, "waiting-for-review": 0}
    assert progress.last_worked is not None


def test_progress_takes_each_controls_latest_assessment(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        first = assess(client, alice)
        client.post(
            f"/api/assessments/{first}/review",
            json={"decision": "reject", "note": "See the records folder."},
            headers=alice,
        )
        again = assess(client, alice)

    store, saver = memory.process_store(), memory.process_checkpointer()
    progress = memory.progress(memory.Memory(store, alice["X-Test-User"], DEFAULT_PROFILE), saver)

    assert progress.controls[DEMO_CONTROL].thread_id == again
    assert progress.counts() == {"accepted": 0, "rejected": 0, "waiting-for-review": 1}


def test_progress_is_kept_per_profile(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        assess(client, alice)

    store, saver = memory.process_store(), memory.process_checkpointer()
    other = memory.progress(memory.Memory(store, alice["X-Test-User"], "another-profile"), saver)

    assert other.controls == {}
    assert other.last_worked is None


# --- The database down: 503, nothing half-written --------------------------------------------


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("post", "/api/assessments", {"control_id": DEMO_CONTROL}),
        (
            "post",
            "/api/assessments/0123456789abcdef0123456789abcdef/review",
            {"decision": "accept"},
        ),
        ("get", "/api/assessments", None),
        ("get", "/api/assessments/0123456789abcdef0123456789abcdef", None),
    ],
    ids=["start", "review", "list", "open"],
)
def test_with_the_database_down_each_route_answers_503(method, path, body, users):
    alice, _ = users
    app = app_on(SecretStr(UNREACHABLE_URL))
    app.state.database = Database(SecretStr(UNREACHABLE_URL), timeout=0.3)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = getattr(client, method)(path, headers=alice, **({"json": body} if body else {}))

    assert response.status_code == 503
    assert response.json()["unavailable"] == "database"
    assert "ci-only" not in response.text


def test_a_failed_write_of_the_assessment_answers_503_and_leaves_nothing_behind(
    database_url, users, monkeypatch
):
    """The thread is remembered before the run starts: when that write fails, no model runs and
    nothing is listed or opened afterwards."""
    alice, _ = users
    started: list[str] = []

    def failing(self, thread_id: str, control_id: str) -> None:
        started.append(thread_id)
        failing_write(self)

    with TestClient(app_on(database_url), raise_server_exceptions=False) as client:
        monkeypatch.setattr(memory.Memory, "remember_assessment", failing)
        response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=alice)
        monkeypatch.undo()
        listed = client.get("/api/assessments", headers=alice)
        opened = client.get(f"/api/assessments/{started[0]}", headers=alice)

    assert response.status_code == 503
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert listed.json()["assessments"] == []
    assert opened.status_code == 404


def test_a_failed_write_of_the_assessment_leaves_the_daily_count_untouched(
    database_url, users, monkeypatch
):
    alice, _ = users
    with TestClient(app_on(database_url), raise_server_exceptions=False) as client:
        set_daily_limit(client, alice, 1)
        monkeypatch.setattr(memory.Memory, "remember_assessment", failing_write)
        refused = client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=alice)
        monkeypatch.undo()

        assert refused.status_code == 503
        assess(client, alice)  # the day's one assessment is still there to start


def test_an_assessment_the_daily_limit_refuses_leaves_no_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url), raise_server_exceptions=False) as client:
        set_daily_limit(client, alice, 1)
        kept = assess(client, alice)
        over = client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=alice)

    assert over.status_code == 429
    with user_memory(database_url, alice) as mine:
        assert mine.thread_ids() == [kept]


# --- What counts as work, and what's kept -----------------------------------------------------


def test_only_a_successful_review_counts_as_work(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url), raise_server_exceptions=False) as client:
        thread_id = assess(client, alice)
        url = f"/api/assessments/{thread_id}/review"
        with user_memory(database_url, alice) as mine:
            started = mine.last_worked()

        unknown = client.post(
            "/api/assessments/no-such-thread/review", json={"decision": "accept"}, headers=alice
        )
        bad = confirm(
            client,
            thread_id,
            [{"requirement_id": "M9", "verdict": "evidenced", "reason": "x"}],
            headers=alice,
        )
        too_soon = client.post(url, json={"decision": "accept"}, headers=alice)
        with user_memory(database_url, alice) as mine:
            after_refusals = mine.last_worked()
        confirmed = confirm(client, thread_id, headers=alice)
        with user_memory(database_url, alice) as mine:
            after_confirm = mine.last_worked()
        rejected = client.post(url, json={"decision": "reject"}, headers=alice)
        with user_memory(database_url, alice) as mine:
            after_review = mine.last_worked()
        again = client.post(url, json={"decision": "reject"}, headers=alice)
        with user_memory(database_url, alice) as mine:
            after_again = mine.last_worked()

    codes = [r.status_code for r in (unknown, bad, too_soon, confirmed, rejected, again)]
    assert codes == [404, 422, 409, 200, 200, 409]
    assert started is not None
    assert after_refusals == started
    assert after_confirm is not None and after_confirm > started  # each step counts (#372)
    assert after_review is not None and after_review > after_confirm
    assert after_again == after_review


def test_a_review_stands_when_noting_the_work_fails(database_url, users, monkeypatch):
    alice, _ = users
    with TestClient(app_on(database_url), raise_server_exceptions=False) as client:
        thread_id = assess(client, alice)
        monkeypatch.setattr(memory.Memory, "note_worked", failing_write)
        reviewed = accepted(client, thread_id, headers=alice)  # each step stands

    assert reviewed["status"] == "accepted"


def test_over_the_cap_the_newest_assessments_are_kept_on_every_store(any_store, monkeypatch):
    monkeypatch.setattr(memory, "MAX_ASSESSMENTS", 2)
    monkeypatch.setattr(memory, "PAGE_SIZE", 2)  # more than one page to read
    started = iter(datetime(2026, 11, day, tzinfo=UTC) for day in (1, 3, 2))
    mine = memory.Memory(any_store, "user_a", DEFAULT_PROFILE, clock=lambda: next(started))
    for thread_id in ("oldest", "newest", "middle"):
        mine.remember_assessment(thread_id, DEMO_CONTROL)

    assert mine.thread_ids() == ["newest", "middle"]


def test_any_user_and_profile_id_can_be_kept_dots_included(any_store):
    dotted = memory.Memory(any_store, "user.a", "acme.example.de")
    split_otherwise = memory.Memory(any_store, "user", "a.acme.example.de")

    dotted.remember_assessment("t1", DEMO_CONTROL)

    assert dotted.thread_ids() == ["t1"]
    assert dotted.last_worked() is not None
    assert split_otherwise.thread_ids() == []
    assert dotted.scope != split_otherwise.scope


def test_listing_compiles_the_graph_once_and_progress_loads_each_controls_newest_only(
    users, monkeypatch
):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        for _ in range(3):
            assess(client, alice)
    saver = memory.process_checkpointer()
    builds: list[object] = []
    real_build = graph.build_graph
    monkeypatch.setattr(
        graph, "build_graph", lambda *a, **k: builds.append(1) or real_build(*a, **k)
    )
    reads: list[object] = []
    real_read = saver.get_tuple
    monkeypatch.setattr(saver, "get_tuple", lambda config: reads.append(1) or real_read(config))

    with user_memory(None, alice) as mine:
        assert len(mine.assessments(saver)) == 3
        assert len(builds) == 1
        reads_for_three = len(reads)
        reads.clear()
        memory.progress(mine, saver)

    assert len(reads) == reads_for_three // 3


@needs_postgres
def test_before_the_setup_has_run_the_list_answers_503_naming_it(request, users, caplog):
    alice, _ = users
    url = request.getfixturevalue("empty_database").url  # no vouch-db-setup

    with TestClient(app_on(url), raise_server_exceptions=False) as client:
        listed = client.get("/api/assessments", headers=alice)

    assert listed.status_code == 503
    assert "vouch-db-setup" in caplog.text


# --- A fresh start (#285): listed from a time on, nothing deleted -----------------------------


def clock_at(*days: int) -> Any:
    """A clock that returns the given days of November 2026 in turn, one per call."""
    times = iter(datetime(2026, 11, day, tzinfo=UTC) for day in days)
    return lambda: next(times)


def test_a_fresh_start_lists_only_the_entries_started_after_it_on_every_store(any_store):
    mine = memory.Memory(any_store, "user_a", DEFAULT_PROFILE, clock=clock_at(1, 2, 3, 4))
    mine.remember_assessment("old", DEMO_CONTROL)
    mine.remember_assessment("older-control", "1.1.1")
    mine.start_afresh()
    mine.remember_assessment("new", DEMO_CONTROL)

    assert mine.fresh_start() == datetime(2026, 11, 3, tzinfo=UTC)
    assert mine.thread_ids() == ["new"]


def test_an_entry_started_at_the_instant_of_the_fresh_start_is_listed(any_store):
    mine = memory.Memory(any_store, "user_a", DEFAULT_PROFILE, clock=clock_at(1, 2, 2))
    mine.remember_assessment("before", DEMO_CONTROL)
    mine.start_afresh()
    mine.remember_assessment("same-instant", DEMO_CONTROL)

    assert mine.thread_ids() == ["same-instant"]


def test_after_a_fresh_start_the_last_work_is_none_until_the_lead_works_again(any_store):
    mine = memory.Memory(any_store, "user_a", DEFAULT_PROFILE, clock=clock_at(1, 2, 3, 4))
    mine.remember_assessment("old", DEMO_CONTROL)
    mine.note_worked()
    mine.start_afresh()

    assert mine.last_worked() is None
    mine.note_worked()
    assert mine.last_worked() == datetime(2026, 11, 4, tzinfo=UTC)


def test_a_second_fresh_start_replaces_the_first_ones_time(any_store):
    mine = memory.Memory(any_store, "user_a", DEFAULT_PROFILE, clock=clock_at(1, 2, 3, 4))
    mine.start_afresh()
    mine.remember_assessment("between", DEMO_CONTROL)
    mine.start_afresh()
    mine.remember_assessment("after", DEMO_CONTROL)

    assert mine.fresh_start() == datetime(2026, 11, 3, tzinfo=UTC)
    assert mine.thread_ids() == ["after"]


def test_removing_the_fresh_start_note_lists_the_old_entries_again(any_store):
    mine = memory.Memory(any_store, "user_a", DEFAULT_PROFILE, clock=clock_at(1, 2, 3))
    mine.remember_assessment("old", DEMO_CONTROL)
    mine.note_worked()
    mine.start_afresh()

    any_store.delete(("vouch", "user_a", DEFAULT_PROFILE), memory.FRESH_START)

    assert mine.fresh_start() is None
    assert mine.thread_ids() == ["old"]
    assert mine.last_worked() == datetime(2026, 11, 2, tzinfo=UTC)


def test_a_fresh_start_that_cannot_open_a_new_conversation_hides_nothing(monkeypatch):
    """The note is written last: when an earlier write fails, the progress is still listed."""
    store = InMemoryStore()
    mine = memory.Memory(store, "user_a", DEFAULT_PROFILE, clock=clock_at(1, 2))
    mine.remember_assessment("old", DEMO_CONTROL)
    mine.set_current_chat("chat-1")
    monkeypatch.setattr(InMemoryStore, "delete", failing_write)

    with pytest.raises(DatabaseUnavailable):
        mine.start_afresh()

    assert mine.fresh_start() is None
    assert mine.thread_ids() == ["old"]


def test_a_fresh_start_opens_a_new_conversation_and_keeps_the_old_ones_lines(any_store):
    mine = memory.Memory(any_store, "user_a", DEFAULT_PROFILE)
    line = {"from": "lead", "text": "Where do I stand?"}
    mine.add_chat_turn("chat-1", [line], started_ns=1)
    mine.set_current_chat("chat-1")

    mine.start_afresh()

    assert mine.current_chat() is None
    assert mine.chat_lines("chat-1") == [line]
