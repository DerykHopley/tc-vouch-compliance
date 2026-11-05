"""Assessments and progress kept per user and compliance profile (#38).

Through the HTTP API, on the bundled fake model. Each test runs on both backends: in memory (no
database URL) and Postgres (CI's `database` job gives a server through VOUCH_TEST_DATABASE_URL;
without one those cases are skipped). The user is set per request by a test header, standing
in for the sign-in token: #32 proves each token gives its own user id.
"""

import uuid
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_api import read_events
from test_database import UNREACHABLE_URL, empty_database, needs_postgres  # noqa: F401 (a fixture)

from vouch import memory
from vouch.api import create_app, current_user, load_default_profile
from vouch.database import Database, DatabaseUnavailable
from vouch.graph import state_serializer
from vouch.profile import DEFAULT_PROFILE
from vouch.settings import Settings, User

DEMO_CONTROL = "4.1.2"


def as_user(request: Request) -> User:
    return User(id=request.headers["X-Test-User"])


def restart() -> None:
    """What a restart of the process forgets: its in-memory saver and store."""
    memory.memory_saver.cache_clear()
    memory.memory_store.cache_clear()


def app_on(url: SecretStr | None) -> Any:
    """A new app (a new process, as far as the database is concerned) on `url`'s database."""
    load_default_profile.cache_clear()
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
    return tuple({"X-Test-User": f"user_{who}_{uuid.uuid4().hex[:8]}"} for who in ("a", "b"))


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

        refused = client.post(url, json={"decision": "accept"}, headers=bob)
        accepted = client.post(url, json={"decision": "accept"}, headers=alice)

    assert refused.status_code == 404
    assert accepted.status_code == 200
    assert accepted.json()["assessment"]["status"] == "accepted"


def test_the_list_is_newest_first_and_shows_each_review(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        first = assess(client, alice)
        second = assess(client, alice)
        client.post(f"/api/assessments/{first}/review", json={"decision": "accept"}, headers=alice)

        listed = client.get("/api/assessments", headers=alice).json()["assessments"]

    assert [(a["thread_id"], a["status"]) for a in listed] == [
        (second, "waiting-for-review"),
        (first, "accepted"),
    ]


# --- Restart and sessions (Postgres) --------------------------------------------------------


@needs_postgres
def test_a_run_waiting_for_review_survives_a_restart_then_resumes_and_completes(postgres, users):
    alice, _ = users
    url = postgres.url

    with TestClient(app_on(url)) as before:
        thread_id = assess(before, alice)
    restart()
    with TestClient(app_on(url)) as after:  # the first app is shut down: its pool is closed
        reviewed = after.post(
            f"/api/assessments/{thread_id}/review", json={"decision": "accept"}, headers=alice
        )
        opened = after.get(f"/api/assessments/{thread_id}", headers=alice)

    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["assessment"]["status"] == "accepted"
    assert reviewed.json()["assessment"]["control_id"] == DEMO_CONTROL
    assert opened.json()["assessment"]["status"] == "accepted"


@needs_postgres
def test_progress_written_in_one_session_is_read_in_the_next(postgres, users):
    alice, _ = users
    url = postgres.url
    with TestClient(app_on(url)) as first_session:
        thread_id = assess(first_session, alice)
        first_session.post(
            f"/api/assessments/{thread_id}/review", json={"decision": "accept"}, headers=alice
        )
    restart()

    with Database(url) as db, db.store() as store, db.checkpointer(state_serializer()) as saver:
        progress = memory.progress(
            memory.Memory(store, alice["X-Test-User"], DEFAULT_PROFILE), saver
        )

    assert progress.controls[DEMO_CONTROL].status == "accepted"
    assert progress.controls[DEMO_CONTROL].thread_id == thread_id
    assert progress.counts() == {"accepted": 1, "rejected": 0, "waiting-for-review": 0}
    assert progress.last_session is not None


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

    store, saver = memory.memory_store(), memory.memory_saver()
    progress = memory.progress(memory.Memory(store, alice["X-Test-User"], DEFAULT_PROFILE), saver)

    assert progress.controls[DEMO_CONTROL].thread_id == again
    assert progress.counts() == {"accepted": 0, "rejected": 0, "waiting-for-review": 1}


def test_progress_is_kept_per_profile(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        assess(client, alice)

    store, saver = memory.memory_store(), memory.memory_saver()
    other = memory.progress(memory.Memory(store, alice["X-Test-User"], "another-profile"), saver)

    assert other.controls == {}
    assert other.last_session is None


# --- The database down: 503, nothing half-written --------------------------------------------


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("post", "/api/assessments", {"control_id": DEMO_CONTROL}),
        ("post", "/api/assessments/0123456789abcdef0123456789abcdef/review", {"decision": "accept"}),
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
        raise DatabaseUnavailable("the database connection failed: test")

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
