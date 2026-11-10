"""Langfuse tracing of Vouch's runs (#460): off with no keys, one trace per run with them.

Nothing leaves the machine: a traced test hands the Langfuse client an in-memory span exporter
(what the client would send, after its masking) and a tracer provider of its own, and the suite
runs with sockets disabled.
"""

import asyncio
import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr
from test_memory import as_user
from test_model_choice import CONTROL, FakeCatalogue, app_with_key
from test_spending import UNPRICED_MODEL, CostingChat, llm_result

from vouch import ScriptedChatModel, evals
from vouch import tracing as tracing_module
from vouch.api import create_app, current_user
from vouch.evals import EvalModels, fake_models, load_cases, run_eval
from vouch.graph import PROMPT_VERSION
from vouch.model_choice import DEFAULT_MODEL
from vouch.paths import FIXTURES_DIR, REPO_DIR
from vouch.profile import load_named_profile, read_profile_once
from vouch.settings import (
    LANGFUSE_CLOUD,
    LangfuseKeys,
    Settings,
    SettingsError,
    load_langfuse_keys,
    load_settings,
)
from vouch.spending import CHAT, GPT_LUNA, PRICES, PROFILE_PASS
from vouch.tracing import TraceFacts, Tracing, run_traced, traced

KEYS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-test-public", "LANGFUSE_SECRET_KEY": "sk-lf-test-secret"}
LOCAL = "http://localhost:3300"
DEMO_CONTROL = "4.1.2"


# --- The settings -------------------------------------------------------------------------------


def test_with_no_langfuse_keys_there_is_nothing_to_trace_with():
    assert load_settings({}).langfuse is None
    assert load_langfuse_keys({}) is None


def test_with_both_keys_tracing_goes_to_langfuse_cloud_unless_a_base_url_is_set():
    keys = load_settings(KEYS).langfuse
    assert keys is not None
    assert keys.public_key == "pk-lf-test-public"
    assert keys.secret_key.get_secret_value() == "sk-lf-test-secret"
    assert "sk-lf-test-secret" not in repr(keys)
    assert keys.base_url == LANGFUSE_CLOUD
    assert load_settings(KEYS | {"LANGFUSE_BASE_URL": LOCAL}).langfuse.base_url == LOCAL  # type: ignore[union-attr]


@pytest.mark.parametrize("name", sorted(KEYS))
def test_one_key_alone_leaves_tracing_off_and_says_which_is_missing(name, caplog):
    caplog.set_level(logging.WARNING)

    assert load_settings({name: KEYS[name]}).langfuse is None

    missing = next(n for n in KEYS if n != name)
    assert missing in caplog.text and "tracing is off" in caplog.text
    assert KEYS[name] not in caplog.text


def test_a_base_url_that_isnt_an_origin_refuses_naming_the_setting():
    with pytest.raises(SettingsError, match="LANGFUSE_BASE_URL"):
        load_settings(KEYS | {"LANGFUSE_BASE_URL": "cloud.langfuse.com/api"})


def test_a_base_url_without_keys_is_never_read_and_a_trailing_slash_is_fine():
    assert load_settings({"LANGFUSE_BASE_URL": "not a url"}).langfuse is None
    keys = load_settings(KEYS | {"LANGFUSE_BASE_URL": "https://cloud.langfuse.com/"}).langfuse
    assert keys is not None and keys.base_url == LANGFUSE_CLOUD


# --- No keys: no client, no handler, no call -----------------------------------------------------


@pytest.fixture
def langfuse_made(monkeypatch) -> list[str]:
    """Every Langfuse client and LangChain handler made, by kind, from here on."""
    import langfuse
    import langfuse.langchain

    made: list[str] = []
    real_client, real_handler = (
        langfuse.Langfuse.__init__,
        langfuse.langchain.CallbackHandler.__init__,
    )

    def client(self, *args, **kwargs):
        made.append("client")
        real_client(self, *args, **kwargs)

    def handler(self, *args, **kwargs):
        made.append("handler")
        real_handler(self, *args, **kwargs)

    monkeypatch.setattr(langfuse.Langfuse, "__init__", client)
    monkeypatch.setattr(langfuse.langchain.CallbackHandler, "__init__", handler)
    return made


