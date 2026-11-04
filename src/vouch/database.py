"""Vouch's Postgres database: the one module that opens connections (#33).

Connections come from one psycopg pool with LangGraph's Postgres settings, `autocommit=True` and
`row_factory=dict_row` (langgraph-checkpoint-postgres's README). Cloud SQL has no transaction
pooler, so one URL (VOUCH_DATABASE_URL) serves the app and the setup command: locally through the
Cloud SQL Auth Proxy, on Cloud Run through the instance's Unix socket
(`host=/cloudsql/<connection name>`).

Nothing connects at start (the pool's `min_size` is 0, and it opens on first use): an
unreachable database leaves the app running, health says so, and a route that needs it answers
503 (vouch.api) after a bounded wait. A dropped connection is the pool's to mend: it checks
each connection on checkout and reconnects with a backoff. A malformed URL is never sent to
libpq, whose error messages can echo it.

Every use is bounded in time (#119): a server-side statement timeout, TCP keepalives, and, for a
server that freezes without closing its sockets (which neither of those can notice), a wait
bound on every answer, the pool's checkout check included (BoundedConnection).

`vouch-db-setup` creates LangGraph's tables (checkpointer and store), once, at deploy, on one
direct connection outside the pool, so a wrong password or a missing database is reported at once.
It also creates Vouch's own tables, the settings (the developer's model choice, #34; the daily
limit, #35), the daily assessment counts and the usage (#35); nothing else does. Before it has
run, a setting reads as unset, and a write, a count or the usage is DatabaseUnavailable (a 503).
"""

import logging
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Protocol, Self

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore
from psycopg import conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout
from pydantic import SecretStr

from vouch.exit_codes import EXIT_BAD_INPUT, EXIT_UNAVAILABLE
from vouch.settings import DATABASE_URL_ENV, SettingsError, load_settings

log = logging.getLogger(__name__)

POOL_MAX_SIZE = 4
"""Well under db-f1-micro's 25 connections (Cloud SQL's flags page: ~0.5 GB memory, 25)."""
CHECKOUT_TIMEOUT_SECONDS = 5.0
"""How long a route waits for a connection before its 503."""
HEALTH_TIMEOUT_SECONDS = 1.0
HEALTH_CACHE_SECONDS = 5.0
"""Health reuses its last answer this long, so it doesn't check the database on every call."""
CONNECT_TIMEOUT_SECONDS = 5
"""libpq's wait for one connection attempt."""
STATEMENT_TIMEOUT_SECONDS = 5
"""The server cancels a statement that runs longer (a lock wait, a slow server that still
answers); the route then answers 503."""
WAIT_TIMEOUT_SECONDS = 6.0
"""How long this side waits on a connection for any one answer: what bounds a frozen server,
which can't enforce its own statement timeout. Just over that timeout, so a slow server's own
cancel comes first."""
CHECK_TIMEOUT_SECONDS = 1.0
"""The wait for the pool's checkout check (an empty query) on a pooled connection."""
KEEPALIVE_IDLE_SECONDS = 10
KEEPALIVE_INTERVAL_SECONDS = 5
KEEPALIVE_COUNT = 3
"""TCP keepalives (libpq): a peer gone from the network is noticed after about 25 s idle. They
add nothing on a Unix socket (Cloud Run's) or against a frozen server whose kernel still
acknowledges them: WAIT_TIMEOUT_SECONDS covers those."""
RECONNECT_SECONDS = 60.0
"""How long the pool keeps reconnecting in the background before it waits for the next request."""
MALFORMED = f"{DATABASE_URL_ENV} is malformed"
SETTINGS_TABLE = "vouch_settings"
"""Settings a developer changes while the app runs, by name: the model (vouch.model_choice)."""
CREATE_SETTINGS = (
    f"CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE} (name text PRIMARY KEY, value text NOT NULL)"
)

COUNTS_TABLE = "vouch_assessment_counts"
"""How many assessments each user started on each UTC day (the daily limit, vouch.spending)."""
USAGE_TABLE = "vouch_usage"
"""Each assessment's tokens and cost (vouch.spending)."""
CREATE_COUNTS = (
    f"CREATE TABLE IF NOT EXISTS {COUNTS_TABLE} (user_id text NOT NULL, day date NOT NULL, "
    "count integer NOT NULL, PRIMARY KEY (user_id, day))"
)
CREATE_USAGE = (
    f"CREATE TABLE IF NOT EXISTS {USAGE_TABLE} (id bigserial PRIMARY KEY, "
    "user_id text NOT NULL, control_id text NOT NULL, model text NOT NULL, "
    "input_tokens bigint NOT NULL, output_tokens bigint NOT NULL, "
    "reported_cost_usd double precision NOT NULL, estimated_cost_usd double precision NOT NULL, "
    "estimated boolean NOT NULL, cost_complete boolean NOT NULL, at timestamptz NOT NULL)"
)

