"""Tracing Vouch's runs in Langfuse (#460), the course's LLM observability task.

With both Langfuse keys set (vouch.settings.LangfuseKeys), each assessment run, chat turn and
profile pass is one trace: its LangGraph steps, each model call (prompt, answer, model, tokens,
and the cost Vouch records for it, #504: cost_details; a failed call's too, or none, #508),
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

import asyncio
import base64
import json
import logging
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from langchain_core.outputs import LLMResult
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.runnables.config import ensure_config, get_callback_manager_for_config
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Status

from vouch.settings import LangfuseKeys

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

    from vouch.spending import Usage

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
        handler = _costing_handler()(public_key=self.public_key, model=facts.model)
        return {"callbacks": [handler], "metadata": facts.metadata()}

    def flush(self) -> None:
        """Send what the client holds now, and wait for it; a failure is logged, never the
        run's."""
        try:
            self.client.flush()
        except Exception:
            log.exception("the traces could not be sent to Langfuse")

    def flush_soon(self) -> threading.Thread:
        """`flush` in a thread of its own, started: a stream closed on the event loop never
        blocks it."""
        thread = threading.Thread(target=self.flush, name="langfuse-flush", daemon=True)
        thread.start()
        return thread

    def flush_within(self, seconds: float) -> None:
        """`flush`, waiting at most `seconds` for it (#510): a slow or down Langfuse holds a run
        no longer, and the send goes on in the background, logged."""
        sending = self.flush_soon()
        sending.join(seconds)
        if sending.is_alive():
            log.warning(
                "the trace wasn't sent to Langfuse within %.1f s; it goes on in the background",
                seconds,
            )

    def shutdown(self) -> None:
        """Send what the client holds and stop it, when the app or the eval runner ends; a
        failure is logged, never the caller's."""
        try:
            self.client.shutdown()
        except Exception:
            log.exception("the Langfuse client could not be shut down")


def cost_details(response: LLMResult, model: str | None) -> dict[str, float] | None:
    """One model call's cost as Langfuse's `cost_details` (US dollars): the cost Vouch records
    for it (vouch.spending.UsageMeter: OpenRouter's reported cost, else the estimate from
    PRICES; the fake priced as DEFAULT_MODEL), its total split into input and output in the
    proportion of the model's list prices; the total alone for a model PRICES doesn't list.
    None when the cost can't be known, so nothing counts as $0."""
    # Imported here: vouch.spending imports vouch.model_choice, which imports this module.
    from vouch.spending import UsageMeter

    meter = UsageMeter(model)
    meter.on_llm_end(response)
    return _metered_cost(meter.usage(), model)


def error_cost_details(
    error: BaseException, response: LLMResult | None, model: str | None
) -> dict[str, float] | None:
    """A failed model call's cost as `cost_details` gives a call's (#508): the cost Vouch
    records for it (UsageMeter.on_llm_error: what its error's body carries); None when the
    error carries none, as the call may still have been billed."""
    from vouch.spending import UsageMeter

    meter = UsageMeter(model)
    meter.on_llm_error(error, response=response)
    return _metered_cost(meter.usage(), model)


def _metered_cost(usage: "Usage", model: str | None) -> dict[str, float] | None:
    """One call's usage, as its UsageMeter recorded it, as `cost_details`."""
    from vouch.model_choice import DEFAULT_MODEL
    from vouch.spending import PRICES

    if not usage.cost_complete:
        return None
    total = usage.cost_usd
    price = PRICES.get(model or DEFAULT_MODEL)
    listed = price.cost(usage.input_tokens, usage.output_tokens) if price else 0.0
    if price is None or listed <= 0:
        return {"total": total}
    share = price.cost(usage.input_tokens, 0) / listed
    return {"input": total * share, "output": total * (1 - share), "total": total}


def _generation_model(response: LLMResult, model: str | None) -> str:
    """The model a generation names: the one OpenRouter's answer says served it, read as the
    usage meter reads it, else the one asked for, else the fake's tag."""
    from vouch.spending import _served_model

    return _served_model(response) or model or FAKE_MODEL_TAG


