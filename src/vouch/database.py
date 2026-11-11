"""Vouch's Postgres database: the one module that opens connections (#33).

Connections come from one psycopg pool with LangGraph's Postgres settings, `autocommit=True` and
`row_factory=dict_row` (langgraph-checkpoint-postgres's README). Cloud SQL has no transaction
pooler, so one URL (VOUCH_DATABASE_URL) serves the app and the setup command: locally through the
Cloud SQL Auth Proxy, on Cloud Run through the instance's Unix socket
(`host=/cloudsql/<connection name>`).

Nothing connects at start (the pool's `min_size` is 0, and it opens on first use): an
unreachable database leaves the app running, health says so, and a route that needs it answers
503 (vouch.api) after a bounded wait. Health also says when the database answers but
vouch-db-setup never ran (`needs-setup`, #123). A dropped connection is the pool's to mend: it checks
each connection on checkout and reconnects with a backoff. A malformed URL is never sent to
libpq, whose error messages can echo it.

Every use is bounded in time (#119): a server-side statement timeout, TCP keepalives, and, for a
server that freezes without closing its sockets (which neither of those can notice), a wait
bound on every answer, the pool's checkout check included (BoundedConnection).

`vouch-db-setup` creates LangGraph's tables (checkpointer and store), once, at deploy, on one
direct connection outside the pool, so a wrong password or a missing database is reported at once.
It also creates Vouch's own tables, the settings (the developer's model choice, #34; the daily
limit, #35), the daily assessment counts, the usage (#35), the append-only audit trail (#39), the
remediation items the lead decided on (#43), the improvement plan's marks on them (#410), each
profile's Goal (#369), its review rounds (#402), the open questions on its controls (#414), the
evidence documents added to it (#584) and the companies made in the app (#385); nothing else
does. Before it has run, a setting reads as unset, a Goal as Audit readiness, the plan as no
marks, the review rounds as the first one, open, the open questions and added documents as none,
and no company is found; and a write, a count, the usage, a review, a remediation decision, a
plan mark, a Goal change, a round started or closed, a question logged or answered, a document
added or withdrawn, or a new company is DatabaseUnavailable (a 503).
"""

import hashlib
import json
import logging
import re
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Literal, Protocol, Self, get_args

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.store.postgres import PostgresStore
from psycopg import conninfo
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout
from pydantic import SecretStr

from vouch.exit_codes import EXIT_BAD_INPUT, EXIT_UNAVAILABLE
from vouch.settings import DATABASE_URL_ENV, load_database_url

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
PROFILE_PASS_COUNTS_TABLE = "vouch_profile_pass_counts"
"""How many profile passes each user ran on each UTC day (their own daily limit, #371)."""
USAGE_TABLE = "vouch_usage"
"""Each assessment's tokens and cost (vouch.spending)."""
CREATE_COUNTS = (
    f"CREATE TABLE IF NOT EXISTS {COUNTS_TABLE} (user_id text NOT NULL, day date NOT NULL, "
    "count integer NOT NULL, PRIMARY KEY (user_id, day))"
)
CREATE_PROFILE_PASS_COUNTS = (
    f"CREATE TABLE IF NOT EXISTS {PROFILE_PASS_COUNTS_TABLE} (user_id text NOT NULL, "
    "day date NOT NULL, count integer NOT NULL, PRIMARY KEY (user_id, day))"
)
CREATE_USAGE = (
    f"CREATE TABLE IF NOT EXISTS {USAGE_TABLE} (id bigserial PRIMARY KEY, "
    "user_id text NOT NULL, control_id text NOT NULL, model text NOT NULL, "
    "input_tokens bigint NOT NULL, output_tokens bigint NOT NULL, "
    "reported_cost_usd double precision NOT NULL, estimated_cost_usd double precision NOT NULL, "
    "estimated boolean NOT NULL, cost_complete boolean NOT NULL, at timestamptz NOT NULL)"
)

