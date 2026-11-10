"""Tracing Vouch's runs in Langfuse (#460), the course's LLM observability task.

With both Langfuse keys set (vouch.settings.LangfuseKeys), each assessment run, chat turn and
profile pass is one trace: its LangGraph steps, each model call (prompt, answer, model, tokens),
and each tool call, tagged with the profile, the control, the model and the prompt version, its
user the signed-in user's id. With either key unset, `start_tracing` gives None: no Langfuse
client is made, nothing is imported from it, and no call is sent.

A run is traced by its RunnableConfig: `traced` gives one holding a fresh Langfuse LangChain
handler and the trace's attributes (Langfuse's `langfuse_*` metadata keys); the graph's run
config takes it, and every step and model call inside inherits it. The review's resume
(Confirm findings, Accept, Reject) isn't traced: no model runs there, and its input carries the
reviewer's name, Clerk user data a trace never holds.

What leaves for Langfuse is redacted at the last stage of export (RedactingSpanExporter, which
wraps the exporter that sends): every span's attributes, its status (where Langfuse puts a
failing step's error text) and its events (an exception's message and stack trace), through
vouch.redaction, as the API's events and log lines are, so no key, token, password or e-mail
address reaches a trace. A batch that can't be redacted is dropped, never sent. Headers and Clerk
session tokens never enter a run.
"""

import base64
import json
import logging
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.runnables.config import ensure_config, get_callback_manager_for_config
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Status

from vouch.settings import LangfuseKeys

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

log = logging.getLogger(__name__)

FAKE_MODEL_TAG = "fake"
"""A trace's model tag on the scripted fake (no model id), as vouch.spending names it."""
TraceKind = Literal["assessment", "chat turn", "profile pass", "eval"]
"""The kinds of run traced, each trace's name."""
ASSESSMENT: TraceKind = "assessment"
CHAT_TURN: TraceKind = "chat turn"
PROFILE_PASS: TraceKind = "profile pass"
EVAL: TraceKind = "eval"


@dataclass(frozen=True)
class TraceFacts:
    """What a trace is named and tagged with: the kind of run (`name`), whose it is (the
    signed-in user's id, never an e-mail or a name), the profile, the model (None: the fake),
    the prompt version, and where it applies, the control, the assessment's thread and the
    chat's (`session_id`, which groups a conversation's turns)."""

    name: TraceKind
    user_id: str
    profile_id: str
    model: str | None
    prompt_version: str
    control_id: str | None = None
    thread_id: str | None = None
    session_id: str | None = None

    def metadata(self) -> dict[str, Any]:
        """The run config's metadata: Langfuse's trace attributes, and the facts themselves."""
        model = self.model or FAKE_MODEL_TAG
        tags = [f"profile:{self.profile_id}", f"model:{model}", f"prompt:{self.prompt_version}"]
        if self.control_id:
            tags.append(f"control:{self.control_id}")
        facts = {
            "profile_id": self.profile_id,
            "control_id": self.control_id,
            "model": model,
            "prompt_version": self.prompt_version,
            "thread_id": self.thread_id,
        }
        trace: dict[str, Any] = {
            "langfuse_trace_name": self.name,
            "langfuse_user_id": self.user_id,
            "langfuse_tags": tags,
        }
        if self.session_id:
            trace["langfuse_session_id"] = self.session_id
        return trace | {k: v for k, v in facts.items() if v is not None}


class Tracing:
    """One Langfuse client for the process, and a fresh handler per run (`config`).

    `span_exporter` and `tracer_provider` are the test seams: an in-memory exporter receives
    what would be sent, redacted; with neither, the client sends to `keys.base_url`
    (langfuse_exporter). Either way RedactingSpanExporter wraps it."""

    def __init__(
        self,
        keys: LangfuseKeys,
        *,
        span_exporter: "SpanExporter | None" = None,
        tracer_provider: "TracerProvider | None" = None,
    ) -> None:
        from langfuse import Langfuse

        self.public_key = keys.public_key
        sending = span_exporter or langfuse_exporter(keys)
        self.client = Langfuse(
            public_key=keys.public_key,
            secret_key=keys.secret_key.get_secret_value(),
            base_url=keys.base_url,
            span_exporter=RedactingSpanExporter(sending),
            tracer_provider=tracer_provider,
        )

    def __repr__(self) -> str:
        return f"Tracing(public_key={self.public_key!r})"

    def config(self, facts: TraceFacts) -> RunnableConfig:
        """The run config that traces one run: a new handler, and the trace's attributes."""
        from langfuse.langchain import CallbackHandler

        return {
            "callbacks": [CallbackHandler(public_key=self.public_key)],
            "metadata": facts.metadata(),
        }

    def flush(self) -> None:
        """Send what the client holds now, and wait for it; a failure is logged, never the
        run's."""
        try:
            self.client.flush()
        except Exception:
            log.exception("the traces could not be sent to Langfuse")

    def flush_soon(self) -> None:
        """`flush` in a thread of its own: a run never waits on Langfuse, and a stream closed on
        the event loop never blocks it."""
        threading.Thread(target=self.flush, name="langfuse-flush", daemon=True).start()

    def shutdown(self) -> None:
        """Send what the client holds and stop it, when the app or the eval runner ends; a
        failure is logged, never the caller's."""
        try:
            self.client.shutdown()
        except Exception:
            log.exception("the Langfuse client could not be shut down")