@cache
def _costing_handler() -> type[Any]:
    """Langfuse's LangChain handler, each generation sent with its cost and model (#504).

    The handler sends a call's tokens but never its cost: Langfuse then infers one only for a
    model in its own price table, and OpenRouter's Claude ids (`anthropic/claude-haiku-5.5`)
    match none of it (`claude-haiku-5-5`). Langfuse takes a cost sent with a generation over
    the one it infers. Made on first use, as Langfuse is imported only with tracing on."""
    from langfuse import LangfuseGeneration
    from langfuse.langchain import CallbackHandler

    class CostingCallbackHandler(CallbackHandler):  # type: ignore[misc]
        def __init__(self, *, public_key: str, model: str | None) -> None:
            super().__init__(public_key=public_key)
            self.vouch_model = model

        def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> Any:
            # The generation is still open here (`_runs`, the handler's own open runs); its
            # on_llm_end ends it. A failure loses the cost, never the generation.
            try:
                generation = self._runs.get(run_id)
                if isinstance(generation, LangfuseGeneration):
                    generation.update(
                        model=_generation_model(response, self.vouch_model),
                        cost_details=cost_details(response, self.vouch_model),
                    )
            except Exception:
                log.exception("a model call's cost couldn't be added to its trace")
            return super().on_llm_end(response, run_id=run_id, **kwargs)

        def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> Any:
            # Langfuse's own error path (5.2.0's `on_llm_error`, through `generation.update`;
            # the #508 tests go red if a later version ends it another way) ends the generation
            # with `cost_details={"total": 0}`,
            # though the call may have been billed (#508). The generation's update is made to
            # send the cost Vouch records for the call instead: none at all when it's unknown,
            # including when it can't be worked out.
            cost = None
            try:
                cost = error_cost_details(error, kwargs.get("response"), self.vouch_model)
            except Exception:
                log.exception("a failed model call's cost couldn't be added to its trace")
            generation = self._runs.get(run_id)
            if isinstance(generation, LangfuseGeneration):
                generation.update = _forcing_cost(generation.update, cost)  # type: ignore[method-assign]
            return super().on_llm_error(error, run_id=run_id, **kwargs)

    return CostingCallbackHandler


def _forcing_cost(update: Callable[..., Any], cost: dict[str, float] | None) -> Callable[..., Any]:
    """A generation's `update`, its `cost_details` always `cost` (None: none sent)."""

    def costed(**fields: Any) -> Any:
        return update(**fields | {"cost_details": cost})

    return costed


def start_tracing(keys: LangfuseKeys | None) -> Tracing | None:
    """Tracing to the keys' Langfuse project; None, and no client made, without keys."""
    return Tracing(keys) if keys is not None else None


@contextmanager
def traced(
    tracing: Tracing | None, facts: TraceFacts, *, wait: bool = True
) -> Iterator[RunnableConfig]:
    """The run config for one traced run (empty with tracing off, or when its handler can't be
    made: logged, and the run goes on untraced), its trace sent as the run ends (`send_trace`).
    With `wait` False it's sent in the background only: a run inside another traced run's
    request (an assessment the chat starts), whose own bounded send carries it."""
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
        if wait:
            send_trace(tracing)
        else:
            tracing.flush_soon()


TRACE_SEND_WAIT_SECONDS = 3.0
"""The longest a run's response waits for its trace to reach Langfuse (#510)."""


def send_trace(tracing: Tracing) -> None:
    """Send a run's trace before its response ends, waiting at most TRACE_SEND_WAIT_SECONDS
    (#510). On Cloud Run a request has CPU only until its response is done; a send left to the
    background after it waits for the next request, or is lost when the instance stops. A stream
    the client leaves is closed when it's collected, which may be on the event loop: there it is
    sent in the background, so no other request waits (as api.record_off_the_event_loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:  # no loop in this thread: the threadpool, a worker, or the CLI
        tracing.flush_within(TRACE_SEND_WAIT_SECONDS)
        return
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