Connection = psycopg.Connection[DictRow]

CONNECTION_KWARGS: dict[str, Any] = {
    "autocommit": True,
    "row_factory": dict_row,
    "connect_timeout": CONNECT_TIMEOUT_SECONDS,
    "options": f"-c statement_timeout={STATEMENT_TIMEOUT_SECONDS}s",
    "keepalives": 1,
    "keepalives_idle": KEEPALIVE_IDLE_SECONDS,
    "keepalives_interval": KEEPALIVE_INTERVAL_SECONDS,
    "keepalives_count": KEEPALIVE_COUNT,
}
"""Every connection's settings: LangGraph's (autocommit, dict rows) and the bounds (#119)."""


NO_ANSWER = "the database didn't answer within {bound}s"
"""The OperationalError a bounded wait ends with."""


class BoundedConnection(psycopg.Connection[DictRow]):
    """psycopg's Connection, waiting at most `wait_timeout` seconds for any one answer (#119).

    A frozen server (a paused instance, a stuck proxy) keeps its socket open and its kernel
    acknowledges what it's sent, so psycopg would wait as long as the freeze lasts. Past the
    bound the connection is closed (the pool then discards it) and the wait raises
    OperationalError, a DatabaseUnavailable to the caller. Every query, fetch and the pool's
    check go through `wait`; psycopg's own `wait` takes the timeout, and closes the connection
    the same way when a cancel goes unanswered.

    It relies on two psycopg details outside its public docs, read in the installed 3.3.8
    source: `Connection.wait`'s `timeout`, and `psycopg.errors._WaitTimeout` (private). The
    dependency is capped below 3.4 in pyproject.toml, and a test fails if either changes.
    """

    wait_timeout: float = WAIT_TIMEOUT_SECONDS

    def wait(self, gen: Any, interval: float = 0.1, timeout: float | None = None) -> Any:
        bound = self.wait_timeout if timeout is None else min(timeout, self.wait_timeout)
        try:
            return super().wait(gen, interval=interval, timeout=bound)
        except psycopg.errors._WaitTimeout:  # private: raised only for `timeout` (see above)
            self.pgconn.finish()
            raise psycopg.OperationalError(NO_ANSWER.format(bound=bound)) from None


def check_within_bound(conn: BoundedConnection) -> None:
    """The pool's checkout check (`check_connection`), waiting CHECK_TIMEOUT_SECONDS at most."""
    before = getattr(conn, "wait_timeout", WAIT_TIMEOUT_SECONDS)  # a test's fake may not have one
    conn.wait_timeout = CHECK_TIMEOUT_SECONDS
    try:
        ConnectionPool.check_connection(conn)
    finally:
        conn.wait_timeout = before


class Connect(Protocol):
    """psycopg's `Connection.connect`: the seam a test swaps for a flaky fake."""

    def __call__(self, conninfo: str, **kwargs: Any) -> Connection: ...


class DatabaseUnavailable(RuntimeError):
    """The database can't be used just now. The message never holds the URL or its password."""


class DatabaseMisconfigured(DatabaseUnavailable):
    """VOUCH_DATABASE_URL is malformed: no retry or wait will help."""


