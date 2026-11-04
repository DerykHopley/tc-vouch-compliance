"""Vouch's Postgres database: the one module that opens connections (#33).

Every connection uses LangGraph's Postgres settings, `autocommit=True` and `row_factory=dict_row`
(langgraph-checkpoint-postgres's README). Cloud SQL has no transaction pooler, so one URL
(VOUCH_DATABASE_URL) serves the app and the setup command: locally through the Cloud SQL Auth
Proxy, on Cloud Run through the instance's Unix socket (`host=/cloudsql/<connection name>`).

Nothing connects at start: an unreachable database leaves the app running, health says so, and a
route that needs it answers 503 (vouch.api). A failed connect is retried with a backoff, as an
instance restart or maintenance drops connections for a moment.

`vouch-db-setup` creates LangGraph's tables (checkpointer and store), once, at deploy.
"""

import logging
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore
from psycopg import conninfo
from psycopg.rows import DictRow, dict_row
from pydantic import SecretStr

from vouch.settings import DATABASE_URL_ENV, SettingsError, load_settings

log = logging.getLogger(__name__)

ATTEMPTS = 3
BACKOFF = 0.5
"""Seconds before the second attempt; each later wait doubles."""
CONNECT_TIMEOUT = 5
"""Seconds libpq waits for one attempt."""
EXIT_BAD_INPUT = 2
EXIT_UNAVAILABLE = 3

Connection = psycopg.Connection[DictRow]


class DatabaseUnavailable(RuntimeError):
    """The database can't be reached. The message never holds the URL or its password."""


class Database:
    """Connections to the database at `url`, each opened for one use and closed after it."""

    def __init__(
        self,
        url: SecretStr,
        *,
        connect: Callable[..., Any] = psycopg.connect,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.url = url
        self._connect_fn = connect
        self._sleep = sleep

    def __repr__(self) -> str:
        return "Database(url=SecretStr('**********'))"

    @contextmanager
    def connection(self, attempts: int = ATTEMPTS) -> Iterator[Connection]:
        """A connection, retried up to `attempts` times. Raises DatabaseUnavailable."""
        conn = self._connect(attempts)
        try:
            yield conn
        finally:
            conn.close()

    def reachable(self) -> bool:
        """One attempt, no retry: health answers quickly."""
        try:
            with self.connection(attempts=1):
                return True
        except DatabaseUnavailable:
            return False

    @contextmanager
    def checkpointer(self) -> Iterator[PostgresSaver]:
        """LangGraph's Postgres checkpointer on one connection."""
        with self.connection() as conn:
            yield PostgresSaver(conn)

    @contextmanager
    def store(self) -> Iterator[PostgresStore]:
        """LangGraph's Postgres store on one connection."""
        with self.connection() as conn:
            yield PostgresStore(conn)

    def setup(self) -> None:
        """Create or migrate LangGraph's tables; safe to run again."""
        with self.connection() as conn:
            PostgresSaver(conn).setup()
            PostgresStore(conn).setup()

    def _connect(self, attempts: int) -> Connection:
        url = self.url.get_secret_value()
        for attempt in range(1, attempts + 1):
            try:
                return self._connect_fn(
                    url, autocommit=True, row_factory=dict_row, connect_timeout=CONNECT_TIMEOUT
                )
            except psycopg.Error as error:
                reason = _redacted(str(error), url)
                if attempt == attempts:
                    log.warning("database unavailable after %d attempts: %s", attempts, reason)
                    raise DatabaseUnavailable(
                        f"the database is unavailable after {attempts} attempt(s): {reason}"
                    ) from None
                wait = BACKOFF * 2 ** (attempt - 1)
                log.info("database connect attempt %d failed, retrying in %ss", attempt, wait)
                self._sleep(wait)
        raise AssertionError("unreachable")  # attempts >= 1


def _redacted(message: str, url: str) -> str:
    """`message` without the URL or its password (libpq may echo parts of a bad URL)."""
    try:
        password = conninfo.conninfo_to_dict(url).get("password")
    except psycopg.Error:
        password = None
    for secret in (url, password):
        if secret:
            message = message.replace(str(secret), "**********")
    return message.strip()


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """`vouch-db-setup`: create LangGraph's tables in VOUCH_DATABASE_URL's database."""
    args = sys.argv[1:] if argv is None else argv
    if args:
        print("usage: vouch-db-setup  (reads VOUCH_DATABASE_URL)", file=sys.stderr)
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
        Database(url, sleep=sleep).setup()
    except DatabaseUnavailable as error:
        print(f"vouch-db-setup: {error}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    print("vouch-db-setup: LangGraph's tables are ready (checkpointer and store)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
