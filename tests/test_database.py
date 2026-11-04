"""The database module (#33): one owner of connections, LangGraph's settings, retries, 503s.

The unit tests run offline on fakes. The integration tests need a Postgres server: CI's full lane
gives one (a service container) through VOUCH_TEST_DATABASE_URL; without it they're skipped.
"""

import logging
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

import psycopg
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from psycopg import conninfo
from psycopg.rows import dict_row
from pydantic import SecretStr

from vouch import database
from vouch.api import create_app, database_connection
from vouch.database import Database, DatabaseUnavailable
from vouch.settings import DATABASE_URL_ENV, Settings, load_settings

PASSWORD = "pw-marker-33"
URL = f"postgresql://vouch:{PASSWORD}@127.0.0.1:5432/vouch"
# A Unix socket in a folder that doesn't exist: libpq fails at once, with no network.
UNREACHABLE_URL = f"postgresql://vouch:{PASSWORD}@/vouch?host=/nonexistent-33"


class FlakyConnect:
    """psycopg's connect, failing the first `failures` calls as a dropped instance does."""

    def __init__(self, failures: int, message: str = "server closed the connection") -> None:
        self.failures = failures
        self.message = message
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs))
        if len(self.calls) <= self.failures:
            raise psycopg.OperationalError(self.message)
        return FakeConnection()


class FakeConnection:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_a_connection_failing_once_then_succeeding_is_retried_after_a_backoff():
    connect, waits = FlakyConnect(failures=1), []
    db = Database(SecretStr(URL), connect=connect, sleep=waits.append)

    with db.connection() as conn:
        assert isinstance(conn, FakeConnection)

    assert len(connect.calls) == 2
    assert waits == [database.BACKOFF]
    assert conn.closed


def test_after_every_attempt_fails_it_gives_up_with_growing_waits():
    connect, waits = FlakyConnect(failures=99), []
    db = Database(SecretStr(URL), connect=connect, sleep=waits.append)

    with pytest.raises(DatabaseUnavailable), db.connection():
        pass

    assert len(connect.calls) == database.ATTEMPTS
    assert waits == [database.BACKOFF * 2**n for n in range(database.ATTEMPTS - 1)]


def test_connections_use_langgraphs_postgres_settings():
    connect = FlakyConnect(failures=0)

    with Database(SecretStr(URL), connect=connect).connection():
        pass

    [(url, kwargs)] = connect.calls
    assert url == URL
    assert kwargs["autocommit"] is True
    assert kwargs["row_factory"] is dict_row


def test_the_database_password_never_reaches_an_error_or_the_log(caplog):
    caplog.set_level(logging.DEBUG)
    connect = FlakyConnect(failures=99, message=f"bad things near {URL} and {PASSWORD}")
    db = Database(SecretStr(URL), connect=connect, sleep=lambda _: None)

    with pytest.raises(DatabaseUnavailable) as refused, db.connection():
        pass

    assert "bad things" in str(refused.value)  # the reason survives, for the owner
    assert PASSWORD not in str(refused.value)
    assert PASSWORD not in caplog.text
    assert PASSWORD not in repr(db)


def test_an_unreachable_database_raises_unavailable_not_psycopgs_error():
    db = Database(SecretStr(UNREACHABLE_URL), sleep=lambda _: None)

    with pytest.raises(DatabaseUnavailable), db.connection():
        pass
    assert db.reachable() is False


def test_settings_read_the_database_url_as_a_secret():
    settings = load_settings({DATABASE_URL_ENV: f" {URL} "})

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == URL
    assert PASSWORD not in repr(settings)
    assert load_settings({}).database_url is None


def client_with_a_database_route(settings: Settings) -> TestClient:
    """The app on `settings`, plus a route that needs the database."""
    app = create_app(settings)

    @app.get("/needs-database")
    def needs_database(conn: Annotated[Any, Depends(database_connection)]) -> dict[str, bool]:
        return {"connected": conn is not None}

    return TestClient(app, raise_server_exceptions=False)