AUDIT_TABLE = "vouch_audit_trail"
"""Every reviewed verdict and override (vouch.audit), append-only, in its decision's transaction."""
AuditAction = Literal[
    "accept",
    "reject",
    "override",
    "confirm",
    "approve",
    "discard",
    "goal",
    "round-start",
    "round-close",
    "priority",
    "done",
    "question",
    "answer",
    "document-add",
    "document-withdraw",
]
"""What an audit entry records the compliance lead doing (vouch.audit's Action): `goal` is a
change of the profile's Goal (#369); `round-start` and `round-close` a review round started or
closed (#402); `priority` and `done` a remediation item's priority changed or marked done, in the
improvement plan (#410); `question` and `answer` an open question logged on a control and its
answer recorded (#414); `document-add` and `document-withdraw` an evidence document (or a new
version of one) added to the profile, or withdrawn (#584)."""
Goal = Literal["audit-readiness", "internal-improvement"]
"""What a compliance profile is used for (#369, vouch.goal): kept per user and profile."""
Priority = Literal["high", "medium", "low"]
"""A remediation item's priority in the improvement plan (#410, vouch.plan)."""
AUDIT_COLUMNS = (
    "user_id",
    "reviewer_name",
    "at",
    "profile_id",
    "control_id",
    "thread_id",
    "action",
    "requirement_id",
    "note",
    "verdict_before",
    "verdict_after",
    "evidence",
    "model",
    "prompt_version",
    "goal_before",
    "goal_after",
    "round_name",
)
CREATE_AUDIT = (
    f"CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (id bigserial PRIMARY KEY, "
    "user_id text NOT NULL, reviewer_name text NOT NULL, at timestamptz NOT NULL, "
    "profile_id text NOT NULL, "
    "control_id text NOT NULL, thread_id text NOT NULL, action text NOT NULL "
    f"CHECK (action IN ({', '.join(repr(a) for a in get_args(AuditAction))})), "
    "requirement_id text, note text NOT NULL, verdict_before text, verdict_after text, "
    "evidence jsonb NOT NULL, model text NOT NULL, prompt_version text NOT NULL, "
    "goal_before text, goal_after text, round_name text)"
)
CREATE_AUDIT_INDEX = (
    f"CREATE INDEX IF NOT EXISTS {AUDIT_TABLE}_by_owner ON {AUDIT_TABLE} "
    "(user_id, profile_id, at DESC, id DESC)"
)
CREATE_AUDIT_GUARD = (
    "CREATE OR REPLACE FUNCTION vouch_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS "
    "$$ BEGIN RAISE EXCEPTION 'the audit trail is append-only'; END $$"
)
CREATE_AUDIT_TRIGGERS = (
    (
        f"CREATE OR REPLACE TRIGGER {AUDIT_TABLE}_append_only BEFORE UPDATE OR DELETE ON "
        f"{AUDIT_TABLE} FOR EACH ROW EXECUTE FUNCTION vouch_audit_append_only()"
    ),
    (
        f"CREATE OR REPLACE TRIGGER {AUDIT_TABLE}_no_truncate BEFORE TRUNCATE ON "
        f"{AUDIT_TABLE} FOR EACH STATEMENT EXECUTE FUNCTION vouch_audit_append_only()"
    ),
)
"""The table refuses an update, a delete or a truncate, whoever asks (dropping it still works:
that's vouch-db-setup's owner's, not the app's)."""
ADD_AUDIT_REVIEWER_NAME = (
    "DO $$ BEGIN "
    "IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = "
    f"current_schema() AND table_name = '{AUDIT_TABLE}' AND column_name = 'reviewer_name') THEN "
    f"ALTER TABLE {AUDIT_TABLE} ADD COLUMN reviewer_name text; "
    f"ALTER TABLE {AUDIT_TABLE} DISABLE TRIGGER {AUDIT_TABLE}_append_only; "
    f"UPDATE {AUDIT_TABLE} SET reviewer_name = user_id; "
    f"ALTER TABLE {AUDIT_TABLE} ENABLE TRIGGER {AUDIT_TABLE}_append_only; "
    f"ALTER TABLE {AUDIT_TABLE} ALTER COLUMN reviewer_name SET NOT NULL; "
    "END IF; END $$"
)
"""A trail made before #154 gains the reviewer's name, the user id on its rows (no way to know
the name then). One statement, so one transaction: the guard is off only inside it, for this
one fill, and no other session sees it off. Runs after CREATE_AUDIT_TRIGGERS, so the trigger
it lifts is there."""
ADD_AUDIT_GOAL_COLUMNS = (
    f"ALTER TABLE {AUDIT_TABLE} ADD COLUMN IF NOT EXISTS goal_before text, "
    "ADD COLUMN IF NOT EXISTS goal_after text"
)
"""A trail made before #369 gains the goal columns, nullable: every row keeps them empty, so
none is rewritten and the append-only guard stays on (an ALTER fires no row trigger). Once
they are there, a second run does nothing."""
ADD_AUDIT_ROUND_NAME = f"ALTER TABLE {AUDIT_TABLE} ADD COLUMN IF NOT EXISTS round_name text"
"""A trail made before #402 gains the round's name, nullable, as the goal columns were added:
no row rewritten, the append-only guard kept, and nothing done on a second run."""
AUDIT_ACTION_CHECK = f"{AUDIT_TABLE}_action_check"
"""The name Postgres gave CREATE_AUDIT's action CHECK (table, column, "check")."""
REPLACE_AUDIT_ACTION_CHECK = (
    "DO $$ BEGIN "
    "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = "
    f"'{AUDIT_TABLE}'::regclass AND conname = '{AUDIT_ACTION_CHECK}' "
    "AND pg_get_constraintdef(oid) LIKE ALL (ARRAY["
    + ", ".join(f"'%''{a}''%'" for a in get_args(AuditAction))
    + "])) THEN "
    f"ALTER TABLE {AUDIT_TABLE} DROP CONSTRAINT IF EXISTS {AUDIT_ACTION_CHECK}, "
    f"ADD CONSTRAINT {AUDIT_ACTION_CHECK} "
    f"CHECK (action IN ({', '.join(repr(a) for a in get_args(AuditAction))})); "
    "END IF; END $$"
)
"""A trail made before an action was added (`confirm`, #372; `goal`, #369; the rounds', #402;
the plan's, #410; the open questions', #414; the added documents', #584)
has a CHECK refusing it: replaced
by one naming every AuditAction. No row is changed (the trail's guard is on update and delete,
which an ALTER doesn't fire), and once the CHECK names them all, a second run does nothing."""
REMEDIATION_TABLE = "vouch_remediation_items"
"""Each remediation item the lead approved or discarded (vouch.remediation), per user, profile,
assessment thread and requirement: one decision each, written in its audit entry's transaction.
A draft lives in its assessment's thread until then."""
REMEDIATION_COLUMNS = (
    "user_id",
    "profile_id",
    "thread_id",
    "requirement_id",
    "status",
    "missing",
    "owner_role",
    "due_date",
    "decided_at",
)
CREATE_REMEDIATION = (
    f"CREATE TABLE IF NOT EXISTS {REMEDIATION_TABLE} (user_id text NOT NULL, "
    "profile_id text NOT NULL, thread_id text NOT NULL, requirement_id text NOT NULL, "
    "status text NOT NULL CHECK (status IN ('approved', 'discarded')), missing text NOT NULL, "
    "owner_role text NOT NULL, due_date date NOT NULL, decided_at timestamptz NOT NULL, "
    "PRIMARY KEY (user_id, profile_id, thread_id, requirement_id))"
)
CREATE_REMEDIATION_GUARD = (
    "CREATE OR REPLACE FUNCTION vouch_remediation_final() RETURNS trigger LANGUAGE plpgsql AS "
    "$$ BEGIN RAISE EXCEPTION 'a remediation decision is final'; END $$"
)
CREATE_REMEDIATION_TRIGGERS = (
    (
        f"CREATE OR REPLACE TRIGGER {REMEDIATION_TABLE}_final BEFORE UPDATE OR DELETE ON "
        f"{REMEDIATION_TABLE} FOR EACH ROW EXECUTE FUNCTION vouch_remediation_final()"
    ),
    (
        f"CREATE OR REPLACE TRIGGER {REMEDIATION_TABLE}_no_truncate BEFORE TRUNCATE ON "
        f"{REMEDIATION_TABLE} FOR EACH STATEMENT EXECUTE FUNCTION vouch_remediation_final()"
    ),
)
"""A decision is kept as made, as its audit entry is: the table refuses an update, a delete or
a truncate, whoever asks."""
COMPANY_PROFILES_TABLE = "vouch_company_profiles"
"""Each company made in the app (#385): its id, the fields its compliance profile is rebuilt and
checked from on load (vouch.company_profiles), never a server path, who made it and when; and,
once a developer removed it (#418), by whom and when. A removed row is kept, never read back,
so its id is never used again."""
COMPANY_PROFILE_COLUMNS = ("id", "profile", "created_by", "created_at")
CREATE_COMPANY_PROFILES = (
    f"CREATE TABLE IF NOT EXISTS {COMPANY_PROFILES_TABLE} (id text PRIMARY KEY, "
    "profile jsonb NOT NULL, created_by text NOT NULL, created_at timestamptz NOT NULL)"
)
ADD_COMPANY_PROFILE_REMOVAL = (
    f"ALTER TABLE {COMPANY_PROFILES_TABLE} ADD COLUMN IF NOT EXISTS removed_at timestamptz, "
    "ADD COLUMN IF NOT EXISTS removed_by text"
)
"""#418's columns, on a table made before them: a company already stored stays live (null)."""
LIVE_COMPANY = "removed_at IS NULL"
REMOVAL_COLUMNS = ("removed_at", "removed_by")
"""The columns ADD_COMPANY_PROFILE_REMOVAL adds: missing, vouch-db-setup hasn't run since #418."""
PLAN_TABLE = "vouch_remediation_plan"
"""The improvement plan's marks on approved remediation items (vouch.plan, #410), per user,
profile, assessment thread and requirement, beside the decisions (whose table stays final): the
lead's priority (null: the default from the verdict) and the done mark (null: not done). Each
write in its audit entry's transaction."""
PLAN_COLUMNS = (
    "user_id",
    "profile_id",
    "thread_id",
    "requirement_id",
    "control_id",
    "priority",
    "done_at",
    "done_note",
)
CREATE_PLAN = (
    f"CREATE TABLE IF NOT EXISTS {PLAN_TABLE} (user_id text NOT NULL, "
    "profile_id text NOT NULL, thread_id text NOT NULL, requirement_id text NOT NULL, "
    "control_id text NOT NULL, priority text "
    f"CHECK (priority IN ({', '.join(repr(p) for p in get_args(Priority))})), "
    "done_at timestamptz, done_note text, "
    "PRIMARY KEY (user_id, profile_id, thread_id, requirement_id))"
)
GOALS_TABLE = "vouch_profile_goals"
"""Each user's Goal under each compliance profile (vouch.goal, #369), the latest change; none
kept is Audit readiness. Written in its audit entry's transaction."""
CREATE_GOALS = (
    f"CREATE TABLE IF NOT EXISTS {GOALS_TABLE} (user_id text NOT NULL, "
    "profile_id text NOT NULL, goal text NOT NULL "
    f"CHECK (goal IN ({', '.join(repr(g) for g in get_args(Goal))})), "
    "changed_at timestamptz NOT NULL, PRIMARY KEY (user_id, profile_id))"
)
ROUNDS_TABLE = "vouch_review_rounds"
"""Each user's review rounds under each compliance profile (vouch.rounds, #402): at most one open
(`closed_at` null), each closed one with its frozen results. None kept is the first round, open
since the start (`opened_at` null), written only when it is closed. Each start and close is
written in its audit entry's transaction."""
CREATE_ROUNDS = (
    f"CREATE TABLE IF NOT EXISTS {ROUNDS_TABLE} (id bigserial PRIMARY KEY, "
    "user_id text NOT NULL, profile_id text NOT NULL, name text NOT NULL, "
    "opened_at timestamptz, closed_at timestamptz, results jsonb)"
)
CREATE_ROUNDS_ONE_OPEN = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {ROUNDS_TABLE}_one_open ON {ROUNDS_TABLE} "
    "(user_id, profile_id) WHERE closed_at IS NULL"
)
"""At most one open round per user and profile, whoever writes."""
QUESTIONS_TABLE = "vouch_open_questions"
"""Each open question the lead logged on a control (vouch.questions, #414), per user and profile:
what is asked, of whom, when it is due, and its answer once recorded (null until then; final).
`id` is the question's, which lead-supplied evidence cites. Each write in its audit entry's
transaction; a fresh start hides the questions asked before it but deletes none."""
QUESTION_COLUMNS = (
    "id",
    "user_id",
    "profile_id",
    "control_id",
    "question",
    "asked_of",
    "due_date",
    "asked_at",
    "answer",
    "answered_at",
)
CREATE_QUESTIONS = (
    f"CREATE TABLE IF NOT EXISTS {QUESTIONS_TABLE} (id bigserial PRIMARY KEY, "
    "user_id text NOT NULL, profile_id text NOT NULL, control_id text NOT NULL, "
    "question text NOT NULL, asked_of text NOT NULL, due_date date NOT NULL, "
    "asked_at timestamptz NOT NULL, answer text, answered_at timestamptz)"
)
CREATE_QUESTIONS_INDEX = (
    f"CREATE INDEX IF NOT EXISTS {QUESTIONS_TABLE}_by_owner ON {QUESTIONS_TABLE} "
    "(user_id, profile_id, asked_at, id)"
)
ADDED_DOCUMENTS_TABLE = "vouch_added_documents"
"""Each version of each evidence document a lead added to a compliance profile
(vouch.added_documents, #584): never deleted or rewritten, but for the withdrawn mark
(`withdrawn_at`, set once)."""
ADDED_DOCUMENT_COLUMNS = (
    "profile_id",
    "doc_id",
    "version",
    "title",
    "kind",
    "approval_date",
    "review_cycle",
    "text",
    "added_by",
    "added_by_name",
    "added_at",
    "withdrawn_at",
)
CREATE_ADDED_DOCUMENTS = (
    f"CREATE TABLE IF NOT EXISTS {ADDED_DOCUMENTS_TABLE} (profile_id text NOT NULL, "
    "doc_id text NOT NULL, version integer NOT NULL CHECK (version >= 1), title text NOT NULL, "
    "kind text NOT NULL CHECK (kind IN ('policy', 'record')), approval_date date NOT NULL, "
    "review_cycle text NOT NULL, text text NOT NULL, added_by text NOT NULL, "
    "added_by_name text NOT NULL, added_at timestamptz NOT NULL, withdrawn_at timestamptz, "
    "PRIMARY KEY (profile_id, doc_id, version))"
)
_KEPT_COLUMNS = tuple(c for c in ADDED_DOCUMENT_COLUMNS if c != "withdrawn_at")
CREATE_ADDED_DOCUMENTS_GUARD = (
    "CREATE OR REPLACE FUNCTION vouch_added_documents_kept() RETURNS trigger LANGUAGE plpgsql "
    "AS $$ BEGIN "
    "IF TG_OP = 'UPDATE' AND OLD.withdrawn_at IS NULL AND NEW.withdrawn_at IS NOT NULL "
    f"AND ({', '.join(f'NEW.{c}' for c in _KEPT_COLUMNS)}) IS NOT DISTINCT FROM "
    f"({', '.join(f'OLD.{c}' for c in _KEPT_COLUMNS)}) THEN RETURN NEW; END IF; "
    "RAISE EXCEPTION 'an added document is kept as added; only the withdrawn mark is set'; "
    "END $$"
)
CREATE_ADDED_DOCUMENTS_TRIGGERS = (
    (
        f"CREATE OR REPLACE TRIGGER {ADDED_DOCUMENTS_TABLE}_kept BEFORE UPDATE OR DELETE ON "
        f"{ADDED_DOCUMENTS_TABLE} FOR EACH ROW EXECUTE FUNCTION vouch_added_documents_kept()"
    ),
    (
        f"CREATE OR REPLACE TRIGGER {ADDED_DOCUMENTS_TABLE}_no_truncate BEFORE TRUNCATE ON "
        f"{ADDED_DOCUMENTS_TABLE} FOR EACH STATEMENT EXECUTE FUNCTION vouch_added_documents_kept()"
    ),
)
"""The table refuses a delete, a truncate, and any update but setting the withdrawn mark once,
whoever asks: an earlier version stays as it was cited."""
ADD_DOCUMENT_LOCK = "SELECT pg_advisory_xact_lock(%s)"
"""Two writes to one profile (adds, withdraws) take turns, so the limit and the next version are
read as written, and no add slips a live version in beside a withdraw."""
REVIEW_LOCK = "SELECT pg_try_advisory_xact_lock(%s) AS taken"
"""One review of a thread at a time, from any process: held until the transaction ends. Taken
without waiting, so a second review is refused at once and holds no connection meanwhile."""
DECIDE_LOCK = "SELECT pg_advisory_xact_lock(%s)"
"""A remediation decision takes the same lock (#372): it waits for a step of the review that
holds it (bounded by the statement timeout), so it reads the assessment as that step left it."""

