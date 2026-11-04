"""Vouch's Postgres database: the one module that opens connections (#33).

Connections come from one psycopg pool with LangGraph's Postgres settings, `autocommit=True` and
`row_factory=dict_row` (langgraph-checkpoint-postgres's README). Cloud SQL has no transaction
pooler, so one URL (VOUCH_DATABASE_URL) serves the app and the setup command: locally through the
Cloud SQL Auth Proxy, on Cloud Run through the instance's Unix socket
(`host=/cloudsql/<connection name>`).

Nothing connects at start (the pool's `min_size` is 0, and it opens on first use): an
unreachable database leaves the app running, health says so, and a route that needs it answers
503 (vouch.api). A dropped connection is the pool's to mend: it checks each connection on
checkout and reconnects with a backoff. A refusal that a retry can't change (a wrong password, a
missing database) fails at once, and a malformed URL is never sent to libpq's error messages.

`vouch-db-setup` creates LangGraph's tables (checkpointer and store), once, at deploy.
"""

import logging
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol, Self

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore
from psycopg import conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool, PoolTimeout
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
RECONNECT_SECONDS = 60.0
"""How long the pool keeps reconnecting in the background before it waits for the next request."""
POLL_SECONDS = 0.1
"""A checkout looks this often for a refusal that waiting can't change."""

MALFORMED = f"{DATABASE_URL_ENV} is malformed"
# libpq's words for a refusal that a retry can't change. It gives no SQLSTATE at connect time
# (checked against Postgres 16), so the message is all there is.
REFUSALS = (
    "password authentication failed",
    "authentication failed",
    "does not exist",
    "no pg_hba.conf entry",
    "permission denied",
)

Connection = psycopg.Connection[DictRow]


class Connect(Protocol):
    """psycopg's `Connection.connect`: the seam a test swaps for a flaky fake."""

    def __call__(self, conninfo: str, **kwargs: Any) -> Connection: ...


class DatabaseUnavailable(RuntimeError):
    """The database can't be used just now. The message never holds the URL or its password."""


class DatabaseMisconfigured(DatabaseUnavailable):
    """A malformed URL, a wrong password or a missing database: no retry or wait will help."""


class Database:
    """Connections to the database at `url`, from a pool opened on first use."""

    def __init__(
        self,
        url: SecretStr,
        *,
        connect: Connect = psycopg.Connection.connect,
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
        self._refusal: str | None = None
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
        conn = self._checkout(pool, self.timeout if timeout is None else timeout)
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

    @contextmanager
    def checkpointer(self) -> Iterator[PostgresSaver]:
        """LangGraph's Postgres checkpointer on the pool. Raises DatabaseUnavailable."""
        pool = self._open_pool()
        with self._unavailable_on_drop():
            yield PostgresSaver(pool)

    def setup(self) -> None:
        """Create or migrate LangGraph's tables; safe to run again. Raises DatabaseUnavailable."""
        with self.connection() as conn:
            PostgresSaver(conn).setup()
            PostgresStore(conn).setup()

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
                    kwargs={
                        "autocommit": True,
                        "row_factory": dict_row,
                        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
                    },
                    min_size=0,
                    max_size=POOL_MAX_SIZE,
                    open=False,
                    check=ConnectionPool.check_connection,
                    timeout=self.timeout,
                    reconnect_timeout=RECONNECT_SECONDS,
                    name="vouch",
                )
                self._pool.open()
            return self._pool

    def _connection_class(self) -> type[Connection]:
        """psycopg's Connection, opened through this Database's seam (the pool calls it)."""
        database = self

        class PooledConnection(psycopg.Connection[DictRow]):
            @classmethod
            def connect(cls, conninfo: str = "", **kwargs: Any) -> Connection:  # type: ignore[override]
                return database._open_one(conninfo, **kwargs)

        return PooledConnection

    def _open_one(self, url: str, **kwargs: Any) -> Connection:
        """One connection for the pool. Its error is redacted, as the pool logs it."""
        try:
            conn = self._connect(url, **kwargs)
        except psycopg.Error as error:
            reason = self._redacted(str(error))
            self._last_failure = reason
            refused = not isinstance(error, psycopg.OperationalError) or any(
                words in reason for words in REFUSALS
            )
            self._refusal = reason if refused else None
            raise psycopg.OperationalError(reason) from None
        self._last_failure = self._refusal = None
        return conn

    def _checkout(self, pool: ConnectionPool[Any], timeout: float) -> Connection:
        """Wait for the pool's connection, but stop at once on a refusal waiting can't change."""
        deadline = self._clock() + timeout
        while True:
            remaining = deadline - self._clock()
            try:
                return pool.getconn(timeout=max(min(POLL_SECONDS, remaining), 0.001))
            except PoolTimeout:
                pass
            if self._refusal is not None:
                log.warning("database refused the connection: %s", self._refusal)
                raise DatabaseMisconfigured(f"the database refused the connection: {self._refusal}")
            if self._clock() >= deadline:
                reason = self._last_failure or "no answer"
                log.warning("database unavailable after %ss: %s", timeout, reason)
                raise DatabaseUnavailable(f"the database is unavailable after {timeout}s: {reason}")

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
        with Database(url, timeout=CHECKOUT_TIMEOUT_SECONDS) as db:  # read now: tests shorten it
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
