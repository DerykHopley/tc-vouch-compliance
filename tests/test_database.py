"""The database module (#33): one owner of connections, LangGraph's settings, retries, 503s.

The unit tests run offline on fakes, or on libpq failing before any network. The integration
tests need a Postgres server: CI's `database` job gives one (a service container) through
VOUCH_TEST_DATABASE_URL; without it they're skipped.
"""

import logging
import os
import re
import select
import socket
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any

import psycopg
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from psycopg import conninfo
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from pydantic import SecretStr

from vouch import database
from vouch.api import create_app, database_connection
from vouch.database import Database, DatabaseMisconfigured, DatabaseUnavailable
from vouch.exit_codes import EXIT_BAD_INPUT, EXIT_UNAVAILABLE
from vouch.settings import DATABASE_URL_ENV, Settings, SettingsError, load_settings

PASSWORD = "pw-marker-33"
URL = f"postgresql://vouch:{PASSWORD}@127.0.0.1:5432/vouch"
# A Unix socket in a folder that doesn't exist: libpq fails at once, with no network.
UNREACHABLE_URL = f"postgresql://vouch:{PASSWORD}@/vouch?host=/nonexistent-33"
# The reviewer's case: libpq can't decode the password, and echoes it.
MALFORMED_URL = "postgresql://vouch:s3cr%zzet@/vouch?host=/nonexistent-33"


class FakeConnection:
    """What the pool touches on a connection: enough to pool it without a server."""

    def __init__(self, missing: int = 0) -> None:
        self.autocommit = True
        self.broken = False
        self.closed = False
        self.missing = missing  # how many of vouch-db-setup's tables health's check finds missing
        self.queries: list[str] = []
        self.pgconn = type("PGconn", (), {"transaction_status": TransactionStatus.IDLE})()

    def execute(self, query: str, *args: Any) -> "FakeConnection":
        if self.broken:  # as libpq does: the status turns unknown, so the pool discards it
            self.pgconn.transaction_status = TransactionStatus.UNKNOWN
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        self.queries.append(query)
        return self

    def fetchone(self) -> dict[str, int]:
        return {"missing": self.missing}

    def rollback(self) -> None: ...

    def close(self) -> None:
        self.closed = True


class FlakyConnect:
    """psycopg's connect, failing the first `failures` calls with `message`."""

    def __init__(
        self, failures: int = 0, message: str = "server closed the connection", missing: int = 0
    ) -> None:
        self.failures = failures
        self.message = message
        self.missing = missing
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.made: list[FakeConnection] = []

    def __call__(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs))
        if len(self.calls) <= self.failures:
            raise psycopg.OperationalError(self.message)
        self.made.append(FakeConnection(self.missing))
        return self.made[-1]


@pytest.fixture
def make_db() -> Iterator[Any]:
    """Databases that are closed after the test, so no pool keeps reconnecting."""
    made: list[Database] = []

    def make(url: str = URL, **kwargs: Any) -> Database:
        made.append(Database(SecretStr(url), **kwargs))
        return made[-1]

    yield make
    for db in made:
        db.close()


def test_a_connection_failing_once_then_succeeding_is_retried(make_db):
    connect = FlakyConnect(failures=1)
    db = make_db(connect=connect)

    with db.connection() as conn:
        assert isinstance(conn, FakeConnection)

    assert len(connect.calls) == 2


def test_connections_use_langgraphs_postgres_settings(make_db):
    connect = FlakyConnect()

    with make_db(connect=connect).connection():
        pass

    [(url, kwargs)] = connect.calls
    assert url == URL
    assert kwargs["autocommit"] is True
    assert kwargs["row_factory"] is dict_row


def bounded(kwargs: dict[str, Any]) -> bool:
    """#119: the statement timeout and TCP keepalives, from vouch.database's constants."""
    return (
        database.CONNECTION_KWARGS.items() <= kwargs.items()
        and kwargs["options"] == f"-c statement_timeout={database.STATEMENT_TIMEOUT_SECONDS}s"
        and kwargs["keepalives"] == 1
    )