SETUP_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
    "store",
    "store_migrations",
    SETTINGS_TABLE,
    COUNTS_TABLE,
    PROFILE_PASS_COUNTS_TABLE,
    USAGE_TABLE,
    AUDIT_TABLE,
    REMEDIATION_TABLE,
    COMPANY_PROFILES_TABLE,
    PLAN_TABLE,
    GOALS_TABLE,
    ROUNDS_TABLE,
    QUESTIONS_TABLE,
    ADDED_DOCUMENTS_TABLE,
)
"""Every table vouch-db-setup creates: LangGraph's (checkpointer and store, as the installed
langgraph-checkpoint-postgres creates them) and Vouch's own. Health checks they're all there."""
SETUP_COLUMNS = tuple((COMPANY_PROFILES_TABLE, column) for column in REMOVAL_COLUMNS)
"""The columns vouch-db-setup adds to a table made before them (#418): health checks them too, so
code deployed before the setup ran shows as needs-setup."""
SETUP_CHECK = (
    "SELECT (SELECT count(*) FROM unnest(%s::text[]) AS t(name) WHERE to_regclass(name) IS NULL)"
    " + (SELECT count(*) FROM unnest(%s::text[], %s::text[]) AS c(tbl, col) WHERE NOT EXISTS ("
    "SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass(c.tbl) AND attname = c.col"
    " AND NOT attisdropped)) AS missing"
)
"""How many of SETUP_TABLES, and of SETUP_COLUMNS, are missing, in one query on the catalogue
(no table is read)."""

Health = Literal["reachable", "needs-setup", "unavailable"]
"""The database as health reports it: answering with its tables, answering without them
(vouch-db-setup never ran), or not answering."""

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


class WaitBounded(Protocol):
    """A connection with a bound on its wait for any one answer: BoundedConnection, and the
    tests' stand-ins for it, typed against this (#129)."""

    wait_timeout: float


@contextmanager
def waiting_at_most(conn: WaitBounded, seconds: float) -> Iterator[None]:
    """`conn` waits at most `seconds` for any one answer inside the block, then as before."""
    before = conn.wait_timeout
    conn.wait_timeout = seconds
    try:
        yield
    finally:
        conn.wait_timeout = before


def check_within_bound(conn: BoundedConnection) -> None:
    """The pool's checkout check (`check_connection`), waiting CHECK_TIMEOUT_SECONDS at most."""
    with waiting_at_most(conn, CHECK_TIMEOUT_SECONDS):
        ConnectionPool.check_connection(conn)


class Connect(Protocol):
    """psycopg's `Connection.connect`: the seam a test swaps for a flaky fake."""

    def __call__(self, conninfo: str, **kwargs: Any) -> Connection: ...


def _setup_check_params() -> tuple[list[str], list[str], list[str]]:
    """SETUP_CHECK's parameters: SETUP_TABLES, then SETUP_COLUMNS as tables and column names."""
    return (
        list(SETUP_TABLES),
        [table for table, _ in SETUP_COLUMNS],
        [column for _, column in SETUP_COLUMNS],
    )