def test_with_no_keys_an_assessment_runs_with_no_client_and_no_handler(langfuse_made):
    """Sockets are disabled for the whole suite (pyproject's addopts), as in CI's fast lane: any
    call out would fail the run, not pass it."""
    read_profile_once.cache_clear()
    app = create_app(Settings())
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    assert response.status_code == 200
    assert "event: result" in response.text
    assert app.state.models.tracing is None
    assert langfuse_made == []


def test_traced_with_tracing_off_gives_an_empty_config_and_run_traced_just_calls():
    facts = TraceFacts("assessment", "u", "nordlicht", None, PROMPT_VERSION)
    with traced(None, facts) as config:
        assert config == {}
        assert run_traced(config, "profile pass", lambda: 42) == 42


# --- Keys set: one trace per run ---------------------------------------------------------------


@pytest.fixture
def exported() -> Iterator[tuple[Tracing, InMemorySpanExporter]]:
    """Tracing on, its spans kept in memory: what the client would send, masked. A public key of
    its own, as the Langfuse SDK keeps one client per public key in the process."""
    exporter = InMemorySpanExporter()
    keys = LangfuseKeys(f"pk-lf-test-{uuid.uuid4().hex}", SecretStr("sk-lf-test-secret"), LOCAL)
    on = Tracing(keys, span_exporter=exporter, tracer_provider=TracerProvider())
    yield on, exporter
    on.shutdown()


@pytest.fixture
def catalogue() -> FakeCatalogue:
    return FakeCatalogue([DEFAULT_MODEL])


def traces(on: Tracing, exporter: InMemorySpanExporter) -> dict[str, list[ReadableSpan]]:
    """The exported spans by trace id, once the client has sent what it holds."""
    on.flush()
    found: dict[str, list[ReadableSpan]] = defaultdict(list)
    for span in exporter.get_finished_spans():
        found[format(span.context.trace_id, "032x")].append(span)
    return found


def of_type(spans: list[ReadableSpan], kind: str) -> list[ReadableSpan]:
    return [s for s in spans if (s.attributes or {}).get("langfuse.observation.type") == kind]


def trace_attributes(spans: list[ReadableSpan]) -> dict:
    """The trace's own attributes (name, user, tags), as the root span carries them."""
    merged: dict = {}
    for span in spans:
        merged |= {
            k: v
            for k, v in (span.attributes or {}).items()
            if not k.startswith("langfuse.observation")
        }
    return merged


def traced_app(on: Tracing, settings: Settings | None = None):
    """A new app on the in-memory backend with tracing on, the user set by a test header."""
    read_profile_once.cache_clear()
    app = create_app(settings or Settings())
    app.state.models.tracing = on
    app.dependency_overrides[current_user] = as_user
    return app


def new_user() -> dict[str, str]:
    return {"X-Test-User": f"user_{uuid.uuid4().hex[:8]}"}


def test_an_assessment_is_one_trace_with_its_steps_model_calls_and_tool_calls(exported):
    on, exporter = exported
    client, lead = TestClient(traced_app(on)), new_user()

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=lead)

    assert "event: result" in response.text
    [spans] = traces(on, exporter).values()
    names = {s.name for s in spans}
    assert {"apply_overlays", "gather", "tools", "judge", "remediate"} <= names
    assert len(of_type(spans, "generation")) == 4  # the demo script: 2 gather, judge, a draft
    assert {s.name for s in of_type(spans, "tool")} == {"search_policies"}
    attributes = trace_attributes(spans)
    assert attributes["user.id"] == lead["X-Test-User"]
    assert attributes["langfuse.trace.name"] == "assessment"
    assert set(attributes["langfuse.trace.tags"]) == {
        "profile:nordlicht",
        f"control:{DEMO_CONTROL}",
        "model:fake",
        f"prompt:{PROMPT_VERSION}",
    }


def test_two_assessments_are_two_traces(exported):
    on, exporter = exported
    client, lead = TestClient(traced_app(on)), new_user()

    for _ in range(2):
        client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=lead)

    assert len(traces(on, exporter)) == 2


def test_a_chat_turn_is_one_trace_with_its_steps_and_model_call(exported):
    on, exporter = exported
    client, lead = TestClient(traced_app(on)), new_user()

    response = client.post(
        "/api/chat", json={"page": "dossier", "message": "Where do I stand?"}, headers=lead
    )

    assert "event: done" in response.text
    [spans] = traces(on, exporter).values()
    assert {"agent", "answer"} <= {s.name for s in spans}
    assert of_type(spans, "generation")
    attributes = trace_attributes(spans)
    assert attributes["langfuse.trace.name"] == "chat turn"
    assert attributes["user.id"] == lead["X-Test-User"]
    thread = json.loads(response.text.split("\n")[1].removeprefix("data: "))["thread_id"]
    assert attributes["session.id"] == thread