def test_with_the_database_down_the_app_starts_health_says_unavailable_and_routes_answer_503():
    client = client_with_a_database_route(Settings(database_url=SecretStr(UNREACHABLE_URL)))

    health = client.get("/api/health")
    refused = client.get("/needs-database")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["database"] == "unavailable"
    assert refused.status_code == 503
    assert PASSWORD not in health.text + refused.text


def test_with_no_database_url_health_is_unchanged_and_database_routes_answer_503():
    client = client_with_a_database_route(Settings())

    assert "database" not in client.get("/api/health").json()
    assert client.get("/needs-database").status_code == 503


def test_the_setup_command_without_a_url_refuses_naming_the_setting(capsys):
    assert database.main([], env={}) == database.EXIT_BAD_INPUT
    assert DATABASE_URL_ENV in capsys.readouterr().err


def test_the_setup_command_against_an_unreachable_database_fails_without_the_password(capsys):
    code = database.main([], env={DATABASE_URL_ENV: UNREACHABLE_URL}, sleep=lambda _: None)

    err = capsys.readouterr().err
    assert code == database.EXIT_UNAVAILABLE
    assert "unavailable" in err
    assert PASSWORD not in err


def test_no_other_module_opens_a_connection():
    """vouch.database owns connections: no other module imports psycopg or a from_conn_string."""
    src = Path(database.__file__).parent
    others = [p for p in src.rglob("*.py") if p.name != "database.py"]

    opening = [
        p.name
        for p in others
        if re.search(r"^\s*(import|from) psycopg|from_conn_string", p.read_text(), re.MULTILINE)
    ]
    assert others
    assert opening == []


# Integration: a real Postgres server (CI's full lane, or `podman run postgres:16` locally).

SERVER_URL = os.environ.get("VOUCH_TEST_DATABASE_URL")
needs_postgres = pytest.mark.skipif(
    not SERVER_URL, reason="no Postgres server: set VOUCH_TEST_DATABASE_URL (CI's full lane)"
)
LANGGRAPH_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
    "store",
    "store_migrations",
}


@pytest.fixture
def empty_database() -> Iterator[Database]:
    """A new, empty database on the test server, dropped afterwards."""
    assert SERVER_URL
    name = f"vouch_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(SERVER_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield Database(SecretStr(conninfo.make_conninfo(SERVER_URL, dbname=name)))
    finally:
        with psycopg.connect(SERVER_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def tables(db: Database) -> set[str]:
    with db.connection() as conn:
        rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return {row["tablename"] for row in rows}


@needs_postgres
def test_the_setup_command_creates_langgraphs_tables_in_an_empty_database(empty_database, capsys):
    url = empty_database.url.get_secret_value()
    assert tables(empty_database) == set()

    assert database.main([], env={DATABASE_URL_ENV: url}) == 0
    assert database.main([], env={DATABASE_URL_ENV: url}) == 0  # again: nothing to do

    assert LANGGRAPH_TABLES <= tables(empty_database)
    assert "ready" in capsys.readouterr().out


@needs_postgres
def test_a_checkpoint_is_written_and_read_back_through_the_module(empty_database):
    empty_database.setup()
    config = {"configurable": {"thread_id": "t-33", "checkpoint_ns": ""}}
    checkpoint = {
        "v": 4,
        "ts": "2026-11-04T12:00:00+00:00",
        "id": "1ef4f797-8335-6428-8001-8a1503f9b875",
        "channel_values": {"verdict": "evidenced"},
        "channel_versions": {"__start__": 1},
        "versions_seen": {"__input__": {}},
    }

    with empty_database.checkpointer() as saver:
        saver.put(config, checkpoint, {"source": "input", "step": 0}, {})
    with empty_database.checkpointer() as saver:
        read = saver.get({"configurable": {"thread_id": "t-33"}})

    assert read is not None
    assert read["id"] == checkpoint["id"]
    assert read["channel_values"] == {"verdict": "evidenced"}


@needs_postgres
def test_health_says_reachable_with_the_database_up(empty_database):
    client = client_with_a_database_route(Settings(database_url=empty_database.url))

    assert client.get("/api/health").json()["database"] == "reachable"
    assert client.get("/needs-database").json() == {"connected": True}