def test_the_private_psycopg_names_the_bounded_wait_needs_still_exist():
    """BoundedConnection catches psycopg.errors._WaitTimeout and passes `timeout` to `wait`:
    an upgrade that drops either fails here, not at the first frozen database."""
    import inspect

    import psycopg

    assert issubclass(psycopg.errors._WaitTimeout, Exception)
    assert "timeout" in inspect.signature(psycopg.Connection.wait).parameters


def test_every_connection_has_a_statement_timeout_and_keepalives(make_db):
    pooled, direct = FlakyConnect(), FlakyConnect(failures=1)

    with make_db(connect=pooled).connection():
        pass
    with pytest.raises(DatabaseUnavailable):
        make_db(connect=direct).setup()

    assert bounded(pooled.calls[0][1]) and bounded(direct.calls[0][1])


def test_the_checkout_check_waits_no_longer_than_its_bound(make_db):
    """Each checkout checks the connection with a shorter wait than a query's, then sets it
    back."""
    seen: list[float] = []

    class Checked(FakeConnection):
        wait_timeout = database.WAIT_TIMEOUT_SECONDS

        def execute(self, query: str, *args: Any) -> None:
            seen.append(self.wait_timeout)

    db = make_db(connect=lambda url, **kwargs: Checked())

    with db.connection() as conn:
        assert conn.wait_timeout == database.WAIT_TIMEOUT_SECONDS
    with db.connection() as conn:  # the same one, from the pool
        assert conn.wait_timeout == database.WAIT_TIMEOUT_SECONDS

    assert seen == [database.CHECK_TIMEOUT_SECONDS] * 2
    assert database.CHECK_TIMEOUT_SECONDS < database.WAIT_TIMEOUT_SECONDS


def test_nothing_connects_until_a_connection_is_asked_for(make_db):
    connect = FlakyConnect()
    db = make_db(connect=connect)
    create_app(Settings(database_url=db.url))

    assert connect.calls == []


def test_connections_are_pooled_and_a_dead_one_is_replaced_on_checkout(make_db):
    connect = FlakyConnect()
    db = make_db(connect=connect)
    with db.connection() as first:
        pass
    with db.connection() as again:
        assert again is first  # pooled, not reopened
    first.broken = True  # the instance restarted meanwhile

    with db.connection() as replaced:
        assert replaced is not first
        assert not replaced.broken

    assert first.pgconn.transaction_status == TransactionStatus.UNKNOWN  # discarded, not reused


def test_a_closed_pool_is_unavailable_not_psycopgs_error(make_db):
    """The app shutting down while a request waits: PoolClosed becomes a 503."""
    db = make_db(connect=FlakyConnect())
    with db.connection():
        pass
    db._pool.close()  # the pool alone, as during shutdown before Database.close returns

    with pytest.raises(DatabaseUnavailable), db.connection():
        pass


def test_a_connection_level_failure_is_retried_until_the_wait_runs_out(make_db):
    connect = FlakyConnect(failures=99, message="connection refused")
    db = make_db(connect=connect, timeout=1.5)

    with pytest.raises(DatabaseUnavailable, match="connection refused"), db.connection():
        pass

    assert len(connect.calls) >= 2  # the pool reconnected after its backoff


def test_a_malformed_url_says_only_that_without_libpqs_text(make_db, caplog):
    caplog.set_level(logging.DEBUG)
    db = make_db(MALFORMED_URL)  # the real psycopg connect

    with pytest.raises(DatabaseMisconfigured) as refused, db.connection():
        pass

    assert str(refused.value) == f"{DATABASE_URL_ENV} is malformed"
    assert "s3cr" not in caplog.text
    assert db.health() == "unavailable"


def test_the_password_never_reaches_an_error_or_the_log_in_any_form(make_db, caplog):
    caplog.set_level(logging.DEBUG)
    url = "postgresql://vouch:p%40ss-marker-33@127.0.0.1:5432/vouch"  # decoded: p@ss-marker-33
    message = f"FATAL: password authentication failed near {url}, p%40ss-marker-33, p@ss-marker-33"
    db = make_db(url, connect=FlakyConnect(failures=99, message=message), timeout=0.5)

    with pytest.raises(DatabaseUnavailable) as refused, db.connection():
        pass

    assert "password authentication failed" in str(refused.value)  # the reason survives
    for form in ("p%40ss-marker-33", "p@ss-marker-33"):
        assert form not in str(refused.value)
        assert form not in caplog.text
    assert "marker" not in repr(db)


