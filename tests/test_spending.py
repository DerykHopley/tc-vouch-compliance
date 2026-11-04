"""The spending guard (#35): each user's assessments capped per day, each one's tokens and cost.

Through the HTTP API in local mode (its one user), on the scripted fake model or on OpenRouter
faked (test_model_choice's fakes): no network, no paid call. The clock is fixed: the guard's
`clock` seam.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from pydantic import SecretStr
from test_database import empty_database, needs_postgres, tables  # noqa: F401 (a fixture)
from test_model_choice import OTHER_MODEL, FakeCatalogue, FakeChat, app_with_key

from vouch import database, spending
from vouch.api import create_app
from vouch.database import Database
from vouch.fake_model import ScriptedChatModel
from vouch.model_choice import DEFAULT_MODEL
from vouch.settings import DEMO_SCRIPT, Settings
from vouch.spending import DAILY_LIMIT, PRICES, DatabaseSpendingStore, MemorySpendingStore

pytestmark = pytest.mark.filterwarnings("ignore:A test tried to use socket.socket")

CONTROL = "NK-ENC-01"
UNREACHABLE_DB = "postgresql://vouch:pw@/vouch?host=/nonexistent-35"
NOV_4_AFTERNOON = datetime(2026, 11, 4, 15, 30, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def chat() -> FakeChat:
    return FakeChat()


@pytest.fixture
def catalogue() -> FakeCatalogue:
    return FakeCatalogue([DEFAULT_MODEL, OTHER_MODEL, "google/gemini-3-flash"])


def app_at(now: datetime, settings: Settings | None = None):
    app = create_app(settings or Settings())
    clock = FixedClock(now)
    app.state.spending.clock = clock
    return app, clock


def assess(client: TestClient, **kwargs: Any):
    return client.post("/api/assessments", json={"control_id": CONTROL}, **kwargs)


def result_of(response) -> dict[str, Any]:
    for block in response.text.split("\n\n"):
        if block.startswith("event: result"):
            return json.loads(block.split("data: ", 1)[1])
    raise AssertionError(f"no result in {response.text!r}")


# --- The daily limit ---------------------------------------------------------------------------


def test_the_31st_assessment_of_a_day_is_refused_with_the_reset_time_and_allowed_next_day():
    app, clock = app_at(NOV_4_AFTERNOON)
    client = TestClient(app, raise_server_exceptions=False)

    allowed = [assess(client).status_code for _ in range(DAILY_LIMIT)]
    refused = assess(client)
    clock.now = datetime(2026, 11, 5, 0, 0, 1, tzinfo=UTC)
    next_day = assess(client)

    assert DAILY_LIMIT == 30
    assert allowed == [200] * 30
    assert refused.status_code == 429
    assert not refused.headers["content-type"].startswith("text/event-stream")
    assert refused.json() == {
        "detail": "You have reached today's limit of 30 assessments. "
        "It resets at 00:00 UTC on 5 November 2026.",
        "limit": "daily",
        "resets_at": "2026-11-05T00:00:00+00:00",
    }
    assert refused.headers["retry-after"] == str(8 * 3600 + 30 * 60)
    assert next_day.status_code == 200
    assert "event: result" in next_day.text


def test_the_limit_is_each_users_own():
    store = MemorySpendingStore()

    assert all(store.take("alice", NOV_4_AFTERNOON.date(), 2) for _ in range(2))
    assert not store.take("alice", NOV_4_AFTERNOON.date(), 2)
    assert store.take("bob", NOV_4_AFTERNOON.date(), 2)


def test_an_unknown_control_is_not_counted():
    app, _ = app_at(NOV_4_AFTERNOON)
    app.state.spending.store.set_daily_limit(1)
    client = TestClient(app)

    assert client.post("/api/assessments", json={"control_id": "NK-NOPE"}).status_code == 404
    assert assess(client).status_code == 200


def test_a_developer_changes_the_daily_limit_and_it_applies_at_once():
    app, _ = app_at(NOV_4_AFTERNOON)
    client = TestClient(app, raise_server_exceptions=False)

    changed = client.put("/api/developer/daily-limit", json={"daily_limit": 1})
    first, second = assess(client), assess(client)

    assert changed.status_code == 200
    assert changed.json()["daily_limit"] == 1
    assert (first.status_code, second.status_code) == (200, 429)
    assert "limit of 1 assessment." in second.json()["detail"]


@pytest.mark.parametrize("limit", [0, -1, 100_001, "thirty", None])
def test_a_daily_limit_outside_its_range_is_refused(limit):
    client = TestClient(create_app(Settings()))

    response = client.put("/api/developer/daily-limit", json={"daily_limit": limit})

    assert response.status_code == 422
    assert client.get("/api/developer/settings").json()["daily_limit"] == DAILY_LIMIT


def test_with_the_counter_store_down_an_assessment_is_refused_before_it_starts():
    settings = Settings(database_url=SecretStr(UNREACHABLE_DB))
    app = create_app(settings)
    app.state.database = Database(settings.database_url, timeout=0.3)
    app.state.spending.store = DatabaseSpendingStore(app.state.database)
    client = TestClient(app, raise_server_exceptions=False)

    response = assess(client)
    app.state.database.close()

    assert app.state.models.runs_on == "fake"  # no model choice to read: the counter refuses
    assert response.status_code == 503
    assert "event:" not in response.text
    assert response.json() == {
        "detail": "The database is unavailable just now. Please try again shortly.",
        "unavailable": "database",
    }


def test_with_a_database_url_counts_and_usage_are_kept_in_the_database():
    app = create_app(Settings(database_url=SecretStr(UNREACHABLE_DB)))

    assert isinstance(app.state.spending.store, DatabaseSpendingStore)
    assert isinstance(create_app(Settings()).state.spending.store, MemorySpendingStore)


# --- Usage: tokens and cost --------------------------------------------------------------------


def test_the_fake_model_reports_token_counts_for_each_turn():
    model = ScriptedChatModel.from_file(DEMO_SCRIPT)

    reply = model.invoke([HumanMessage("x" * 400)])

    assert reply.usage_metadata["input_tokens"] == 100  # about four characters a token
    assert reply.usage_metadata["output_tokens"] > 0
    assert reply.usage_metadata["total_tokens"] == (
        reply.usage_metadata["input_tokens"] + reply.usage_metadata["output_tokens"]
    )


def test_a_fake_model_assessment_records_its_tokens_and_a_computed_cost_marked_estimated():
    app, _ = app_at(NOV_4_AFTERNOON)

    usage = result_of(assess(TestClient(app)))["usage"]

    price = PRICES[DEFAULT_MODEL]
    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0
    expected = (
        usage["input_tokens"] * price.input_per_million
        + usage["output_tokens"] * price.output_per_million
    ) / 1_000_000
    assert usage["cost_usd"] == pytest.approx(expected)
    assert usage["estimated"] is True
    assert usage["model"] == "fake"
    (kept,) = app.state.spending.store.records()
    assert (kept.user_id, kept.control_id) == ("local-user", CONTROL)
    assert kept.usage.to_dict() == usage


class CostingChat(FakeChat):
    """OpenRouter reporting each call's cost in its usage, as its usage accounting does."""

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        response = super().__call__(request)
        body = response.json()
        body["usage"]["cost"] = 0.00025
        return httpx2.Response(200, json=body)


