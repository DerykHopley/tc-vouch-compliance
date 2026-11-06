"""The spending guard (#35): each user's assessments capped per day, each one's tokens and cost.

Through the HTTP API in local mode (its one user), on the scripted fake model or on OpenRouter
faked (test_model_choice's fakes): no network, no paid call. The clock is fixed: the guard's
`clock` seam.
"""

import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from pydantic import SecretStr
from test_database import empty_database, needs_postgres, tables  # noqa: F401 (a fixture)
from test_model_choice import (
    OTHER_MODEL,
    FakeCatalogue,
    FakeChat,
    app_with_key,
)

from vouch import database
from vouch.api import create_app, record_off_the_event_loop
from vouch.database import Database
from vouch.fake_model import ScriptedChatModel
from vouch.graph import Judgement
from vouch.model_choice import DEFAULT_MODEL, CallBudgetSpent, OpenRouter
from vouch.settings import DEMO_SCRIPT, Settings
from vouch.spending import (
    CHAT,
    DAILY_LIMIT,
    PRICES,
    SONNET,
    DailyLimitReached,
    DatabaseSpendingStore,
    MemorySpendingStore,
    NoSharedCounter,
    SpendingGuard,
    Usage,
    UsageMeter,
    UsageRecord,
    UsageTotals,
)

pytestmark = pytest.mark.filterwarnings("ignore:A test tried to use socket.socket")

CONTROL = "4.1.2"
UNREACHABLE_DB = "postgresql://vouch:pw@/vouch?host=/nonexistent-35"
NOV_4_AFTERNOON = datetime(2026, 11, 4, 15, 30, tzinfo=UTC)
UNPRICED_MODEL = "google/gemini-3-flash"


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
    return FakeCatalogue([DEFAULT_MODEL, OTHER_MODEL, UNPRICED_MODEL])


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


def priced(input_tokens: int, output_tokens: int) -> float:
    return PRICES[DEFAULT_MODEL].cost(input_tokens, output_tokens)


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
    store.set_daily_limit(2)
    day = NOV_4_AFTERNOON.date()

    assert [store.take("alice", day) for _ in range(3)] == [(True, 2), (True, 2), (False, 2)]
    assert store.take("bob", day) == (True, 2)
    assert store.take("alice", day + timedelta(days=1)) == (True, 2)


@pytest.mark.parametrize(
    ("body", "status"),
    [({"control_id": ""}, 422), ({}, 422), ({"control_id": "NK-NOPE"}, 404)],
)
def test_a_request_refused_before_the_assessment_takes_no_count(body, status):
    app, _ = app_at(NOV_4_AFTERNOON)
    app.state.spending.set_daily_limit(1)
    client = TestClient(app)

    refused = client.post("/api/assessments", json=body)

    assert refused.status_code == status
    assert assess(client).status_code == 200  # the day's one assessment is still there


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


def test_on_openrouter_without_a_database_an_assessment_is_refused_before_any_call(chat, catalogue):
    """A count in memory resets whenever Cloud Run scales to zero: no limit to hold a paid model."""
    app = app_with_key(chat, catalogue)
    app.state.spending.store = MemorySpendingStore()

    response = assess(TestClient(app, raise_server_exceptions=False))

    assert response.status_code == 503
    assert "VOUCH_DATABASE_URL" in response.json()["detail"]
    assert chat.requests == []


def test_on_the_fake_model_the_memory_store_serves():
    app = create_app(Settings())

    assert isinstance(app.state.spending.store, MemorySpendingStore)
    assert assess(TestClient(app)).status_code == 200


def test_with_a_database_url_counts_and_usage_are_kept_in_the_database():
    app = create_app(Settings(database_url=SecretStr(UNREACHABLE_DB)))

    assert isinstance(app.state.spending.store, DatabaseSpendingStore)


# --- Usage: tokens and cost --------------------------------------------------------------------


class CallsSeen(BaseCallbackHandler):
    def __init__(self) -> None:
        self.ended: list[LLMResult] = []

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        self.ended.append(response)