def test_an_unreachable_database_raises_unavailable_not_psycopgs_error(make_db):
    db = make_db(UNREACHABLE_URL, timeout=0.3)  # the real psycopg connect

    with pytest.raises(DatabaseUnavailable, match="No such file"), db.connection():
        pass


def test_a_connection_dropping_in_use_is_unavailable(make_db):
    db = make_db(connect=FlakyConnect())

    with pytest.raises(DatabaseUnavailable), db.connection():
        raise psycopg.OperationalError("terminating connection due to administrator command")
    with pytest.raises(DatabaseUnavailable), db.checkpointer():
        raise psycopg.OperationalError("server closed the connection unexpectedly")


def test_health_checks_the_database_at_most_once_in_a_few_seconds(make_db):
    now = [100.0]
    connect = FlakyConnect()
    db = make_db(connect=connect, clock=lambda: now[0])
    checks = []
    real = Database.connection

    def counted(self, timeout=None):
        checks.append(timeout)
        return real(self, timeout)

    db.connection = counted.__get__(db)

    assert db.health() == db.health() == "reachable"
    now[0] += database.HEALTH_CACHE_SECONDS
    assert db.health() == "reachable"

    assert checks == [database.HEALTH_TIMEOUT_SECONDS] * 2
    # The setup check (#123) is part of the cached answer: one query per check, not per call.
    [conn] = connect.made
    assert [q for q in conn.queries if "to_regclass" in q] == [database.SETUP_CHECK] * 2


def test_health_says_needs_setup_when_vouch_db_setup_s_tables_are_missing(make_db):
    db = make_db(connect=FlakyConnect(missing=2))

    assert db.health() == "needs-setup"


def test_health_s_setup_check_waits_no_longer_than_health_s_bound(make_db):
    """A server that freezes after the checkout check: the setup query still answers within
    HEALTH_TIMEOUT_SECONDS, not a query's WAIT_TIMEOUT_SECONDS."""
    seen: list[float] = []

    class Timed(FakeConnection):
        wait_timeout = database.WAIT_TIMEOUT_SECONDS

        def execute(self, query: str, *args: Any) -> FakeConnection:
            if "to_regclass" in query:
                seen.append(self.wait_timeout)
            return super().execute(query, *args)

    db = make_db(connect=lambda url, **kwargs: Timed())

    assert db.health() == "reachable"
    assert seen == [database.HEALTH_TIMEOUT_SECONDS]
    with db.connection() as conn:
        assert conn.wait_timeout == database.WAIT_TIMEOUT_SECONDS  # set back after the check


def test_settings_read_the_database_url_as_a_secret():
    settings = load_settings({DATABASE_URL_ENV: f" {URL} "})

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == URL
    assert PASSWORD not in repr(settings)
    assert load_settings({}).database_url is None


def client_with_a_database_route(settings: Settings, db: Database | None = None) -> TestClient:
    """The app on `settings` (its Database replaced by `db`), plus routes that need it."""
    app = create_app(settings)
    if db is not None:
        app.state.database = db

    @app.get("/needs-database")
    def needs_database(conn: Annotated[Any, Depends(database_connection)]) -> dict[str, bool]:
        return {"connected": conn is not None}

    @app.get("/drops-database")
    def drops_database(conn: Annotated[Any, Depends(database_connection)]) -> None:
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    return TestClient(app, raise_server_exceptions=False)


def test_with_the_database_down_the_app_starts_health_says_unavailable_and_routes_answer_503(
    make_db,
):
    db = make_db(UNREACHABLE_URL, timeout=0.3)
    client = client_with_a_database_route(Settings(database_url=db.url), db)

    health = client.get("/api/health")
    refused = client.get("/needs-database")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["database"] == "unavailable"
    assert refused.status_code == 503
    assert "try again shortly" in refused.json()["detail"]
    assert PASSWORD not in health.text + refused.text


def test_a_connection_dropping_mid_route_answers_503_not_500(make_db):
    db = make_db(connect=FlakyConnect())
    client = client_with_a_database_route(Settings(database_url=db.url), db)

    response = client.get("/drops-database")

    assert response.status_code == 503
    assert "try again shortly" in response.json()["detail"]