class Database:
    """Connections to the database at `url`, from a pool opened on first use."""

    def __init__(
        self,
        url: SecretStr,
        *,
        connect: Connect = BoundedConnection.connect,
        timeout: float = CHECKOUT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self._connect = connect
        self._clock = clock
        self._secrets = _secrets_in(url.get_secret_value())
        self._malformed = not _parses(url.get_secret_value())
        self._pool: ConnectionPool[Any] | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._last_failure: str | None = None
        self._health: tuple[float, bool] | None = None

    def __repr__(self) -> str:
        return f"Database(url={self.url!r})"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Close the pool and its connections; the app does this at shutdown."""
        with self._lock:
            self._closed = True
            if self._pool is not None:
                self._pool.close()

    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[Connection]:
        """A pooled connection, within `timeout` seconds. Raises DatabaseUnavailable, also when
        the connection drops while in use."""
        pool = self._open_pool()
        wait = self.timeout if timeout is None else timeout
        try:
            conn = pool.getconn(timeout=wait)
        except (PoolTimeout, PoolClosed) as error:  # PoolClosed: the app is shutting down
            reason = self._last_failure or self._redacted(str(error))
            log.warning("database unavailable after %ss: %s", wait, reason)
            raise DatabaseUnavailable(f"the database is unavailable: {reason}") from None
        try:
            with self._unavailable_on_drop():
                yield conn
        finally:
            pool.putconn(conn)

    def reachable(self) -> bool:
        """Whether a connection can be had; reused for HEALTH_CACHE_SECONDS."""
        now = self._clock()
        if self._health is not None and now - self._health[0] < HEALTH_CACHE_SECONDS:
            return self._health[1]
        try:
            with self.connection(timeout=HEALTH_TIMEOUT_SECONDS):
                answer = True
        except DatabaseUnavailable:
            answer = False
        self._health = (now, answer)
        return answer

    def setting(self, name: str) -> str | None:
        """The stored value of setting `name`; None when it was never set. Raises
        DatabaseUnavailable."""
        with self.connection() as conn:
            try:
                row = conn.execute(
                    f"SELECT value FROM {SETTINGS_TABLE} WHERE name = %s", (name,)
                ).fetchone()
            except psycopg.errors.UndefinedTable:  # vouch-db-setup hasn't run: nothing stored
                return None
        return row["value"] if row else None

    def set_setting(self, name: str, value: str) -> None:
        """Store `value` as setting `name`, replacing any before. Raises DatabaseUnavailable."""
        with self.connection() as conn, self._needs_setup(SETTINGS_TABLE):
            conn.execute(
                f"INSERT INTO {SETTINGS_TABLE} (name, value) VALUES (%s, %s) "
                "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value",
                (name, value),
            )

    def take_assessment(
        self, user_id: str, day: date, limit_setting: str, default_limit: int
    ) -> tuple[bool, int]:
        """Count one assessment for the user's day if they started fewer than the limit (setting
        `limit_setting`, else `default_limit`): the limit read and the count taken in one
        statement, so nothing changes between them and two at once can't both slip under it.
        (counted, the limit). Raises DatabaseUnavailable."""
        with self.connection() as conn, self._needs_setup(COUNTS_TABLE):
            row = conn.execute(
                "WITH lim AS (SELECT coalesce((SELECT value::integer FROM "
                f"{SETTINGS_TABLE} WHERE name = %(setting)s), %(default)s) AS n), "
                f"taken AS (INSERT INTO {COUNTS_TABLE} AS c (user_id, day, count) "
                "SELECT %(user_id)s, %(day)s, 1 FROM lim WHERE lim.n >= 1 "
                "ON CONFLICT (user_id, day) DO UPDATE SET count = c.count + 1 "
                "WHERE c.count < (SELECT n FROM lim) RETURNING c.count) "
                "SELECT (SELECT n FROM lim) AS lim, EXISTS (SELECT 1 FROM taken) AS taken",
                {
                    "setting": limit_setting,
                    "default": default_limit,
                    "user_id": user_id,
                    "day": day,
                },
            ).fetchone()
        assert row is not None  # a SELECT of two values always gives one row
        return row["taken"], row["lim"]

    def record_usage(
        self,
        *,
        user_id: str,
        control_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        reported_cost_usd: float,
        estimated_cost_usd: float,
        estimated: bool,
        cost_complete: bool,
        at: datetime,
    ) -> None:
        """Keep one assessment's usage (vouch.spending's UsageRecord). Raises
        DatabaseUnavailable."""
        with self.connection() as conn, self._needs_setup(USAGE_TABLE):
            conn.execute(
                f"INSERT INTO {USAGE_TABLE} (user_id, control_id, model, input_tokens, "
                "output_tokens, reported_cost_usd, estimated_cost_usd, estimated, cost_complete, "
                "at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    user_id,
                    control_id,
                    model,
                    input_tokens,
                    output_tokens,
                    reported_cost_usd,
                    estimated_cost_usd,
                    estimated,
                    cost_complete,
                    at,
                ),
            )

    def usage_totals(self, fake_model: str) -> dict[str, dict[str, Any]]:
        """Every assessment's usage added up, by "openrouter" and "fake" (runs on `fake_model`);
        a kind with no runs is left out. Raises DatabaseUnavailable."""
        with self.connection() as conn, self._needs_setup(USAGE_TABLE):
            rows = conn.execute(
                "SELECT CASE WHEN model = %s THEN 'fake' ELSE 'openrouter' END AS kind, "
                "count(*) AS assessments, sum(input_tokens)::bigint AS input_tokens, "
                "sum(output_tokens)::bigint AS output_tokens, sum(reported_cost_usd) AS reported_cost_usd, "
                "sum(estimated_cost_usd) AS estimated_cost_usd, "
                "count(*) FILTER (WHERE NOT cost_complete) AS incomplete "
                f"FROM {USAGE_TABLE} GROUP BY kind",
                (fake_model,),
            ).fetchall()
        return {row.pop("kind"): row for row in rows}

    @contextmanager
    def _needs_setup(self, table: str) -> Iterator[None]:
        """A table vouch-db-setup makes: missing, it's DatabaseUnavailable, and the log says so."""
        try:
            yield
        except psycopg.errors.UndefinedTable:
            log.error("no %s table: run vouch-db-setup on this database", table)
            raise DatabaseUnavailable(f"no {table} table: run vouch-db-setup") from None

    @contextmanager
    def checkpointer(self) -> Iterator[PostgresSaver]:
        """LangGraph's Postgres checkpointer on the pool. Raises DatabaseUnavailable."""
        pool = self._open_pool()
        with self._unavailable_on_drop():
            yield PostgresSaver(pool)

    def setup(self) -> None:
        """Create or migrate LangGraph's tables; safe to run again.

        One direct connection, no pool and no retry, so its failure is the real reason.
        Raises DatabaseMisconfigured (malformed URL) or DatabaseUnavailable.
        """
        if self._malformed:
            raise DatabaseMisconfigured(MALFORMED)
        try:
            conn = self._connect(self.url.get_secret_value(), **CONNECTION_KWARGS)
        except psycopg.Error as error:
            reason = self._redacted(str(error))
            raise DatabaseUnavailable(f"cannot connect to the database: {reason}") from None
        try:
            with self._unavailable_on_drop():
                PostgresSaver(conn).setup()
                PostgresStore(conn).setup()
                conn.execute(CREATE_SETTINGS)
                conn.execute(CREATE_COUNTS)
                conn.execute(CREATE_USAGE)
        finally:
            conn.close()

    def _open_pool(self) -> ConnectionPool[Any]:
        if self._malformed:
            raise DatabaseMisconfigured(MALFORMED)
        with self._lock:
            if self._closed:
                raise DatabaseUnavailable("the database is closed")
            if self._pool is None:
                self._pool = ConnectionPool(
                    self.url.get_secret_value(),
                    connection_class=self._connection_class(),
                    kwargs=dict(CONNECTION_KWARGS),
                    min_size=0,
                    max_size=POOL_MAX_SIZE,
                    open=False,
                    check=check_within_bound,
                    timeout=self.timeout,
                    reconnect_timeout=RECONNECT_SECONDS,
                    name="vouch",
                )
                self._pool.open()
            return self._pool

    def _connection_class(self) -> type[Connection]:
        """psycopg's Connection, opened through this Database's seam (the pool calls it)."""
        database = self

        class PooledConnection(BoundedConnection):
            @classmethod
            def connect(cls, conninfo: str = "", **kwargs: Any) -> Connection:  # type: ignore[override]
                return database._open_one(conninfo, **kwargs)

        return PooledConnection

    def _open_one(self, url: str, **kwargs: Any) -> Connection:
        """One connection for the pool. Its error is redacted, as the pool logs it."""
        try:
            conn = self._connect(url, **kwargs)
        except psycopg.Error as error:
            self._last_failure = self._redacted(str(error))
            raise psycopg.OperationalError(self._last_failure) from None
        self._last_failure = None
        return conn

    @contextmanager
    def _unavailable_on_drop(self) -> Iterator[None]:
        """A connection that drops while in use (an instance restart): DatabaseUnavailable."""
        try:
            yield
        except psycopg.OperationalError as error:
            reason = self._redacted(str(error))
            log.warning("database connection failed in use: %s", reason)
            raise DatabaseUnavailable(f"the database connection failed: {reason}") from None

    def _redacted(self, message: str) -> str:
        for secret in self._secrets:
            message = message.replace(secret, "**********")
        return message.strip()