def test_the_fake_model_reports_token_counts_for_each_turn():
    model = ScriptedChatModel.from_file(DEMO_SCRIPT)

    reply = model.invoke([HumanMessage("x" * 400)])

    assert reply.usage_metadata["input_tokens"] == 100  # about four characters a token
    assert reply.usage_metadata["output_tokens"] > 0


def test_the_fake_models_judge_is_a_metered_model_call():
    seen = CallsSeen()
    model = ScriptedChatModel.from_file(DEMO_SCRIPT)
    model.callbacks = [seen]

    judged = model.with_structured_output(Judgement).invoke([HumanMessage("x" * 40)])

    assert judged == Judgement.model_validate(model.script.judge.model_dump())
    (call,) = seen.ended
    assert call.generations[0][0].message.usage_metadata["input_tokens"] == 10


def test_a_fake_model_assessment_records_every_calls_tokens_and_a_computed_cost_marked_estimated():
    app, _ = app_at(NOV_4_AFTERNOON)

    usage = result_of(assess(TestClient(app)))["usage"]

    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0
    assert usage["model"] == "fake"
    assert usage["cost_usd"] == pytest.approx(priced(usage["input_tokens"], usage["output_tokens"]))
    assert usage["estimated"] is True
    assert usage["cost_complete"] is True
    (kept,) = app.state.spending.store.records()
    assert (kept.user_id, kept.control_id) == ("local-user", CONTROL)
    assert kept.usage.to_dict() == usage


def test_a_fake_run_meters_its_gather_turns_and_its_judge():
    app, _ = app_at(NOV_4_AFTERNOON)
    script = ScriptedChatModel.from_file(DEMO_SCRIPT).script
    seen = CallsSeen()
    real_for_assessment = app.state.models.for_assessment
    app.state.models.for_assessment = lambda chosen, callbacks: real_for_assessment(
        chosen, [*callbacks, seen]
    )

    assess(TestClient(app))

    assert len(seen.ended) == len(script.gather) + 1


def test_a_fake_run_meters_its_remediation_drafts_too(tmp_path):
    """Nothing searched, so every requirement is not evidenced and the drafts are asked for."""
    script_path = tmp_path / "nothing.json"
    script_path.write_text('{"gather": [{"answer": "Nothing found."}]}')
    app, _ = app_at(NOV_4_AFTERNOON, Settings(fake_script=script_path))
    seen = CallsSeen()
    real_for_assessment = app.state.models.for_assessment
    app.state.models.for_assessment = lambda chosen, callbacks: real_for_assessment(
        chosen, [*callbacks, seen]
    )

    result = result_of(assess(TestClient(app)))

    assert result["assessment"]["remediation"]  # the drafts were asked for
    assert len(seen.ended) == 3  # the answer, the judge, the remediation drafts
    assert result["usage"]["input_tokens"] == sum(
        r.generations[0][0].message.usage_metadata["input_tokens"] for r in seen.ended
    )


class CostingChat(FakeChat):
    """OpenRouter reporting each call's cost in its usage, as its usage accounting does; the
    `unreported` calls (by number, from 1) come without it, the `bare` ones without any usage."""

    def __init__(self, unreported: set[int] = frozenset(), bare: set[int] = frozenset()) -> None:
        super().__init__()
        self.unreported = unreported
        self.bare = bare

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        response = super().__call__(request)
        body = response.json()
        n = len(self.requests)
        if n in self.bare:
            del body["usage"]
        elif n not in self.unreported:
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
        "reported_cost_usd": pytest.approx(0.00025 * calls),
        "estimated_cost_usd": 0.0,
        "cost_usd": pytest.approx(0.00025 * calls),
        "estimated": False,
        "cost_complete": True,
    }