def test_with_no_database_url_health_is_unchanged_and_database_routes_answer_503():
    client = client_with_a_database_route(Settings())

    assert "database" not in client.get("/api/health").json()
    assert client.get("/needs-database").status_code == 503


def test_the_app_closes_its_database_at_shutdown():
    app = create_app(Settings(database_url=SecretStr(URL)))

    with TestClient(app):
        assert not app.state.database.closed
    assert app.state.database.closed


def test_the_setup_command_without_a_url_refuses_naming_the_setting(capsys):
    assert database.main([], env={}) == EXIT_BAD_INPUT
    assert DATABASE_URL_ENV in capsys.readouterr().err


SIGN_IN_ENVS = {  # #190: sign-in settings the API would refuse, which database setup doesn't use
    "keys-without-mode": {"CLERK_SECRET_KEY": "sk_test_x", "CLERK_PUBLISHABLE_KEY": "pk_test_x"},
    "clerk-without-parties": {"VOUCH_AUTH_MODE": "clerk", "CLERK_SECRET_KEY": "sk_test_x"},
}


@pytest.mark.parametrize("sign_in", SIGN_IN_ENVS.values(), ids=SIGN_IN_ENVS.keys())
def test_the_setup_command_reaches_the_database_whatever_the_sign_in_settings(monkeypatch, sign_in):
    reached: list[str] = []

    class FakeDatabase:
        def __init__(self, url: SecretStr):
            reached.append(url.get_secret_value())

        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> None:
            pass

        def setup(self) -> None:
            reached.append("setup")

    monkeypatch.setattr(database, "Database", FakeDatabase)
    env = {**sign_in, DATABASE_URL_ENV: "postgresql://vouch@localhost:5432/vouch"}
    with pytest.raises(SettingsError):  # precondition: the API refuses these settings (#151)
        load_settings(env)

    assert database.main([], env=env) == 0
    assert reached == [env[DATABASE_URL_ENV], "setup"]


def test_the_setup_command_on_a_malformed_url_refuses_without_the_password(capsys):
    assert database.main([], env={DATABASE_URL_ENV: MALFORMED_URL}) == EXIT_BAD_INPUT

    captured = capsys.readouterr()
    assert f"{DATABASE_URL_ENV} is malformed" in captured.err
    assert "s3cr" not in captured.out + captured.err


def test_the_setup_command_against_an_unreachable_database_fails_without_the_password(capsys):
    code = database.main([], env={DATABASE_URL_ENV: UNREACHABLE_URL})

    err = capsys.readouterr().err
    assert code == EXIT_UNAVAILABLE
    assert "No such file" in err  # libpq's own reason
    assert PASSWORD not in err


def test_the_setup_connects_once_without_the_pool_or_a_retry(make_db):
    connect = FlakyConnect(failures=1, message="server closed the connection")
    db = make_db(connect=connect)

    with pytest.raises(DatabaseUnavailable, match="server closed the connection"):
        db.setup()

    [(_, kwargs)] = connect.calls
    assert kwargs["autocommit"] is True and kwargs["row_factory"] is dict_row
    assert kwargs["connect_timeout"] == database.CONNECT_TIMEOUT_SECONDS
    assert db._pool is None


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


# Integration: a real Postgres server (CI's `database` job, or `podman run postgres:16` locally).