def start_tracing(keys: LangfuseKeys | None) -> Tracing | None:
    """Tracing to the keys' Langfuse project; None, and no client made, without keys."""
    return Tracing(keys) if keys is not None else None


@contextmanager
def traced(tracing: Tracing | None, facts: TraceFacts) -> Iterator[RunnableConfig]:
    """The run config for one traced run (empty with tracing off, or when its handler can't be
    made: logged, and the run goes on untraced), its trace sent as the run ends, in the
    background (`flush_soon`): on Cloud Run an instance may have little CPU between requests, so
    it isn't left to the client's own five-second schedule."""
    if tracing is None:
        yield {}
        return
    try:
        config = tracing.config(facts)
    except Exception:
        log.exception("a %s couldn't be traced", facts.name)
        yield {}
        return
    try:
        yield config
    finally:
        tracing.flush_soon()


def with_run_config(config: RunnableConfig, run_config: RunnableConfig | None) -> RunnableConfig:
    """A graph run's own `config` (its thread, its limits) with a trace's `run_config` joined;
    the run's own keys win."""
    return {**(run_config or {}), **config}


def run_traced[T](config: RunnableConfig, name: str, call: Callable[[], T]) -> T:
    """`call()` as one run named `name` under `config`, so every model call and graph run inside
    joins its trace; with an empty config (tracing off), just `call()`."""
    if not config:
        return call()

    def run(_: object) -> T:
        return call()

    return RunnableLambda(run, name=name).invoke(None, config)


def tool_span[T](name: str, args: Mapping[str, Any], call: Callable[[], T]) -> T:
    """`call()`, one tool call the graph runs itself, seen as a tool run by the callbacks of the
    step it runs in (a trace's tool span); with none, just `call()`."""
    manager = get_callback_manager_for_config(ensure_config())
    if not manager.handlers:
        return call()
    run = manager.on_tool_start(
        {"name": name}, json.dumps(dict(args), default=str), name=name, inputs=dict(args)
    )
    try:
        result = call()
    except BaseException as error:
        run.on_tool_error(error)
        raise
    run.on_tool_end(result)
    return result


def _clean(value: Any) -> tuple[Any, bool]:
    """A span attribute's value redacted (vouch.redaction), and whether anything was removed.
    JSON (how Langfuse writes inputs, outputs and metadata) is redacted value by value, so it
    stays JSON; any other text as it is; a sequence item by item."""
    from vouch.redaction import redact, redact_data

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            text, removed = redact(value)
            return text, bool(removed)
        clean, removed = redact_data(decoded)
        return (json.dumps(clean, ensure_ascii=False), True) if removed else (value, False)
    if isinstance(value, list | tuple):
        clean, removed = redact_data(value)  # a sequence of plain values: tags
        return clean, bool(removed)
    return value, False


def _redacted_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    return {key: _clean(value)[0] for key, value in (attributes or {}).items()}


def _redacted_span(span: ReadableSpan) -> ReadableSpan:
    """The span as it may leave: its attributes, its status's description and its events'
    attributes redacted; its name, ids, times and links as they are."""
    from vouch.redaction import redact

    status = span.status
    if status.description:
        status = Status(status.status_code, redact(status.description)[0])
    events = [
        Event(event.name, _redacted_attributes(event.attributes), event.timestamp)
        for event in span.events
    ]
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=_redacted_attributes(span.attributes),
        events=events,
        links=span.links,
        kind=span.kind,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class RedactingSpanExporter(SpanExporter):
    """The last stage before a span leaves for Langfuse: each span redacted (_redacted_span),
    then handed to `sending`. A batch that can't be redacted is dropped and logged, never sent."""

    def __init__(self, sending: SpanExporter) -> None:
        self.sending = sending

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            clean = [_redacted_span(span) for span in spans]
        except Exception:
            log.exception("%d spans couldn't be redacted, so they weren't sent", len(spans))
            return SpanExportResult.FAILURE
        return self.sending.export(clean)

    def shutdown(self) -> None:
        self.sending.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self.sending.force_flush(timeout_millis)


LANGFUSE_TRACES_PATH = "/api/public/otel/v1/traces"
"""Langfuse's OpenTelemetry endpoint (its OpenTelemetry integration page)."""
LANGFUSE_EXPORT_TIMEOUT_SECONDS = 5.0
"""As the Langfuse SDK's own exporter: one export's limit."""


def langfuse_exporter(keys: LangfuseKeys) -> SpanExporter:
    """The exporter that sends to the keys' Langfuse project over OTLP/HTTP, as the Langfuse SDK
    builds its own: basic auth with the key pair, and the ingestion version that shows a trace
    at once (without it, up to ten minutes later)."""
    from opentelemetry.exporter.otlp.proto.http import Compression
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    pair = f"{keys.public_key}:{keys.secret_key.get_secret_value()}"
    return OTLPSpanExporter(
        endpoint=keys.base_url + LANGFUSE_TRACES_PATH,
        headers={
            "Authorization": "Basic " + base64.b64encode(pair.encode()).decode("ascii"),
            "x-langfuse-public-key": keys.public_key,
            "x-langfuse-ingestion-version": "4",
        },
        timeout=LANGFUSE_EXPORT_TIMEOUT_SECONDS,
        compression=Compression.Gzip,
    )