def test_on_openrouter_without_a_reported_cost_it_is_computed_and_estimated(chat, catalogue):
    usage = result_of(assess(TestClient(app_with_key(chat, catalogue))))["usage"]

    calls = len(chat.requests)
    assert usage["cost_usd"] == pytest.approx(priced(12 * calls, 5 * calls))
    assert usage["estimated"] is True
    assert usage["cost_complete"] is True


def test_a_call_with_no_usage_and_no_cost_leaves_the_cost_incomplete_never_zero(catalogue):
    costing = CostingChat(bare={1})
    app = app_with_key(costing, catalogue)

    usage = result_of(assess(TestClient(app)))["usage"]

    others = len(costing.requests) - 1
    assert usage["cost_complete"] is False
    assert usage["reported_cost_usd"] == pytest.approx(0.00025 * others)  # the known part stays


def test_an_unpriced_call_keeps_the_reported_costs_of_the_others():
    meter = UsageMeter(UNPRICED_MODEL)

    meter.on_llm_end(llm_result(12, 5, cost=0.001))
    meter.on_llm_end(llm_result(12, 5))  # no reported cost, and no price to compute one

    usage = meter.usage()
    assert usage.reported_cost_usd == pytest.approx(0.001)
    assert usage.estimated_cost_usd == 0.0
    assert usage.cost_complete is False
    assert (usage.input_tokens, usage.output_tokens) == (24, 10)


def llm_result(input_tokens: int, output_tokens: int, cost: float | None = None) -> LLMResult:
    meta = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    meta["total_tokens"] = input_tokens + output_tokens
    message = AIMessage("", usage_metadata=meta)
    usage = {"prompt_tokens": input_tokens, "completion_tokens": output_tokens}
    if cost is not None:
        usage["cost"] = cost
    return LLMResult(
        generations=[[ChatGeneration(message=message)]], llm_output={"token_usage": usage}
    )


def test_a_failed_call_counts_what_its_error_carries():
    meter = UsageMeter(DEFAULT_MODEL)
    body = {"usage": {"prompt_tokens": 40, "completion_tokens": 3, "cost": 0.002}}
    failed = AIMessage("", response_metadata={"body": body, "status_code": 502})

    meter.on_llm_error(
        RuntimeError("502"), response=LLMResult(generations=[[ChatGeneration(message=failed)]])
    )

    usage = meter.usage()
    assert (usage.input_tokens, usage.output_tokens) == (40, 3)
    assert usage.reported_cost_usd == pytest.approx(0.002)
    assert usage.cost_complete is True


def test_a_failed_call_carrying_nothing_leaves_the_cost_incomplete():
    meter = UsageMeter(DEFAULT_MODEL)

    meter.on_llm_error(RuntimeError("timed out"), response=LLMResult(generations=[[]]))

    assert meter.usage().cost_complete is False


def test_a_call_stopped_by_the_call_budget_was_never_sent_so_nothing_is_missing():
    meter = UsageMeter(DEFAULT_MODEL)

    meter.on_llm_error(CallBudgetSpent("more than 12 model calls"))

    assert meter.usage().cost_complete is True


def test_an_openrouter_failure_after_retries_marks_the_assessments_cost_incomplete(
    chat, catalogue, monkeypatch
):
    monkeypatch.setattr("openai._base_client.BaseClient._calculate_retry_timeout", lambda *a: 0)
    chat.fail = lambda attempt, request: httpx2.Response(500, json={"error": {"message": "down"}})
    app = app_with_key(chat, catalogue)

    response = assess(TestClient(app, raise_server_exceptions=False))

    assert "event: error" in response.text
    (kept,) = app.state.spending.store.records()
    assert kept.usage.cost_complete is False
    assert app.state.spending.settings()["usage"]["openrouter"]["incomplete"] == 1


def test_a_failed_assessment_still_records_the_tokens_it_spent(chat, catalogue):
    chat.text_only = True  # never answers: stops at the call budget, after paid calls
    app = app_with_key(chat, catalogue)

    response = assess(TestClient(app, raise_server_exceptions=False))

    assert "event: error" in response.text
    (kept,) = app.state.spending.store.records()
    assert kept.usage.input_tokens == 12 * len(chat.requests)
    assert kept.usage.cost_complete is True  # the call past the budget was never sent