def test_an_assessment_the_chat_starts_is_a_trace_of_its_own_beside_the_turn_s(exported):
    on, exporter = exported
    client, lead = TestClient(traced_app(on)), new_user()

    response = client.post(
        "/api/chat", json={"page": "dossier", "message": "Assess 4.1.2"}, headers=lead
    )

    assert "event: done" in response.text
    found = traces(on, exporter)
    names = sorted(trace_attributes(spans)["langfuse.trace.name"] for spans in found.values())
    assert names == ["assessment", "chat turn"]
    [assessment] = [s for s in found.values() if "judge" in {span.name for span in s}]
    assert len(of_type(assessment, "generation")) == 4
    assert trace_attributes(assessment)["user.id"] == lead["X-Test-User"]


def test_a_handler_that_cannot_be_made_leaves_the_run_untraced_not_failed(exported, monkeypatch):
    on, _ = exported
    monkeypatch.setattr(on, "config", lambda facts: 1 / 0)
    client, lead = TestClient(traced_app(on)), new_user()

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=lead)

    assert "event: result" in response.text


def test_a_profile_pass_is_one_trace_with_its_model_call_and_each_control_s_steps(exported):
    on, exporter = exported
    client, lead = TestClient(traced_app(on)), new_user()

    response = client.post("/api/profile-pass", headers=lead)

    assert response.status_code == 200, response.text
    [spans] = traces(on, exporter).values()
    assert len(of_type(spans, "generation")) == 1
    assert "review" in {s.name for s in spans}  # each control's thread run to its review
    attributes = trace_attributes(spans)
    assert attributes["langfuse.trace.name"] == "profile pass"
    assert "profile:nordlicht" in attributes["langfuse.trace.tags"]


# --- Sent inside the request (#510) --------------------------------------------------------------
#
# On Cloud Run a request has CPU only until its response is done: a trace sent after that may
# wait for the next request, or be lost. So a run's trace is sent before its response ends,
# waiting at most TRACE_SEND_WAIT_SECONDS for Langfuse.


class SlowExporter(InMemorySpanExporter):
    """An exporter that takes `seconds` to send, as Langfuse over the network does; with `hang`,
    it answers only once released (or after HANG_SECONDS), as a Langfuse that is down."""

    HANG_SECONDS = 8.0

    def __init__(self, seconds: float = 0.0, *, hang: bool = False) -> None:
        super().__init__()
        self.seconds, self.hang = seconds, hang
        self.released = threading.Event()

    def export(self, spans):
        if self.hang:
            self.released.wait(self.HANG_SECONDS)
        else:
            time.sleep(self.seconds)
        return super().export(spans)


def tracing_to(exporter: SlowExporter) -> Tracing:
    keys = LangfuseKeys(f"pk-lf-test-{uuid.uuid4().hex}", SecretStr("sk-lf-test-secret"), LOCAL)
    return Tracing(keys, span_exporter=exporter, tracer_provider=TracerProvider())


@pytest.fixture
def slow() -> Iterator[tuple[Tracing, SlowExporter]]:
    """Tracing on, each export taking a third of a second: a send left to the background can't
    finish before the response does."""
    exporter = SlowExporter(0.3)
    on = tracing_to(exporter)
    yield on, exporter
    on.shutdown()


@pytest.fixture
def down() -> Iterator[tuple[Tracing, SlowExporter]]:
    """Tracing on, to a Langfuse that never answers."""
    exporter = SlowExporter(hang=True)
    on = tracing_to(exporter)
    yield on, exporter
    exporter.released.set()
    on.shutdown()


def sent_by_the_end(on: Tracing, exporter: InMemorySpanExporter, response) -> None:
    """Every span the run made was exported by the time its response ended: none comes later."""
    at_the_end = len(exporter.get_finished_spans())
    assert at_the_end > 0, response.text
    on.flush()
    assert len(exporter.get_finished_spans()) == at_the_end


def test_an_assessment_s_trace_is_sent_before_its_stream_ends(slow):
    on, exporter = slow
    client, lead = TestClient(traced_app(on)), new_user()

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=lead)

    assert "event: result" in response.text
    sent_by_the_end(on, exporter, response)