SERVER_URL = os.environ.get("VOUCH_TEST_DATABASE_URL")
needs_postgres = pytest.mark.skipif(
    not SERVER_URL, reason="no Postgres server: set VOUCH_TEST_DATABASE_URL (CI's database job)"
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
    db = Database(SecretStr(conninfo.make_conninfo(SERVER_URL, dbname=name)))
    try:
        yield db
    finally:
        db.close()
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
def test_health_says_needs_setup_until_vouch_db_setup_has_run_then_reachable(empty_database):
    """#123: a database with no tables isn't a working deploy, though it answers."""
    now = [0.0]
    db = Database(empty_database.url, clock=lambda: now[0])
    client = client_with_a_database_route(Settings(database_url=db.url), db)
    try:
        before = client.get("/api/health").json()
        assert before["status"] == "ok"  # liveness: the app itself is up
        assert before["database"] == "needs-setup"
        assert client.get("/needs-database").json() == {"connected": True}
        with pytest.raises(DatabaseUnavailable, match="vouch-db-setup"):
            db.set_setting("model", "fake")  # a route on a table answers 503

        empty_database.setup()
        assert client.get("/api/health").json()["database"] == "needs-setup"  # still cached
        now[0] += database.HEALTH_CACHE_SECONDS

        assert client.get("/api/health").json()["database"] == "reachable"
    finally:
        db.close()


@needs_postgres
def test_health_s_setup_check_names_every_table_vouch_db_setup_creates(empty_database):
    empty_database.setup()

    assert set(database.SETUP_TABLES) == tables(empty_database)


@needs_postgres
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"password": "wrong-pw-33"}, "password authentication failed"),
        ({"dbname": "no_such_db_33"}, "does not exist"),
    ],
)
def test_the_setup_command_reports_a_real_refusal_at_once(change, reason, capsys):
    assert SERVER_URL
    url = conninfo.make_conninfo(SERVER_URL, **change)
    started = time.monotonic()

    code = database.main([], env={DATABASE_URL_ENV: url})

    err = capsys.readouterr().err
    assert code == EXIT_UNAVAILABLE
    assert time.monotonic() - started < 2
    assert reason in err
    assert "wrong-pw-33" not in err


@needs_postgres
def test_a_setting_is_kept_and_read_back_once_the_setup_has_run(empty_database):
    """Vouch's own settings table (#34, the model choice), made by the setup command alone."""
    empty_database.setup()
    assert empty_database.setting("model") is None

    empty_database.set_setting("model", "anthropic/claude-haiku-5.5")
    empty_database.set_setting("model", "openai/gpt-5.5")  # replaced, not added

    assert empty_database.setting("model") == "openai/gpt-5.5"
    assert database.SETTINGS_TABLE in tables(empty_database)


@needs_postgres
def test_without_the_setup_a_setting_reads_as_unset_and_a_write_is_unavailable(
    empty_database, caplog
):
    """Only vouch-db-setup creates the table: a write before it is a 503 whose log names it."""
    assert empty_database.setting("model") is None

    with pytest.raises(DatabaseUnavailable):
        empty_database.set_setting("model", "openai/gpt-5.5")

    assert "vouch-db-setup" in caplog.text
    assert database.SETTINGS_TABLE not in tables(empty_database)


def test_a_setting_with_the_database_down_is_unavailable(make_db):
    db = make_db(UNREACHABLE_URL, timeout=0.3)

    with pytest.raises(DatabaseUnavailable):
        db.setting("model")
    with pytest.raises(DatabaseUnavailable):
        db.set_setting("model", "x/y")


@needs_postgres
def test_a_developers_model_choice_outlasts_a_restart_in_the_database(empty_database):
    """#34: the choice is kept in the database, so a new instance of the app runs on it too."""
    from test_model_choice import OTHER_MODEL, FakeCatalogue, FakeChat, fake_openrouter

    url = empty_database.url
    empty_database.setup()

    def app_on_the_database():
        app = create_app(Settings(openrouter_api_key=SecretStr("sk-or-test"), database_url=url))
        app.state.models.openrouter = fake_openrouter(FakeChat(), FakeCatalogue([OTHER_MODEL]))
        return app

    with TestClient(app_on_the_database()) as first:
        assert first.put("/api/developer/model", json={"model": OTHER_MODEL}).status_code == 200
    with TestClient(app_on_the_database()) as second:
        shown = second.get("/api/developer/settings").json()

    assert (shown["model"], shown["kept_in"]) == (OTHER_MODEL, "database")


class FreezableProxy:
    """A TCP proxy to the test server that can freeze: it keeps every connection open and
    accepts new ones, but passes nothing on, as a paused Postgres container does (#119). Its
    kernel still acknowledges what it's sent, so TCP keepalives see nothing wrong."""

    def __init__(self, host: str, port: int) -> None:
        self.target = (host, port)
        self.frozen = threading.Event()
        self.stopped = threading.Event()
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.sockets: list[socket.socket] = [self.listener]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self.stopped.is_set():
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            self.sockets.append(client)
            if self.frozen.is_set():
                continue  # accepted, and never answered
            server = socket.create_connection(self.target)
            self.sockets.append(server)
            for source, sink in ((client, server), (server, client)):
                threading.Thread(target=self._pump, args=(source, sink), daemon=True).start()

    def _pump(self, source: socket.socket, sink: socket.socket) -> None:
        while not self.stopped.is_set():
            if self.frozen.is_set():
                time.sleep(0.05)
                continue
            try:
                ready, _, _ = select.select([source], [], [], 0.05)
                if ready and not self.frozen.is_set():  # frozen while it waited: pass nothing
                    data = source.recv(65536)
                    if not data:
                        return
                    sink.sendall(data)
            except (OSError, ValueError):  # closed by stop()
                return

    def stop(self) -> None:
        self.stopped.set()
        for sock in self.sockets:
            sock.close()