# --- An abandoned stream records off the event loop ----------------------------------------------


class SlowStore(MemorySpendingStore):
    def __init__(self) -> None:
        super().__init__()
        self.recorded_on: list[str] = []
        self.done = threading.Event()

    def record(self, record: UsageRecord) -> None:
        time.sleep(0.5)  # a database slow to answer
        self.recorded_on.append(threading.current_thread().name)
        super().record(record)
        self.done.set()


def test_on_the_event_loop_the_usage_is_recorded_in_another_thread_without_blocking_it():
    store = SlowStore()
    guard = SpendingGuard(store, clock=FixedClock(NOV_4_AFTERNOON))
    usage = Usage("fake", 1, 1, 0.0, 0.0, estimated=True, cost_complete=True)

    async def on_the_loop() -> float:
        started = time.monotonic()
        record_off_the_event_loop(guard, "local-user", CONTROL, usage)
        return time.monotonic() - started

    took = asyncio.run(on_the_loop())

    assert took < 0.25
    assert store.done.wait(2)
    assert store.recorded_on != [threading.main_thread().name]


def test_off_the_event_loop_the_usage_is_recorded_at_once():
    store = MemorySpendingStore()
    guard = SpendingGuard(store, clock=FixedClock(NOV_4_AFTERNOON))
    usage = Usage("fake", 1, 1, 0.0, 0.0, estimated=True, cost_complete=True)

    record_off_the_event_loop(guard, "local-user", CONTROL, usage)

    assert len(store.records()) == 1


# --- The developer page totals ------------------------------------------------------------------


def test_the_developer_page_totals_tokens_and_cost_keeping_fake_runs_apart(catalogue):
    app = app_with_key(CostingChat(), catalogue)
    on_the_fake = create_app(Settings())
    on_the_fake.state.spending.store = app.state.spending.store  # one store, as one database
    client = TestClient(app)
    real = [result_of(assess(client))["usage"] for _ in range(2)]
    fake = result_of(assess(TestClient(on_the_fake)))["usage"]

    page = client.get("/api/developer/settings").json()

    assert page["daily_limit"] == DAILY_LIMIT
    assert page["usage"] == {
        "openrouter": {
            "assessments": 2,
            "input_tokens": sum(u["input_tokens"] for u in real),
            "output_tokens": sum(u["output_tokens"] for u in real),
            "reported_cost_usd": pytest.approx(sum(u["reported_cost_usd"] for u in real)),
            "estimated_cost_usd": 0.0,
            "incomplete": 0,
        },
        "fake": {
            "assessments": 1,
            "input_tokens": fake["input_tokens"],
            "output_tokens": fake["output_tokens"],
            "reported_cost_usd": 0.0,
            "estimated_cost_usd": pytest.approx(fake["estimated_cost_usd"]),
            "incomplete": 0,
        },
    }


def test_the_totals_count_an_incomplete_cost_apart():
    store = MemorySpendingStore()
    whole = Usage(DEFAULT_MODEL, 10, 2, 0.5, 0.0, estimated=False, cost_complete=True)
    partial = Usage(UNPRICED_MODEL, 4, 1, 0.25, 0.0, estimated=False, cost_complete=False)
    store.record(UsageRecord("alice", CONTROL, whole, NOV_4_AFTERNOON))
    store.record(UsageRecord("bob", CONTROL, partial, NOV_4_AFTERNOON))

    totals = store.totals()

    assert isinstance(totals, UsageTotals)
    assert totals.to_dict()["openrouter"] == {
        "assessments": 2,
        "input_tokens": 14,
        "output_tokens": 3,
        "reported_cost_usd": 0.75,
        "estimated_cost_usd": 0.0,
        "incomplete": 1,
    }
    assert totals.to_dict()["fake"]["assessments"] == 0