def test_a_chat_turn_s_trace_and_its_assessment_s_are_sent_before_its_stream_ends(slow):
    on, exporter = slow
    client, lead = TestClient(traced_app(on)), new_user()

    response = client.post(
        "/api/chat", json={"page": "dossier", "message": "Assess 4.1.2"}, headers=lead
    )

    assert "event: done" in response.text
    sent_by_the_end(on, exporter, response)
    found = traces(on, exporter).values()
    assert {trace_attributes(s)["langfuse.trace.name"] for s in found} == {
        "assessment",
        "chat turn",
    }


def test_a_profile_pass_s_trace_is_sent_before_its_answer(slow):
    on, exporter = slow
    client, lead = TestClient(traced_app(on)), new_user()

    response = client.post("/api/profile-pass", headers=lead)

    assert response.status_code == 200, response.text
    sent_by_the_end(on, exporter, response)


def test_a_langfuse_that_never_answers_holds_the_response_at_most_the_bound(
    down, monkeypatch, caplog
):
    on, _ = down
    monkeypatch.setattr(tracing_module, "TRACE_SEND_WAIT_SECONDS", 0.5)
    client, lead = TestClient(traced_app(on)), new_user()

    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="vouch.tracing"):
        response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=lead)
    took = time.monotonic() - started

    assert "event: result" in response.text
    assert took < 0.5 + 2.5  # the bound, and the fake run's own time
    assert "wasn't sent to Langfuse within 0.5 s" in caplog.text


def test_a_stream_closed_on_the_event_loop_never_waits_for_langfuse(down):
    """A stream the client leaves is closed when it's collected, which may be on the event
    loop: there, the trace goes in the background, so no other request stalls."""
    on, _ = down
    facts = TraceFacts("assessment", "u", "nordlicht", None, PROMPT_VERSION)

    async def run_and_close() -> float:
        started = time.monotonic()
        with traced(on, facts) as config:
            run_traced(config, "assessment", lambda: 42)
        return time.monotonic() - started

    assert asyncio.run(run_and_close()) < 0.5


# --- Each generation's cost and model (#504)----------------------------------------------------

COST = "langfuse.observation.cost_details"
MODEL = "langfuse.observation.model.name"


def costs(spans: list[ReadableSpan]) -> list[dict[str, float]]:
    """Each generation's cost details, as Langfuse reads them: a JSON object in USD."""
    return [json.loads(s.attributes[COST]) for s in of_type(spans, "generation")]  # type: ignore[index,arg-type]


def kept_cost(app, user: dict[str, str], control_id: str | None = None) -> float:
    """The cost Vouch kept for the user's runs (the in-memory spending store's records)."""
    return sum(
        r.usage.cost_usd
        for r in app.state.spending.store.records()
        if r.user_id == user["X-Test-User"] and control_id in (None, r.control_id)
    )


def assert_each_generation_costed(spans: list[ReadableSpan], model: str) -> float:
    """Every generation carries the model and its cost (input, output and total, in USD);
    gives the trace's total."""
    generations = of_type(spans, "generation")
    assert generations
    found = costs(spans)
    for cost in found:
        assert set(cost) == {"input", "output", "total"}
        assert cost["total"] > 0
        assert cost["input"] + cost["output"] == pytest.approx(cost["total"])
    assert {s.attributes.get(MODEL) for s in generations} == {model}  # type: ignore[union-attr]
    return sum(cost["total"] for cost in found)


def test_each_generation_of_an_assessment_carries_the_cost_vouch_records(exported):
    on, exporter = exported
    app = traced_app(on)
    client, lead = TestClient(app), new_user()

    client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=lead)

    [spans] = traces(on, exporter).values()
    total = assert_each_generation_costed(spans, "fake")
    assert total == pytest.approx(kept_cost(app, lead))


def test_each_generation_of_a_chat_turn_carries_the_cost_vouch_records(exported):
    on, exporter = exported
    app = traced_app(on)
    client, lead = TestClient(app), new_user()

    response = client.post(
        "/api/chat", json={"page": "dossier", "message": "Where do I stand?"}, headers=lead
    )

    assert "event: done" in response.text
    [spans] = traces(on, exporter).values()
    total = assert_each_generation_costed(spans, "fake")
    assert total == pytest.approx(kept_cost(app, lead, CHAT))