@pytest.fixture
def freezable(empty_database) -> Iterator[tuple[Database, FreezableProxy]]:
    """A Database on the test server through a FreezableProxy. Its health clock moves on past
    the cache at every check, so each health call asks the database."""
    target = empty_database.url.get_secret_value()
    params = conninfo.conninfo_to_dict(target)
    proxy = FreezableProxy(str(params.get("host") or "127.0.0.1"), int(params.get("port") or 5432))
    now = [0.0]

    def clock() -> float:
        now[0] += database.HEALTH_CACHE_SECONDS
        return now[0]

    url = conninfo.make_conninfo(target, host="127.0.0.1", port=proxy.port)
    empty_database.setup()  # health's reachable needs vouch-db-setup's tables (#123)
    db = Database(SecretStr(url), clock=clock)
    try:
        yield db, proxy
    finally:
        proxy.frozen.clear()
        db.close()
        proxy.stop()


def within(seconds: float, action: Callable[[], Any]) -> Any:
    """`action`'s answer (or the DatabaseUnavailable it raised), failing the test if it takes
    `seconds` or more.
    It runs in a thread, so a hang fails here and the fixture's thaw lets it finish."""
    outcome: list[Any] = []

    def run() -> None:
        try:
            outcome.append(action())
        except DatabaseUnavailable as error:
            outcome.append(error)

    worker = threading.Thread(target=run, daemon=True)
    start = time.monotonic()
    worker.start()
    worker.join(seconds)
    took = time.monotonic() - start
    assert outcome, f"still waiting after {took:.1f}s"
    return outcome[0]


@needs_postgres
@pytest.mark.enable_socket
def test_against_a_frozen_database_health_says_unavailable_within_its_bound(freezable):
    db, proxy = freezable
    client = client_with_a_database_route(Settings(database_url=db.url), db)
    assert client.get("/api/health").json()["database"] == "reachable"  # one pooled connection

    proxy.frozen.set()
    bound = database.HEALTH_TIMEOUT_SECONDS + database.CHECK_TIMEOUT_SECONDS + 1
    health = within(bound, lambda: client.get("/api/health"))

    assert health.json()["database"] == "unavailable"


@needs_postgres
@pytest.mark.enable_socket
def test_against_a_frozen_database_a_route_answers_503_within_its_bound(freezable):
    db, proxy = freezable
    client = client_with_a_database_route(Settings(database_url=db.url), db)
    assert client.get("/needs-database").json() == {"connected": True}  # one pooled connection

    proxy.frozen.set()
    bound = database.CHECKOUT_TIMEOUT_SECONDS + database.CHECK_TIMEOUT_SECONDS + 1
    refused = within(bound, lambda: client.get("/needs-database"))

    assert refused.status_code == 503
    assert refused.json()["unavailable"] == "database"


@needs_postgres
@pytest.mark.enable_socket
def test_a_query_the_database_freezes_during_is_unavailable_within_its_bound(freezable):
    db, proxy = freezable

    def frozen_mid_query() -> None:
        with db.connection() as conn:
            proxy.frozen.set()
            conn.execute("SELECT 1")

    raised = within(database.WAIT_TIMEOUT_SECONDS + 1, frozen_mid_query)

    assert isinstance(raised, DatabaseUnavailable)
    assert database.NO_ANSWER.split("{")[0] in str(raised)


@needs_postgres
def test_the_server_holds_each_statement_to_the_timeout(empty_database):
    with empty_database.connection() as conn:
        shown = conn.execute("SHOW statement_timeout").fetchone()

    assert shown == {"statement_timeout": f"{database.STATEMENT_TIMEOUT_SECONDS}s"}