def _parses(url: str) -> bool:
    try:
        conninfo.conninfo_to_dict(url)
    except psycopg.Error:
        return False
    return True


def _secrets_in(url: str) -> tuple[str, ...]:
    """The URL, and its password as written and decoded, longest first."""
    found = {url}
    try:
        found.add(conninfo.conninfo_to_dict(url).get("password") or "")
    except psycopg.Error:
        pass
    try:
        raw = urllib.parse.urlsplit(url).password or ""
    except ValueError:
        raw = ""
    found |= {raw, urllib.parse.unquote(raw), urllib.parse.quote(raw, safe="")}
    return tuple(sorted((s for s in found if s), key=len, reverse=True))


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`vouch-db-setup`: create LangGraph's tables in VOUCH_DATABASE_URL's database."""
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(f"usage: vouch-db-setup  (reads {DATABASE_URL_ENV})", file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        url = load_settings(env).database_url
    except SettingsError as error:
        print(f"vouch-db-setup: {error}", file=sys.stderr)
        return EXIT_BAD_INPUT
    if url is None:
        print(f"vouch-db-setup: set {DATABASE_URL_ENV}", file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        with Database(url) as db:
            db.setup()
    except DatabaseMisconfigured as error:
        print(f"vouch-db-setup: {error}", file=sys.stderr)
        return EXIT_BAD_INPUT
    except DatabaseUnavailable as error:
        print(f"vouch-db-setup: {error}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    print("vouch-db-setup: LangGraph's tables are ready (checkpointer and store)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