def test_on_openrouter_the_reported_cost_is_recorded_and_not_estimated(catalogue):
    costing = CostingChat()
    app = app_with_key(costing, catalogue)

    usage = result_of(assess(TestClient(app)))["usage"]

    calls = len(costing.requests)
    assert calls >= 2  # gather and the judge, each measured
    assert usage == {
        "model": DEFAULT_MODEL,
        "input_tokens": 12 * calls,
        "output_tokens": 5 * calls,
        "cost_usd": pytest.approx(0.00025 * calls),
        "estimated": False,
    }


def test_on_openrouter_without_a_reported_cost_it_is_computed_and_estimated(chat, catalogue):
    usage = result_of(assess(TestClient(app_with_key(chat, catalogue))))["usage"]

    price = PRICES[DEFAULT_MODEL]
    calls = len(chat.requests)
    expected = (12 * calls * price.input_per_million + 5 * calls * price.output_per_million) / 1e6
    assert usage["cost_usd"] == pytest.approx(expected)
    assert usage["estimated"] is True


def test_a_model_missing_from_the_price_table_has_no_cost_and_is_estimated(chat, catalogue):
    app = app_with_key(chat, catalogue)
    app.state.models.store.set("google/gemini-3-flash")

    usage = result_of(assess(TestClient(app)))["usage"]

    assert usage["model"] == "google/gemini-3-flash"
    assert usage["cost_usd"] is None
    assert usage["estimated"] is True