def test_the_profile_pass_generation_carries_the_cost_vouch_records(exported):
    on, exporter = exported
    app = traced_app(on)
    client, lead = TestClient(app), new_user()

    response = client.post("/api/profile-pass", headers=lead)

    assert response.status_code == 200, response.text
    [spans] = traces(on, exporter).values()
    total = assert_each_generation_costed(spans, "fake")
    assert total == pytest.approx(kept_cost(app, lead, PROFILE_PASS))


@pytest.mark.filterwarnings("ignore:A test tried to use socket.socket")
def test_on_openrouter_each_generation_carries_openrouter_s_reported_cost_and_the_model(
    exported, catalogue
):
    """The live case: OpenRouter's Claude ids (`anthropic/claude-haiku-5.5`,
    `anthropic/claude-sonnet-5.5`) match no model Langfuse prices (its own are spelt
    `claude-haiku-5-5`, `claude-sonnet-5-5`), so only the cost Vouch sends fills the Cost
    column. OpenRouter's reported cost goes as it is, never re-estimated."""
    on, exporter = exported
    costing = CostingChat()
    app = app_with_key(costing, catalogue)
    app.state.models.tracing = on
    app.dependency_overrides[current_user] = as_user
    lead = new_user()

    TestClient(app).post("/api/assessments", json={"control_id": CONTROL}, headers=lead)

    [spans] = traces(on, exporter).values()
    assert_each_generation_costed(spans, DEFAULT_MODEL)
    assert [c["total"] for c in costs(spans)] == [pytest.approx(0.00025)] * len(costing.requests)


def test_a_call_with_no_reported_cost_carries_the_estimate_split_by_the_model_s_prices():
    price = PRICES[GPT_LUNA]

    cost = tracing_module.cost_details(llm_result(1000, 200), GPT_LUNA)

    assert cost == {
        "input": pytest.approx(price.cost(1000, 0)),
        "output": pytest.approx(price.cost(0, 200)),
        "total": pytest.approx(price.cost(1000, 200)),
    }


def test_a_reported_cost_is_the_total_split_in_the_model_s_price_proportions():
    cost = tracing_module.cost_details(llm_result(1000, 200, cost=0.004), GPT_LUNA)

    assert cost is not None
    share = PRICES[GPT_LUNA].cost(1000, 0) / PRICES[GPT_LUNA].cost(1000, 200)
    assert cost == {
        "input": pytest.approx(0.004 * share),
        "output": pytest.approx(0.004 * (1 - share)),
        "total": pytest.approx(0.004),
    }


def test_a_reported_cost_on_an_unpriced_model_is_sent_as_its_total():
    cost = tracing_module.cost_details(llm_result(12, 5, cost=0.001), UNPRICED_MODEL)

    assert cost == {"total": pytest.approx(0.001)}


def test_a_call_whose_cost_cant_be_known_sends_none_never_zero():
    assert tracing_module.cost_details(llm_result(12, 5), UNPRICED_MODEL) is None


# --- What is never sent ------------------------------------------------------------------------

API_KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"
BEARER = "Authorization: Bearer abc123.def456.ghi789"
EMAIL = "jane.doe@alpenwerk.example"


def test_no_key_token_or_e_mail_reaches_a_trace(exported):
    on, exporter = exported
    held = f"key {API_KEY}, header {BEARER}, mail {EMAIL}"
    facts = TraceFacts("assessment", "user_1", "nordlicht", None, PROMPT_VERSION)

    with traced(on, facts) as config:
        run_traced(config, "leaky", lambda: {"said": held, "nested": [held], held: "as a key"})
        RunnableLambda(lambda text: f"echo: {text}", name="echo").invoke(held, config)

    sent = json.dumps(
        [dict(s.attributes or {}) for spans in traces(on, exporter).values() for s in spans]
    )
    assert "[removed]" in sent  # the run reached the export, masked
    for secret in (API_KEY, "abc123.def456.ghi789", EMAIL):
        assert secret not in sent


def sent_text(spans: list[ReadableSpan]) -> str:
    """Everything a span carries out: its attributes, its status's description, and each event's
    name and attributes (an exception's message and stack trace among them)."""
    return json.dumps(
        [
            {
                "attributes": dict(s.attributes or {}),
                "status": s.status.description,
                "events": [{"name": e.name, **dict(e.attributes or {})} for e in s.events],
            }
            for s in spans
        ],
        default=str,
    )


