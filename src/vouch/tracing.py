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

What leaves for Langfuse is masked as it is exported (`mask_spans`): every attribute through
vouch.redaction, as the API's events and log lines are, so no key, token, password or e-mail
address reaches a trace. Headers and Clerk session tokens never enter a run.
"""

import json
import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.runnables.config import ensure_config, get_callback_manager_for_config

from vouch.settings import LangfuseKeys

if TYPE_CHECKING:  # imported when tracing is on: with no keys, Langfuse is never imported
    from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter

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
    what would be sent, masked; with neither, the client sends to `keys.base_url`."""

    def __init__(
        self,
        keys: LangfuseKeys,
        *,
        span_exporter: "SpanExporter | None" = None,
        tracer_provider: "TracerProvider | None" = None,
    ) -> None:
        from langfuse import Langfuse

        self.public_key = keys.public_key
        self.client = Langfuse(
            public_key=keys.public_key,
            secret_key=keys.secret_key.get_secret_value(),
            base_url=keys.base_url,
            mask_otel_spans=mask_spans,
            span_exporter=span_exporter,
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
        """Send what the client holds now; a failure is logged, never the run's."""
        try:
            self.client.flush()
        except Exception:
            log.exception("the traces could not be sent to Langfuse")

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
    """The run config for one traced run (empty with tracing off), its trace sent when the run
    ends: on Cloud Run the instance may have no CPU between requests, so it isn't left to the
    client's own schedule."""
    if tracing is None:
        yield {}
        return
    try:
        yield tracing.config(facts)
    finally:
        tracing.flush()


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
        items = [_clean(item) for item in value]
        return tuple(item for item, _ in items), any(changed for _, changed in items)
    return value, False


def mask_spans(*, params: "MaskOtelSpansParams") -> "MaskOtelSpansResult":
    """Langfuse's export-stage mask: every span attribute that holds a secret or personal data
    replaced by its redacted value. Should it raise, Langfuse drops the batch: nothing unmasked
    is ever sent."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span in params.spans.items():
        changed = {}
        for key, value in span.attributes.items():
            clean, removed = _clean(value)
            if removed:
                changed[key] = clean
        if changed:
            patches[identifier] = OtelSpanPatch(set_attributes=changed)
    return MaskOtelSpansResult(span_patches=patches)