def test_a_failed_assessment_still_records_the_tokens_it_spent(chat, catalogue):
    chat.text_only = True  # never answers: stops at the call budget, after paid calls
    app = app_with_key(chat, catalogue)

    response = assess(TestClient(app, raise_server_exceptions=False))

    assert "event: error" in response.text
    (kept,) = app.state.spending.store.records()
    assert kept.usage.input_tokens == 12 * len(chat.requests)


# --- The developer page totals ------------------------------------------------------------------


def test_the_developer_page_totals_tokens_and_cost():
    app, _ = app_at(NOV_4_AFTERNOON)
    client = TestClient(app)
    usages = [result_of(assess(client))["usage"] for _ in range(3)]

    page = client.get("/api/developer/settings").json()

    assert page["daily_limit"] == DAILY_LIMIT
    assert page["usage"] == {
        "assessments": 3,
        "input_tokens": sum(u["input_tokens"] for u in usages),
        "output_tokens": sum(u["output_tokens"] for u in usages),
        "cost_usd": pytest.approx(sum(u["cost_usd"] for u in usages)),
        "estimated": True,
        "unpriced": 0,
    }


def test_the_totals_count_an_unpriced_assessment_apart():
    store = MemorySpendingStore()
    priced = spending.Usage("fake", 10, 2, 0.5, estimated=False)
    unpriced = spending.Usage("x/y", 4, 1, None, estimated=True)
    store.record("alice", CONTROL, priced, NOV_4_AFTERNOON)
    store.record("bob", CONTROL, unpriced, NOV_4_AFTERNOON + timedelta(minutes=1))

    assert store.totals() == {
        "assessments": 2,
        "input_tokens": 14,
        "output_tokens": 3,
        "cost_usd": 0.5,
        "estimated": True,
        "unpriced": 1,
    }


# --- In the database: a real Postgres server, as in tests/test_database.py ----------------------
# `empty_database` is test_database's fixture, so its parameter shadows the import (F811).


@needs_postgres
def test_the_setup_creates_the_counts_and_usage_tables(empty_database):  # noqa: F811
    empty_database.setup()

    assert {database.COUNTS_TABLE, database.USAGE_TABLE} <= tables(empty_database)


@needs_postgres
def test_in_the_database_the_limit_counts_each_users_day(empty_database):  # noqa: F811
    empty_database.setup()
    store = DatabaseSpendingStore(empty_database)
    today, tomorrow = NOV_4_AFTERNOON.date(), NOV_4_AFTERNOON.date() + timedelta(days=1)

    taken = [store.take("alice", today, 2) for _ in range(3)]

    assert taken == [True, True, False]
    assert store.take("bob", today, 2)
    assert store.take("alice", tomorrow, 2)


@needs_postgres
def test_in_the_database_the_daily_limit_and_usage_outlast_a_restart(empty_database):  # noqa: F811
    empty_database.setup()

    def app_on_the_database():
        app = create_app(Settings(database_url=empty_database.url))
        app.state.spending.clock = FixedClock(NOV_4_AFTERNOON)
        return app

    with TestClient(app_on_the_database()) as first:
        assert first.put("/api/developer/daily-limit", json={"daily_limit": 2}).status_code == 200
        usages = [result_of(assess(first))["usage"] for _ in range(2)]
    with TestClient(app_on_the_database()) as second:
        refused = assess(second)
        page = second.get("/api/developer/settings").json()

    assert refused.status_code == 429
    assert page["daily_limit"] == 2
    assert page["usage"] == {
        "assessments": 2,
        "input_tokens": sum(u["input_tokens"] for u in usages),
        "output_tokens": sum(u["output_tokens"] for u in usages),
        "cost_usd": pytest.approx(sum(u["cost_usd"] for u in usages)),
        "estimated": True,
        "unpriced": 0,
    }


@needs_postgres
def test_without_the_setup_an_assessment_is_refused_and_the_log_names_it(empty_database, caplog):  # noqa: F811
    client = TestClient(create_app(Settings(database_url=empty_database.url)))

    response = assess(client)
    client.app.state.database.close()

    assert response.status_code == 503
    assert "vouch-db-setup" in caplog.text