def _names_a_column(error: psycopg.errors.UndefinedColumn, columns: Collection[str]) -> bool:
    """Whether Postgres's "column ... does not exist" names one of `columns`."""
    message = error.diag.message_primary or str(error)
    return any(re.search(rf"\b{re.escape(column)}\b", message) for column in columns)


class DatabaseUnavailable(RuntimeError):
    """The database can't be used just now. The message never holds the URL or its password."""


class ReviewInProgress(RuntimeError):
    """Another review of the thread holds its lock (`Database.reviewing`)."""


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
        self._health: tuple[float, Health] | None = None

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
    def connection(self, timeout: float | None = None) -> Iterator[BoundedConnection]:
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

    def health(self) -> Health:
        """Whether a connection can be had and vouch-db-setup's tables are there (#123); one
        check, its answer reused for HEALTH_CACHE_SECONDS."""
        now = self._clock()
        if self._health is not None and now - self._health[0] < HEALTH_CACHE_SECONDS:
            return self._health[1]
        answer: Health
        try:
            with (
                self.connection(timeout=HEALTH_TIMEOUT_SECONDS) as conn,
                waiting_at_most(conn, HEALTH_TIMEOUT_SECONDS),
            ):
                row = conn.execute(SETUP_CHECK, _setup_check_params()).fetchone()
            answer = "reachable" if row and row["missing"] == 0 else "needs-setup"
        except DatabaseUnavailable:
            answer = "unavailable"
        if answer == "needs-setup":
            log.error(
                "vouch-db-setup's tables or columns are missing: run vouch-db-setup on this database"
            )
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
        return self._take(COUNTS_TABLE, user_id, day, limit_setting, default_limit)

    def take_profile_pass(
        self, user_id: str, day: date, limit_setting: str, default_limit: int
    ) -> tuple[bool, int]:
        """Count one profile pass for the user's day, as take_assessment counts an assessment,
        in the passes' own table (#371). (counted, the limit). Raises DatabaseUnavailable."""
        return self._take(PROFILE_PASS_COUNTS_TABLE, user_id, day, limit_setting, default_limit)

    def _take(
        self, table: str, user_id: str, day: date, limit_setting: str, default_limit: int
    ) -> tuple[bool, int]:
        """One more for the user's day in the counts `table`, if under the limit: the limit
        read and the count taken in one statement."""
        with self.connection() as conn, self._needs_setup(table):
            row = conn.execute(
                "WITH lim AS (SELECT coalesce((SELECT value::integer FROM "
                f"{SETTINGS_TABLE} WHERE name = %(setting)s), %(default)s) AS n), "
                f"taken AS (INSERT INTO {table} AS c (user_id, day, count) "
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

    def under_assessment_limit(
        self, user_id: str, day: date, limit_setting: str, default_limit: int
    ) -> tuple[bool, int]:
        """Whether the user started fewer assessments that day than the limit (as
        take_assessment reads it), counting nothing: (under it, the limit). Raises
        DatabaseUnavailable."""
        with self.connection() as conn, self._needs_setup(COUNTS_TABLE):
            row = conn.execute(
                "WITH lim AS (SELECT coalesce((SELECT value::integer FROM "
                f"{SETTINGS_TABLE} WHERE name = %(setting)s), %(default)s) AS n) "
                "SELECT (SELECT n FROM lim) AS lim, coalesce((SELECT count FROM "
                f"{COUNTS_TABLE} WHERE user_id = %(user_id)s AND day = %(day)s), 0) "
                "< (SELECT n FROM lim) AS under",
                {
                    "setting": limit_setting,
                    "default": default_limit,
                    "user_id": user_id,
                    "day": day,
                },
            ).fetchone()
        assert row is not None  # a SELECT of two values always gives one row
        return row["under"], row["lim"]

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

    def usage_totals(
        self, fake_model: str, not_assessments: tuple[str, ...]
    ) -> dict[str, dict[str, Any]]:
        """Every usage added up, by "openrouter" and "fake" (runs on `fake_model`); rows whose
        control is one of `not_assessments` (chat messages, profile passes) count in the tokens
        and cost but not as assessments. A kind with no runs is left out. Raises DatabaseUnavailable."""
        with self.connection() as conn, self._needs_setup(USAGE_TABLE):
            rows = conn.execute(
                "SELECT CASE WHEN model = %s THEN 'fake' ELSE 'openrouter' END AS kind, "
                "count(*) FILTER (WHERE control_id <> ALL(%s)) AS assessments, "
                "sum(input_tokens)::bigint AS input_tokens, "
                "sum(output_tokens)::bigint AS output_tokens, sum(reported_cost_usd) AS reported_cost_usd, "
                "sum(estimated_cost_usd) AS estimated_cost_usd, "
                "count(*) FILTER (WHERE NOT cost_complete) AS incomplete "
                f"FROM {USAGE_TABLE} GROUP BY kind",
                (fake_model, list(not_assessments)),
            ).fetchall()
        return {row.pop("kind"): row for row in rows}

    def recent_usage(self, limit: int, leave_out: tuple[str, ...]) -> list[dict[str, Any]]:
        """The last `limit` usage rows, newest first by `at`, leaving out those whose control is
        one of `leave_out` (chat messages): the developer page's recent runs (#496). Raises
        DatabaseUnavailable."""
        with self.connection() as conn, self._needs_setup(USAGE_TABLE):
            return conn.execute(
                "SELECT user_id, control_id, model, input_tokens, output_tokens, "
                "reported_cost_usd, estimated_cost_usd, estimated, cost_complete, at "
                f"FROM {USAGE_TABLE} WHERE control_id <> ALL(%s) ORDER BY at DESC, id DESC LIMIT %s",
                (list(leave_out), limit),
            ).fetchall()

    @contextmanager
    def _failed(self, table: str) -> Iterator[None]:
        """Any other database error writing to `table` (a value it refuses, say):
        DatabaseUnavailable, the reason in the log."""
        try:
            yield
        except psycopg.Error as error:
            log.error("a write to %s failed: %s", table, self._redacted(str(error)))
            raise DatabaseUnavailable(f"a write to {table} failed") from None

    @contextmanager
    def _needs_setup(self, table: str, columns: Collection[str] = ()) -> Iterator[None]:
        """A table vouch-db-setup makes: missing, or missing one of `columns` it adds to it,
        it's DatabaseUnavailable, and the log says so. Any other missing column is raised as
        itself: a bug, not a setup to run."""
        try:
            yield
        except psycopg.errors.UndefinedTable:
            log.error("no %s table: run vouch-db-setup on this database", table)
            raise DatabaseUnavailable(f"no {table} table: run vouch-db-setup") from None
        except psycopg.errors.UndefinedColumn as error:
            if not _names_a_column(error, columns):
                raise
            log.error("a column of %s is missing: run vouch-db-setup on this database", table)
            raise DatabaseUnavailable(
                f"a column of {table} is missing: run vouch-db-setup"
            ) from None

    @contextmanager
    def checkpointer(self, serde: SerializerProtocol | None = None) -> Iterator[PostgresSaver]:
        """LangGraph's Postgres checkpointer on the pool, writing and reading with `serde`
        (LangGraph's default when None; the API passes vouch.graph.state_serializer). Raises
        DatabaseUnavailable, also for a failure while it's in use or before vouch-db-setup ran."""
        pool = self._open_pool()
        with self._unavailable_on_drop(), self._needs_setup("checkpoints"):
            yield PostgresSaver(pool, serde=serde)

    @contextmanager
    def store(self) -> Iterator[PostgresStore]:
        """LangGraph's Postgres store on the pool: long-term memory (vouch.memory). Raises
        DatabaseUnavailable, also for a failure while it's in use or before vouch-db-setup ran."""
        pool = self._open_pool()
        with self._unavailable_on_drop(), self._needs_setup("store"):
            yield PostgresStore(pool)

    @contextmanager
    def reviewing(
        self, lock_key: str, serde: SerializerProtocol | None = None
    ) -> Iterator[tuple[PostgresSaver, Callable[[list[Mapping[str, Any]]], None]]]:
        """One transaction for a review (vouch.audit): a checkpointer on its connection, and a
        way to append audit entries (mappings of AUDIT_COLUMNS) on it. Both are committed when
        the block ends, or both rolled back when it raises: no decision without its entries.

        The pool's connections are in autocommit; the transaction is opened explicitly, and
        LangGraph's own transactions inside it become savepoints. It first takes the review
        lock on `lock_key` (vouch.graph's thread key): ReviewInProgress when another review
        holds it. Raises DatabaseUnavailable for any database failure, also before
        vouch-db-setup ran."""
        with (
            self.connection() as conn,
            self._failed(AUDIT_TABLE),
            self._needs_setup(f"checkpoints or {AUDIT_TABLE}"),
            conn.transaction(),
        ):
            row = conn.execute(REVIEW_LOCK, (lock_id(lock_key),)).fetchone()
            if not (row and row["taken"]):
                raise ReviewInProgress(lock_key)

            def append(entries: list[Mapping[str, Any]]) -> None:
                _insert_audit(conn, entries)

            yield PostgresSaver(conn, serde=serde), append

    @contextmanager
    def deciding(
        self, lock_key: str, serde: SerializerProtocol | None = None
    ) -> Iterator[tuple[PostgresSaver, "RemediationWrites"]]:
        """One transaction for a remediation decision (vouch.remediation): it first takes the
        review lock on `lock_key` (vouch.graph's thread key), waiting while a step of the
        review holds it, so no Reject or Accept lands between the assessment read and the
        decision. A checkpointer and the decision's reads and writes, all on its connection.
        Raises DatabaseUnavailable for any database failure, also before vouch-db-setup ran."""
        tables = f"checkpoints, {REMEDIATION_TABLE} or {AUDIT_TABLE}"
        with (
            self.connection() as conn,
            self._failed(tables),
            self._needs_setup(tables),
            conn.transaction(),
        ):
            conn.execute(DECIDE_LOCK, (lock_id(lock_key),))
            yield PostgresSaver(conn, serde=serde), RemediationWrites(conn)

    def remediation_items(
        self, user_id: str, profile_id: str, thread_ids: list[str]
    ) -> list[dict[str, Any]]:
        """The user's decided remediation items of those assessments' threads under the profile,
        in one query: mappings of REMEDIATION_COLUMNS. None before vouch-db-setup made the table
        (a deploy that adds it, before setup runs): nothing is decided yet. Raises
        DatabaseUnavailable."""
        return self._read_remediation(
            f"SELECT {', '.join(REMEDIATION_COLUMNS)} FROM {REMEDIATION_TABLE} "
            "WHERE user_id = %(user_id)s AND profile_id = %(profile_id)s "
            "AND thread_id = ANY(%(thread_ids)s)",
            {"user_id": user_id, "profile_id": profile_id, "thread_ids": thread_ids},
        )

    def _read_remediation(self, query: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Rows of REMEDIATION_TABLE; none when it isn't there yet (logged: run vouch-db-setup)."""
        with self.connection() as conn:
            try:
                return conn.execute(query, params).fetchall()
            except psycopg.errors.UndefinedTable:
                log.error("no %s table: run vouch-db-setup on this database", REMEDIATION_TABLE)
                return []

    def create_company_profile(
        self,
        ids: Iterator[str],
        profile: Mapping[str, Any],
        created_by: str,
        created_at: datetime,
    ) -> str:
        """Keep a company profile under the first of `ids` (endless) no row holds yet; that id.
        Each try is one insert that takes the id or finds it taken, so two at once get different
        ids. Raises DatabaseUnavailable, also before vouch-db-setup ran."""
        with (
            self.connection() as conn,
            self._failed(COMPANY_PROFILES_TABLE),
            self._needs_setup(COMPANY_PROFILES_TABLE),
        ):
            while True:
                profile_id = next(ids)
                row = conn.execute(
                    f"INSERT INTO {COMPANY_PROFILES_TABLE} ({', '.join(COMPANY_PROFILE_COLUMNS)}) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING RETURNING id",
                    (profile_id, Jsonb(dict(profile)), created_by, created_at),
                ).fetchone()
                if row is not None:
                    return str(row["id"])

    def company_profiles(self) -> list[dict[str, Any]]:
        """Every live company profile (not removed), oldest first: mappings of
        COMPANY_PROFILE_COLUMNS; none before vouch-db-setup made the table or its removal
        columns (logged). Raises DatabaseUnavailable."""
        return self._read_company_profiles(f"WHERE {LIVE_COMPANY} ORDER BY created_at, id", {})

    def company_profile(self, profile_id: str) -> dict[str, Any] | None:
        """The live company profile `profile_id`, as company_profiles gives it; None when there
        is none (or it was removed), also before vouch-db-setup made the table or its removal
        columns (logged). Raises DatabaseUnavailable."""
        rows = self._read_company_profiles(
            f"WHERE id = %(id)s AND {LIVE_COMPANY}", {"id": profile_id}
        )
        return rows[0] if rows else None

    def _read_company_profiles(self, rest: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        query = f"SELECT {', '.join(COMPANY_PROFILE_COLUMNS)} FROM {COMPANY_PROFILES_TABLE} {rest}"
        with self.connection() as conn:
            try:
                return conn.execute(query, params).fetchall()
            except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn) as error:
                if isinstance(error, psycopg.errors.UndefinedColumn) and not _names_a_column(
                    error, REMOVAL_COLUMNS
                ):
                    raise
                log.error(
                    "no %s table, or not its removal columns: run vouch-db-setup on this database",
                    COMPANY_PROFILES_TABLE,
                )
                return []

    def remove_company_profile(
        self, profile_id: str, removed_by: str, removed_at: datetime
    ) -> bool:
        """Mark the live company profile `profile_id` removed, by whom and when; the row is
        kept. One update, so two at once mark it once: True for the one that did, False when
        there is no live row of that id. Raises DatabaseUnavailable, also before vouch-db-setup
        made the table or its removal columns."""
        with (
            self.connection() as conn,
            self._failed(COMPANY_PROFILES_TABLE),
            self._needs_setup(COMPANY_PROFILES_TABLE, columns=REMOVAL_COLUMNS),
        ):
            row = conn.execute(
                f"UPDATE {COMPANY_PROFILES_TABLE} SET removed_at = %s, removed_by = %s "
                f"WHERE id = %s AND {LIVE_COMPANY} RETURNING id",
                (removed_at, removed_by, profile_id),
            ).fetchone()
        return row is not None

    @contextmanager
    def planning(
        self, lock_key: str, serde: SerializerProtocol | None = None
    ) -> Iterator[tuple[PostgresSaver, "RemediationWrites", "PlanWrites"]]:
        """One transaction for a mark in the improvement plan (vouch.plan, #410): it first takes
        the thread's lock on `lock_key`, as `deciding` does, so the assessment, its decisions
        and its marks are read as they stand and two marks at once take turns. A checkpointer,
        the decisions' reads and the plan's reads and writes, all on its connection. Raises
        DatabaseUnavailable for any database failure, also before vouch-db-setup ran."""
        tables = f"checkpoints, {REMEDIATION_TABLE}, {PLAN_TABLE} or {AUDIT_TABLE}"
        with (
            self.connection() as conn,
            self._failed(tables),
            self._needs_setup(tables),
            conn.transaction(),
        ):
            conn.execute(DECIDE_LOCK, (lock_id(lock_key),))
            yield PostgresSaver(conn, serde=serde), RemediationWrites(conn), PlanWrites(conn)

    def plan_marks(
        self, user_id: str, profile_id: str, thread_ids: list[str]
    ) -> list[dict[str, Any]]:
        """The user's plan marks on those assessments' threads under the profile (#410), in one
        query: mappings of PLAN_COLUMNS. None before vouch-db-setup made the table (logged: run
        vouch-db-setup): every item reads its default priority, none done. Raises
        DatabaseUnavailable."""
        with self.connection() as conn:
            try:
                return conn.execute(
                    f"SELECT {', '.join(PLAN_COLUMNS)} FROM {PLAN_TABLE} "
                    "WHERE user_id = %(user_id)s AND profile_id = %(profile_id)s "
                    "AND thread_id = ANY(%(thread_ids)s)",
                    {"user_id": user_id, "profile_id": profile_id, "thread_ids": thread_ids},
                ).fetchall()
            except psycopg.errors.UndefinedTable:
                log.error("no %s table: run vouch-db-setup on this database", PLAN_TABLE)
                return []

    def profile_goal(self, user_id: str, profile_id: str) -> Goal | None:
        """The user's Goal under the profile; None when none was ever changed, or before
        vouch-db-setup made the table (logged: run vouch-db-setup). Raises DatabaseUnavailable."""
        with self.connection() as conn:
            try:
                row = conn.execute(
                    f"SELECT goal FROM {GOALS_TABLE} WHERE user_id = %s AND profile_id = %s",
                    (user_id, profile_id),
                ).fetchone()
            except psycopg.errors.UndefinedTable:
                log.error("no %s table: run vouch-db-setup on this database", GOALS_TABLE)
                return None
        return row["goal"] if row else None

    def change_goal(
        self,
        user_id: str,
        profile_id: str,
        goal: Goal,
        default: Goal,
        changed_at: datetime,
        entry: Callable[[Goal], Mapping[str, Any]],
    ) -> bool:
        """Keep `goal` as the user's under the profile with its audit entry (`entry` of the goal
        before: a mapping of AUDIT_COLUMNS), in one transaction: both, or neither. The goal
        stored already (`default` when none is) writes nothing: False. Two changes at once take
        turns on the user's and profile's lock, so each real change writes one entry. Raises
        DatabaseUnavailable for any database failure, also before vouch-db-setup ran."""
        tables = f"{GOALS_TABLE} or {AUDIT_TABLE}"
        with (
            self.connection() as conn,
            self._failed(tables),
            self._needs_setup(tables),
            conn.transaction(),
        ):
            conn.execute(DECIDE_LOCK, (lock_id(json.dumps(["goal", user_id, profile_id])),))
            row = conn.execute(
                f"SELECT goal FROM {GOALS_TABLE} WHERE user_id = %s AND profile_id = %s",
                (user_id, profile_id),
            ).fetchone()
            before: Goal = row["goal"] if row else default
            if before == goal:
                return False
            conn.execute(
                f"INSERT INTO {GOALS_TABLE} (user_id, profile_id, goal, changed_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, profile_id) DO UPDATE "
                "SET goal = EXCLUDED.goal, changed_at = EXCLUDED.changed_at",
                (user_id, profile_id, goal, changed_at),
            )
            _insert_audit(conn, [entry(before)])
        return True

    def review_rounds(
        self, user_id: str, profile_id: str, closed_since: datetime | None
    ) -> tuple[bool, dict[str, Any] | None, dict[str, Any] | None]:
        """The user's review rounds under the profile (#402): whether any is kept, the open one,
        and the last one closed at or after `closed_since` (any, when None), each a mapping of
        `name`, `opened_at`, `closed_at` and `results`. Before vouch-db-setup made the table,
        none (logged: run vouch-db-setup). Raises DatabaseUnavailable."""
        columns = "name, opened_at, closed_at, results"
        mine = "user_id = %(user_id)s AND profile_id = %(profile_id)s"
        args = {"user_id": user_id, "profile_id": profile_id, "since": closed_since}
        with self.connection() as conn:
            try:
                kept = conn.execute(
                    f"SELECT EXISTS (SELECT 1 FROM {ROUNDS_TABLE} WHERE {mine}) AS kept", args
                ).fetchone()
                open_round = conn.execute(
                    f"SELECT {columns} FROM {ROUNDS_TABLE} WHERE {mine} AND closed_at IS NULL",
                    args,
                ).fetchone()
                closed = conn.execute(
                    f"SELECT {columns} FROM {ROUNDS_TABLE} WHERE {mine} "
                    "AND closed_at IS NOT NULL "
                    "AND (%(since)s::timestamptz IS NULL OR closed_at >= %(since)s) "
                    "ORDER BY closed_at DESC, id DESC LIMIT 1",
                    args,
                ).fetchone()
            except psycopg.errors.UndefinedTable:
                log.error("no %s table: run vouch-db-setup on this database", ROUNDS_TABLE)
                return False, None, None
        return bool(kept and kept["kept"]), open_round, closed

    def listed_rounds(self, user_id: str, profile_id: str) -> list[dict[str, Any]]:
        """Every review round the user keeps under the profile, in the order opened (#571), each
        a mapping of `name`, `opened_at`, `closed_at` and `results`. Before vouch-db-setup made
        the table, none (logged: run vouch-db-setup). Raises DatabaseUnavailable."""
        with self.connection() as conn:
            try:
                return conn.execute(
                    f"SELECT name, opened_at, closed_at, results FROM {ROUNDS_TABLE} "
                    "WHERE user_id = %s AND profile_id = %s ORDER BY id",
                    (user_id, profile_id),
                ).fetchall()
            except psycopg.errors.UndefinedTable:
                log.error("no %s table: run vouch-db-setup on this database", ROUNDS_TABLE)
                return []

    def start_round(
        self,
        user_id: str,
        profile_id: str,
        name: str,
        opened_at: datetime,
        entry: Mapping[str, Any],
    ) -> bool:
        """Open a round named `name` at `opened_at` with its audit entry (a mapping of
        AUDIT_COLUMNS), in one transaction: both, or neither. While a round is open (the first
        one too, open while none is kept) it writes nothing: False. Two at once take turns on
        the user's and profile's lock. Raises DatabaseUnavailable for any database failure, also
        before vouch-db-setup ran."""
        tables = f"{ROUNDS_TABLE} or {AUDIT_TABLE}"
        with (
            self.connection() as conn,
            self._failed(tables),
            self._needs_setup(tables),
            conn.transaction(),
        ):
            conn.execute(DECIDE_LOCK, (lock_id(json.dumps(["round", user_id, profile_id])),))
            row = conn.execute(
                "SELECT count(*) AS kept, count(*) FILTER (WHERE closed_at IS NULL) AS open "
                f"FROM {ROUNDS_TABLE} WHERE user_id = %s AND profile_id = %s",
                (user_id, profile_id),
            ).fetchone()
            if row is None or row["kept"] == 0 or row["open"] > 0:
                return False
            conn.execute(
                f"INSERT INTO {ROUNDS_TABLE} (user_id, profile_id, name, opened_at) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, profile_id, name, opened_at),
            )
            _insert_audit(conn, [entry])
        return True

    def close_round(
        self,
        user_id: str,
        profile_id: str,
        first_name: str,
        closed_at: datetime,
        results: Sequence[Mapping[str, Any]],
        entry: Callable[[str], Mapping[str, Any]],
    ) -> tuple[str, datetime | None] | None:
        """Close the open round at `closed_at` with its frozen `results` and its audit entry
        (`entry` of the round's name: a mapping of AUDIT_COLUMNS), in one transaction: both, or
        neither. With none kept, the first round (named `first_name`, opened at no time) is
        written closed. The closed round's name and opening time; None when no round is open,
        with nothing written. Two at once take turns on the user's and profile's lock, so one closes.
        Raises DatabaseUnavailable for any database failure, also before vouch-db-setup ran."""
        tables = f"{ROUNDS_TABLE} or {AUDIT_TABLE}"
        with (
            self.connection() as conn,
            self._failed(tables),
            self._needs_setup(tables),
            conn.transaction(),
        ):
            conn.execute(DECIDE_LOCK, (lock_id(json.dumps(["round", user_id, profile_id])),))
            mine = (user_id, profile_id)
            closed = conn.execute(
                f"UPDATE {ROUNDS_TABLE} SET closed_at = %s, results = %s "
                "WHERE user_id = %s AND profile_id = %s AND closed_at IS NULL "
                "RETURNING name, opened_at",
                (closed_at, Jsonb(results), *mine),
            ).fetchone()
            opened_at: datetime | None = None
            if closed is not None:
                name: str = closed["name"]
                opened_at = closed["opened_at"]
            else:
                kept = conn.execute(
                    f"SELECT 1 FROM {ROUNDS_TABLE} WHERE user_id = %s AND profile_id = %s",
                    mine,
                ).fetchone()
                if kept is not None:
                    return None
                name = first_name
                conn.execute(
                    f"INSERT INTO {ROUNDS_TABLE} (user_id, profile_id, name, closed_at, results) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (*mine, name, closed_at, Jsonb(results)),
                )
            _insert_audit(conn, [entry(name)])
        return name, opened_at

    def open_questions(self, user_id: str, profile_id: str) -> list[dict[str, Any]]:
        """The user's open questions under the profile (#414), oldest first: mappings of
        QUESTION_COLUMNS. None before vouch-db-setup made the table (logged: run vouch-db-setup).
        Raises DatabaseUnavailable."""
        with self.connection() as conn:
            try:
                return conn.execute(
                    f"SELECT {', '.join(QUESTION_COLUMNS)} FROM {QUESTIONS_TABLE} "
                    "WHERE user_id = %s AND profile_id = %s ORDER BY asked_at, id",
                    (user_id, profile_id),
                ).fetchall()
            except psycopg.errors.UndefinedTable:
                log.error("no %s table: run vouch-db-setup on this database", QUESTIONS_TABLE)
                return []

    def log_question(self, row: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
        """Keep an open question (a mapping of QUESTION_COLUMNS but `id`, `answer` and
        `answered_at`) with its audit entry (a mapping of AUDIT_COLUMNS), in one transaction:
        both, or neither. The question as kept, with its id. Raises DatabaseUnavailable for any
        database failure, also before vouch-db-setup ran."""
        columns = QUESTION_COLUMNS[1:-2]
        tables = f"{QUESTIONS_TABLE} or {AUDIT_TABLE}"
        with (
            self.connection() as conn,
            self._failed(tables),
            self._needs_setup(tables),
            conn.transaction(),
        ):
            kept = conn.execute(
                f"INSERT INTO {QUESTIONS_TABLE} ({', '.join(columns)}) "
                f"VALUES ({', '.join(f'%({c})s' for c in columns)}) "
                f"RETURNING {', '.join(QUESTION_COLUMNS)}",
                row,
            ).fetchone()
            _insert_audit(conn, [entry])
        if kept is None:  # an INSERT … RETURNING returns its row
            raise DatabaseUnavailable(f"a question in {QUESTIONS_TABLE} was not kept")
        return kept

    def answer_question(
        self,
        key: Mapping[str, Any],
        answer: str,
        answered_at: datetime,
        entry: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        """Record the answer to the question `key` names (`id`, `user_id`, `profile_id`,
        `control_id`) with its audit entry (a mapping of AUDIT_COLUMNS), in one transaction:
        both, or neither. The question's row is locked first, so two answers at once take
        turns. (None, False) when there is no such question; (the question as kept, False) when
        it was answered already, writing nothing; else (the question answered, True). Raises
        DatabaseUnavailable for any database failure, also before vouch-db-setup ran."""
        tables = f"{QUESTIONS_TABLE} or {AUDIT_TABLE}"
        mine = (
            "id = %(id)s AND user_id = %(user_id)s AND profile_id = %(profile_id)s "
            "AND control_id = %(control_id)s"
        )
        columns = ", ".join(QUESTION_COLUMNS)
        with (
            self.connection() as conn,
            self._failed(tables),
            self._needs_setup(tables),
            conn.transaction(),
        ):
            kept = conn.execute(
                f"SELECT {columns} FROM {QUESTIONS_TABLE} WHERE {mine} FOR UPDATE", key
            ).fetchone()
            if kept is None or kept["answer"] is not None:
                return kept, False
            answered = conn.execute(
                f"UPDATE {QUESTIONS_TABLE} SET answer = %(answer)s, answered_at = %(at)s "
                f"WHERE {mine} RETURNING {columns}",
                {**key, "answer": answer, "at": answered_at},
            ).fetchone()
            _insert_audit(conn, [entry])
        return answered, True

    def added_documents(self, profile_id: str | None) -> list[dict[str, Any]]:
        """Every version of the profile's added documents (every profile's, for None), by
        profile, id and version: mappings of ADDED_DOCUMENT_COLUMNS. An empty list before
        vouch-db-setup made the table (logged: run vouch-db-setup). Raises DatabaseUnavailable."""
        with self.connection() as conn:
            try:
                return conn.execute(
                    f"SELECT {', '.join(ADDED_DOCUMENT_COLUMNS)} FROM {ADDED_DOCUMENTS_TABLE} "
                    "WHERE %(p)s::text IS NULL OR profile_id = %(p)s "
                    "ORDER BY profile_id, doc_id, version",
                    {"p": profile_id},
                ).fetchall()
            except psycopg.errors.UndefinedTable:
                log.error("no %s table: run vouch-db-setup on this database", ADDED_DOCUMENTS_TABLE)
                return []

    def added_watermark(self, profile_id: str) -> tuple[int, int, datetime | None]:
        """How many versions of the profile's added documents are kept, how many are withdrawn,
        and the latest time one was added or withdrawn: (0, 0, None) for none, also before vouch-db-setup made the table.
        One aggregate on the table, which an instance asks before using what it holds. Raises
        DatabaseUnavailable."""
        with self.connection() as conn:
            try:
                row = conn.execute(
                    "SELECT count(*) AS n, count(withdrawn_at) AS w, "
                    "max(greatest(added_at, withdrawn_at)) AS at "
                    f"FROM {ADDED_DOCUMENTS_TABLE} WHERE profile_id = %s",
                    (profile_id,),
                ).fetchone()
            except psycopg.errors.UndefinedTable:
                return 0, 0, None
        return (0, 0, None) if row is None else (int(row["n"]), int(row["w"]), row["at"])

    def add_document(
        self,
        profile_id: str,
        made: Callable[[list[dict[str, Any]]], Mapping[str, Any]],
        entry: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Keep the version `made` gives from the profile's versions already kept (a mapping of
        ADDED_DOCUMENT_COLUMNS), with `entry`'s audit entry of the version kept when given (a
        mapping of AUDIT_COLUMNS, #585), in one transaction holding the profile's lock, so two
        writes take turns: the version kept, and every version of the profile as it then
        stands. An exception from `made` writes nothing and is raised as itself. Raises
        DatabaseUnavailable for any database failure, also before vouch-db-setup ran."""
        columns = ", ".join(ADDED_DOCUMENT_COLUMNS)
        with (
            self.connection() as conn,
            self._failed(ADDED_DOCUMENTS_TABLE),
            self._needs_setup(ADDED_DOCUMENTS_TABLE),
            conn.transaction(),
        ):
            conn.execute(ADD_DOCUMENT_LOCK, (lock_id(f"added-documents:{profile_id}"),))
            kept = conn.execute(
                f"SELECT {columns} FROM {ADDED_DOCUMENTS_TABLE} WHERE profile_id = %s "
                "ORDER BY doc_id, version",
                (profile_id,),
            ).fetchall()
            row = made(kept)
            added = conn.execute(
                f"INSERT INTO {ADDED_DOCUMENTS_TABLE} ({columns}) "
                f"VALUES ({', '.join(f'%({c})s' for c in ADDED_DOCUMENT_COLUMNS)}) "
                f"RETURNING {columns}",
                row,
            ).fetchone()
            if added is None:  # an INSERT … RETURNING returns its row
                raise DatabaseUnavailable(f"a document in {ADDED_DOCUMENTS_TABLE} was not kept")
            if entry is not None:
                _insert_audit(conn, [entry(added)])
        return added, [*kept, added]

    def withdraw_document(
        self,
        profile_id: str,
        doc_id: str,
        at: datetime,
        entry: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Mark every version of the document not yet withdrawn withdrawn at `at`, holding the
        profile's lock as an add does (so an add can't slip a live version in beside it), with
        `entry`'s audit entry of its newest version when this marked any (#586), in the same
        transaction: every version of the profile as it then stands, by id and version. Raises
        DatabaseUnavailable for any database failure, also before vouch-db-setup ran."""
        columns = ", ".join(ADDED_DOCUMENT_COLUMNS)
        with (
            self.connection() as conn,
            self._failed(ADDED_DOCUMENTS_TABLE),
            self._needs_setup(ADDED_DOCUMENTS_TABLE),
            conn.transaction(),
        ):
            conn.execute(ADD_DOCUMENT_LOCK, (lock_id(f"added-documents:{profile_id}"),))
            marked = conn.execute(
                f"UPDATE {ADDED_DOCUMENTS_TABLE} SET withdrawn_at = %s WHERE profile_id = %s "
                "AND doc_id = %s AND withdrawn_at IS NULL",
                (at, profile_id, doc_id),
            ).rowcount
            rows = conn.execute(
                f"SELECT {columns} FROM {ADDED_DOCUMENTS_TABLE} WHERE profile_id = %s "
                "ORDER BY doc_id, version",
                (profile_id,),
            ).fetchall()
            if marked and entry is not None:
                newest = [r for r in rows if r["doc_id"] == doc_id][-1]
                _insert_audit(conn, [entry(newest)])
            return rows

    def audit_entries(
        self, user_id: str, profile_id: str, *, limit: int, before: int | None = None
    ) -> list[dict[str, Any]]:
        """At most `limit` of the user's audit entries under the profile, newest first (by time,
        then id), after the entry `before` names when given: mappings of `id` and AUDIT_COLUMNS.
        Raises DatabaseUnavailable."""
        with self.connection() as conn, self._needs_setup(AUDIT_TABLE):
            return conn.execute(
                f"SELECT id, {', '.join(AUDIT_COLUMNS)} FROM {AUDIT_TABLE} "
                "WHERE user_id = %(user_id)s AND profile_id = %(profile_id)s "
                "AND (%(before)s::bigint IS NULL OR (at, id) < "
                f"(SELECT at, id FROM {AUDIT_TABLE} WHERE id = %(before)s)) "
                "ORDER BY at DESC, id DESC LIMIT %(limit)s",
                {"user_id": user_id, "profile_id": profile_id, "before": before, "limit": limit},
            ).fetchall()

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
                conn.execute(CREATE_PROFILE_PASS_COUNTS)
                conn.execute(CREATE_USAGE)
                conn.execute(CREATE_AUDIT)
                conn.execute(CREATE_AUDIT_INDEX)
                conn.execute(CREATE_AUDIT_GUARD)
                for trigger in CREATE_AUDIT_TRIGGERS:
                    conn.execute(trigger)
                conn.execute(ADD_AUDIT_REVIEWER_NAME)
                conn.execute(ADD_AUDIT_GOAL_COLUMNS)
                conn.execute(ADD_AUDIT_ROUND_NAME)
                conn.execute(REPLACE_AUDIT_ACTION_CHECK)
                conn.execute(CREATE_REMEDIATION)
                conn.execute(CREATE_REMEDIATION_GUARD)
                for trigger in CREATE_REMEDIATION_TRIGGERS:
                    conn.execute(trigger)
                conn.execute(CREATE_COMPANY_PROFILES)
                conn.execute(ADD_COMPANY_PROFILE_REMOVAL)
                conn.execute(CREATE_PLAN)
                conn.execute(CREATE_GOALS)
                conn.execute(CREATE_ROUNDS)
                conn.execute(CREATE_ROUNDS_ONE_OPEN)
                conn.execute(CREATE_QUESTIONS)
                conn.execute(CREATE_QUESTIONS_INDEX)
                conn.execute(CREATE_ADDED_DOCUMENTS)
                conn.execute(CREATE_ADDED_DOCUMENTS_GUARD)
                for trigger in CREATE_ADDED_DOCUMENTS_TRIGGERS:
                    conn.execute(trigger)
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


class RemediationWrites:
    """A remediation decision's reads and writes, on the connection of its transaction
    (`Database.deciding`)."""

    COLUMNS = ", ".join(REMEDIATION_COLUMNS)

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def kept(self, key: Mapping[str, Any]) -> dict[str, Any] | None:
        """The item decided under `key` (user_id, profile_id, thread_id, requirement_id), if
        any: a mapping of REMEDIATION_COLUMNS."""
        return self.conn.execute(
            f"SELECT {self.COLUMNS} FROM {REMEDIATION_TABLE} WHERE user_id = %(user_id)s "
            "AND profile_id = %(profile_id)s AND thread_id = %(thread_id)s "
            "AND requirement_id = %(requirement_id)s",
            key,
        ).fetchone()

    def decide(
        self, item: Mapping[str, Any], entries: list[Mapping[str, Any]]
    ) -> tuple[dict[str, Any], bool]:
        """Keep the lead's decision on a remediation item (a mapping of REMEDIATION_COLUMNS)
        and its audit entries: when the item was decided already, nothing is written: (the
        kept item, False); else (the item, True). Two at once take turns on the item's key, so
        one decides."""
        values = ", ".join(f"%({c})s" for c in REMEDIATION_COLUMNS)
        kept = self.conn.execute(
            f"INSERT INTO {REMEDIATION_TABLE} ({self.COLUMNS}) VALUES ({values}) "
            f"ON CONFLICT DO NOTHING RETURNING {self.COLUMNS}",
            item,
        ).fetchone()
        if kept is not None:
            _insert_audit(self.conn, entries)
            return kept, True
        existing = self.kept(item)
        if existing is None:  # the insert met it, and the guard lets nothing delete one
            raise DatabaseUnavailable(f"a decision in {REMEDIATION_TABLE} vanished")
        return existing, False


class PlanWrites:
    """A plan mark's reads and writes, on the connection of its transaction
    (`Database.planning`)."""

    COLUMNS = ", ".join(PLAN_COLUMNS)

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def kept(self, key: Mapping[str, Any]) -> dict[str, Any] | None:
        """The marks kept under `key` (user_id, profile_id, thread_id, requirement_id), if any:
        a mapping of PLAN_COLUMNS."""
        return self.conn.execute(
            f"SELECT {self.COLUMNS} FROM {PLAN_TABLE} WHERE user_id = %(user_id)s "
            "AND profile_id = %(profile_id)s AND thread_id = %(thread_id)s "
            "AND requirement_id = %(requirement_id)s",
            key,
        ).fetchone()

    def mark(self, row: Mapping[str, Any], entries: list[Mapping[str, Any]]) -> None:
        """Keep the item's marks (a mapping of PLAN_COLUMNS), replacing those kept, with their
        audit entries (mappings of AUDIT_COLUMNS), in the transaction: both, or neither."""
        values = ", ".join(f"%({c})s" for c in PLAN_COLUMNS)
        self.conn.execute(
            f"INSERT INTO {PLAN_TABLE} ({self.COLUMNS}) VALUES ({values}) "
            "ON CONFLICT (user_id, profile_id, thread_id, requirement_id) DO UPDATE SET "
            "priority = EXCLUDED.priority, done_at = EXCLUDED.done_at, "
            "done_note = EXCLUDED.done_note",
            row,
        )
        _insert_audit(self.conn, entries)


def _insert_audit(conn: Connection, entries: list[Mapping[str, Any]]) -> None:
    """Append audit entries (mappings of AUDIT_COLUMNS) on `conn`, in its transaction."""
    columns = ", ".join(AUDIT_COLUMNS)
    values = ", ".join(f"%({c})s" for c in AUDIT_COLUMNS)
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {AUDIT_TABLE} ({columns}) VALUES ({values})",
            [{**entry, "evidence": Jsonb(entry["evidence"])} for entry in entries],
        )


def lock_id(key: str) -> int:
    """`key` as an advisory lock's bigint: the first 8 bytes of its BLAKE2b hash, signed. Two
    keys sharing one only take turns needlessly."""
    return int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), signed=True)


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
        found.add(str(conninfo.conninfo_to_dict(url).get("password") or ""))
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
    url = load_database_url(env)
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
