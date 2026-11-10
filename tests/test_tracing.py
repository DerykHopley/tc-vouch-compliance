"""Langfuse tracing of Vouch's runs (#460): off with no keys, one trace per run with them.

Nothing leaves the machine: a traced test hands the Langfuse client an in-memory span exporter
(what the client would send, after its masking) and a tracer provider of its own, and the suite
runs with sockets disabled.
"""

import json
import logging
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

from vouch import evals
from vouch.api import create_app, current_user
from vouch.evals import EvalModels, fake_models, load_cases, run_eval
from vouch.graph import PROMPT_VERSION
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