# --- In the database: a real Postgres server, as in tests/test_database.py ----------------------
# `empty_database` is test_database's fixture, so its parameter shadows the import (F811).


@needs_postgres
def test_the_setup_creates_the_counts_and_usage_tables(empty_database):  # noqa: F811
    empty_database.setup()

    assert {database.COUNTS_TABLE, database.USAGE_TABLE} <= tables(empty_database)


@needs_postgres
def test_in_the_database_the_take_reads_the_limit_in_the_same_statement(empty_database):  # noqa: F811
    empty_database.setup()
    store = DatabaseSpendingStore(empty_database)
    today, tomorrow = NOV_4_AFTERNOON.date(), NOV_4_AFTERNOON.date() + timedelta(days=1)

    assert store.take("alice", today) == (True, DAILY_LIMIT)  # no limit stored: the default
    empty_database.set_setting("daily_limit", "2")
    taken = [store.take("alice", today) for _ in range(2)]

    assert taken == [(True, 2), (False, 2)]
    assert store.take("bob", today) == (True, 2)
    assert store.take("alice", tomorrow) == (True, 2)


@needs_postgres
def test_in_the_database_usage_is_kept_and_totalled_apart_for_the_fake(empty_database):  # noqa: F811
    empty_database.setup()
    store = DatabaseSpendingStore(empty_database)
    real = Usage(DEFAULT_MODEL, 10, 2, 0.5, 0.25, estimated=True, cost_complete=False)
    fake = Usage("fake", 7, 3, 0.0, 0.0001, estimated=True, cost_complete=True)
    store.record(UsageRecord("alice", CONTROL, real, NOV_4_AFTERNOON))
    store.record(UsageRecord("alice", CONTROL, fake, NOV_4_AFTERNOON))

    totals = store.totals().to_dict()

    assert totals["openrouter"] == {
        "assessments": 1,
        "input_tokens": 10,
        "output_tokens": 2,
        "reported_cost_usd": 0.5,
        "estimated_cost_usd": 0.25,
        "incomplete": 1,
    }
    assert totals["fake"] == {
        "assessments": 1,
        "input_tokens": 7,
        "output_tokens": 3,
        "reported_cost_usd": 0.0,
        "estimated_cost_usd": pytest.approx(0.0001),
        "incomplete": 0,
    }


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
    assert page["usage"]["fake"]["assessments"] == 2
    assert page["usage"]["fake"]["input_tokens"] == sum(u["input_tokens"] for u in usages)


@needs_postgres
def test_without_the_setup_an_assessment_is_refused_and_the_log_names_it(empty_database, caplog):  # noqa: F811
    client = TestClient(create_app(Settings(database_url=empty_database.url)))

    response = assess(client)
    client.app.state.database.close()

    assert response.status_code == 503
    assert "vouch-db-setup" in caplog.text


@needs_postgres
def test_without_the_setup_a_setting_write_names_the_setup(empty_database, caplog):  # noqa: F811
    with pytest.raises(database.DatabaseUnavailable):
        empty_database.set_setting("daily_limit", "5")

    assert "vouch-db-setup" in caplog.text


# --- The served model (#185) -------------------------------------------------------------------

ROUTER = "typesafe/jev-router"
LUNA = "openai/gpt-6-luna"


class RoutingChat(FakeChat):
    """OpenRouter behind a router: each answer names the model that served it, in turn."""

    def __init__(self, served: list[str]) -> None:
        super().__init__()
        self.served = served

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = super().__call__(request).json()
        body["model"] = self.served[(len(self.requests) - 1) % len(self.served)]
        return httpx2.Response(200, json=body)


def openrouter_model(chat: FakeChat, model: str, meter: UsageMeter):
    openrouter = OpenRouter(
        SecretStr("sk-test"), chat_client=httpx2.Client(transport=httpx2.MockTransport(chat))
    )
    return openrouter.chat_model(model, [meter])