def test_a_failing_run_s_error_reaches_the_trace_with_no_key_token_or_e_mail(exported, monkeypatch):
    """The verifier's case (#460): the model's error text holds secrets, and Langfuse copies an
    error into each failing span's status and exception events, which its attribute mask
    doesn't reach."""
    on, exporter = exported
    held = f"provider said: key {API_KEY}, header {BEARER}, mail {EMAIL}"

    def failing(self, *args, **kwargs):
        raise RuntimeError(held)

    monkeypatch.setattr(ScriptedChatModel, "_generate", failing)
    client, lead = TestClient(traced_app(on)), new_user()

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=lead)

    assert "event: error" in response.text
    [spans] = traces(on, exporter).values()
    failed = [s for s in spans if s.status.description]
    assert failed  # the error did reach the trace, as a status
    sent = sent_text(spans)
    assert "provider said" in sent and "[removed]" in sent  # kept, but masked
    for secret in (API_KEY, "abc123.def456.ghi789", EMAIL):
        assert secret not in sent


def test_an_exception_event_is_redacted_and_a_batch_that_cant_be_is_never_sent(monkeypatch):
    from opentelemetry.trace import Status, StatusCode

    sending = InMemorySpanExporter()
    exporter = tracing_module.RedactingSpanExporter(sending)
    provider = TracerProvider()
    span = provider.get_tracer("t").start_span("gather")
    held = f"key {API_KEY}, mail {EMAIL}"
    span.record_exception(RuntimeError(held))
    span.set_status(Status(StatusCode.ERROR, held))
    span.end()

    exporter.export([span])
    [sent] = sending.get_finished_spans()
    text = sent_text([sent])
    assert "exception.message" in text and "[removed]" in text
    assert API_KEY not in text and EMAIL not in text

    monkeypatch.setattr(tracing_module, "_clean", lambda value: 1 / 0)
    assert exporter.export([span]).name == "FAILURE"
    assert len(sending.get_finished_spans()) == 1  # nothing more went out


def test_a_span_with_nothing_to_remove_is_sent_as_it_is(exported):
    on, exporter = exported
    facts = TraceFacts("assessment", "user_1", "nordlicht", None, PROMPT_VERSION)

    with traced(on, facts) as config:
        run_traced(config, "plain", lambda: {"said": "Nordlicht's policy, § 4 (ü)"})

    [spans] = traces(on, exporter).values()
    [plain] = [s for s in spans if s.name == "plain"]
    assert json.loads(plain.attributes["langfuse.observation.output"]) == {  # type: ignore[index]
        "said": "Nordlicht's policy, § 4 (ü)"
    }


# --- The eval runner ---------------------------------------------------------------------------


def test_the_eval_runner_reads_no_keys_without_trace():
    assert evals.trace_keys(trace=False, env=KEYS | {"LANGFUSE_BASE_URL": LOCAL}) is None


def test_the_eval_runner_traces_with_trace_to_the_local_langfuse():
    keys = evals.trace_keys(trace=True, env=KEYS | {"LANGFUSE_BASE_URL": LOCAL})
    assert keys is not None and keys.base_url == LOCAL


@pytest.mark.parametrize(
    "env",
    [
        KEYS,
        KEYS | {"LANGFUSE_BASE_URL": "https://cloud.langfuse.com"},
        {"LANGFUSE_BASE_URL": LOCAL},
    ],
    ids=["cloud-by-default", "cloud", "no-keys"],
)
def test_trace_to_anything_but_a_local_langfuse_is_refused(env):
    with pytest.raises(evals.TraceRefused):
        evals.trace_keys(trace=True, env=env)


def test_trace_to_langfuse_cloud_stops_the_runner_before_any_case(tmp_path, monkeypatch):
    for name, value in (KEYS | {"LANGFUSE_BASE_URL": LANGFUSE_CLOUD}).items():
        monkeypatch.setenv(name, value)

    assert evals.main(["--trace", "--results", str(tmp_path)]) == 2
    assert not list(tmp_path.iterdir())


def test_an_eval_case_on_trace_is_one_trace(exported):
    on, exporter = exported
    profile = load_named_profile("nordlicht")
    cases = load_cases(FIXTURES_DIR / "nordlicht" / "expected-answers.yaml", "nordlicht")
    scripts = REPO_DIR / "evals" / "scripts" / "nordlicht"
    models = EvalModels("fake", fake_models(scripts))

    run_eval(cases.cases[:1], profile, models, today=date(2026, 5, 1), tracing=on)

    [spans] = traces(on, exporter).values()
    assert trace_attributes(spans)["langfuse.trace.name"] == "eval"