def test_a_router_answer_naming_another_model_is_recorded_as_served_per_call():
    meter = UsageMeter(ROUTER)
    model = openrouter_model(RoutingChat([LUNA, SONNET, LUNA]), ROUTER, meter)

    for _ in range(3):
        model.invoke([HumanMessage("Is the laptop encrypted?")])

    usage = meter.usage()
    assert usage.model == ROUTER  # the requested model stays the usage's model
    assert usage.served_models == {LUNA: 2, SONNET: 1}


def test_a_model_asked_for_directly_is_served_as_itself():
    meter = UsageMeter(DEFAULT_MODEL)
    model = openrouter_model(FakeChat(), DEFAULT_MODEL, meter)

    model.invoke([HumanMessage("Is the laptop encrypted?")])
    model.invoke([HumanMessage("And the backups?")])

    assert meter.usage().served_models == {DEFAULT_MODEL: 2}


def test_a_call_whose_answer_names_no_model_records_no_served_model():
    meter = UsageMeter(UNPRICED_MODEL)

    meter.on_llm_end(llm_result(12, 5, cost=0.001))

    assert meter.usage().served_models == {}


def test_a_streamed_call_names_its_served_model_in_the_messages_metadata():
    message = AIMessage("", response_metadata={"model_name": LUNA})
    meter = UsageMeter(ROUTER)

    meter.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))

    assert meter.usage().served_models == {LUNA: 1}


def test_the_apps_usage_answer_is_unchanged_by_the_served_model(chat, catalogue):
    usage = result_of(assess(TestClient(app_with_key(chat, catalogue))))["usage"]

    assert "served_models" not in usage
# The chat (#44): checked against the day's limit without being counted, its usage totalled


def chat_and_assessment_usage(store) -> UsageTotals:
    chat_usage = Usage("fake", 5, 4, 0.0, 0.0001, estimated=True, cost_complete=True)
    assessed = Usage("fake", 7, 3, 0.0, 0.0001, estimated=True, cost_complete=True)
    guard = SpendingGuard(store, clock=FixedClock(NOV_4_AFTERNOON))
    guard.record("alice", CHAT, chat_usage)
    guard.record("alice", CONTROL, assessed)
    return store.totals()


def test_chat_usage_counts_in_the_tokens_and_cost_but_not_as_an_assessment():
    fake = chat_and_assessment_usage(MemorySpendingStore()).fake

    assert (fake.assessments, fake.input_tokens, fake.output_tokens) == (1, 12, 7)
    assert fake.estimated_cost_usd == pytest.approx(0.0002)


def test_the_chat_is_checked_against_the_days_limit_without_taking_from_it():
    guard = SpendingGuard(MemorySpendingStore(), clock=FixedClock(NOV_4_AFTERNOON))
    guard.set_daily_limit(1)

    guard.check("alice", paid=False)
    guard.check("alice", paid=False)
    guard.start("alice", paid=False)  # the one assessment is still there to take

    with pytest.raises(DailyLimitReached) as refused:
        guard.check("alice", paid=False)
    assert refused.value.limit == 1
    guard.check("bob", paid=False)  # another user's day is their own


def test_a_paid_chat_needs_a_shared_counter_as_a_paid_assessment_does():
    guard = SpendingGuard(MemorySpendingStore(), clock=FixedClock(NOV_4_AFTERNOON))

    with pytest.raises(NoSharedCounter):
        guard.check("alice", paid=True)


@needs_postgres
def test_in_the_database_the_chat_check_takes_nothing_and_chat_usage_isnt_an_assessment(
    empty_database,  # noqa: F811
):
    empty_database.setup()
    store = DatabaseSpendingStore(empty_database)
    guard = SpendingGuard(store, clock=FixedClock(NOV_4_AFTERNOON))
    guard.set_daily_limit(1)

    guard.check("alice", paid=True)
    guard.start("alice", paid=True)
    with pytest.raises(DailyLimitReached):
        guard.check("alice", paid=True)

    fake = chat_and_assessment_usage(store).fake
    assert (fake.assessments, fake.input_tokens, fake.output_tokens) == (1, 12, 7)
