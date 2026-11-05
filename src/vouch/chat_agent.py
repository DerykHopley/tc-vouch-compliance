"""The chat agent (#45): a ReAct agent over two tools, its chat threads kept in the checkpointer.

`progress` reads the lead's progress from the store (vouch.memory) and says where they stand
and what to do next, in the Dossier's order (vouch.dossier); a chat opens with it (the
greeting). `start_assessment` runs the assessment graph (vouch.runs) and streams its steps into
the chat. The two agent layers stay apart: the chat agent never writes or reviews a verdict; the
lead signs off on the working paper.

Each turn's events, as the chat stream sends them: a `token` per piece of the reply, a `step`
per assessment step (or a guard step, when something was removed), a `suggestion` (the next
step, from `progress`), an `assessment` when one has finished. All are redacted (security
layer 4).
"""

import logging
import queue
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from vouch.allow_list import GUARD
from vouch.chat import PROGRESS, START_ASSESSMENT, RedactedText
from vouch.dossier import CardStatus, NextStep, overview
from vouch.graph import Assessment, new_thread_id, thread_key
from vouch.isa import IsaControl, UnknownControlError, load_base_framework, load_isa_control
from vouch.memory import Memory, progress
from vouch.model_choice import ModelChoice
from vouch.profile import ComplianceProfile
from vouch.redaction import redact_data, removal_step
from vouch.runs import RUNNING_ALREADY, RunningAssessments, assessment_events
from vouch.spending import DailyLimitReached, NoSharedCounter, SpendingGuard, Usage, UsageMeter

log = logging.getLogger(__name__)

ASSESSMENT_FAILED = "The assessment could not be completed. Please try again later."
"""As vouch.api's FAILED: the one sentence for an assessment that failed."""
RECURSION_LIMIT = 12
"""At most this many agent and tool steps in one turn; the chat model's own call budget
(vouch.model_choice.CALL_BUDGET) stops it sooner on OpenRouter."""
CHAT_THREAD = "chat:"
"""A chat thread's key in the checkpointer starts with it, after the user's scope, so it never
names an assessment's thread."""

PROGRESS_TOOL = {
    "type": "function",
    "function": {
        "name": PROGRESS,
        "description": (
            "Where the compliance lead stands: how many controls are accepted, rejected, "
            "proposed and not assessed, when they last worked, and the next step."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}
START_ASSESSMENT_TOOL = {
    "type": "function",
    "function": {
        "name": START_ASSESSMENT,
        "description": (
            "Assess one VDA ISA control against the company's evidence. Its steps are shown to "
            "the compliance lead as it runs; it ends with a proposed verdict, waiting for the "
            "lead's review on the control's working paper."
        ),
        "parameters": {
            "type": "object",
            "properties": {"control_id": {"type": "string", "description": "e.g. 5.1.1"}},
            "required": ["control_id"],
        },
    },
}

Write = Callable[[tuple[str, dict[str, Any]]], None]
"""Sends an event (name, data) into the chat stream as a tool runs."""


def progress_words(
    by_status: dict[CardStatus, int], last_worked: datetime | None, next_step: NextStep | None
) -> str:
    """Where the lead stands, in plain sentences: when they last worked, how many controls are
    in each review state, and the next step."""
    total = sum(by_status.values())
    if by_status["not-assessed"] == total:
        said = [f"You have not assessed any of the {total} controls yet."]
    else:
        said = []
        if last_worked is not None:
            day = last_worked.astimezone(UTC).date()
            said.append(f"You last worked on {day.day} {day:%B %Y}.")
        counts = [
            f"{n} {'is' if n == 1 else 'are'} {words}"
            for status, words in _STATUS_WORDS
            if (n := by_status[status])
        ]
        listed = ", ".join(counts[:-1]) + ", and " + counts[-1] if len(counts) > 1 else counts[0]
        said.append(f"Of the {total} controls, {listed}.")
    said.append(_next_words(next_step))
    return " ".join(said)


_STATUS_WORDS: tuple[tuple[CardStatus, str], ...] = (
    ("accepted", "accepted"),
    ("rejected", "rejected"),
    ("waiting-for-review", "proposed and waiting for your review"),
    ("not-assessed", "not assessed"),
)


def _next_words(next_step: NextStep | None) -> str:
    if next_step is None:
        return "Every control is accepted."
    control_id = next_step["control_id"]
    return {
        "review": f"The next step is to review control {control_id} on its working paper.",
        "assess-again": f"The next step is to assess control {control_id} again.",
        "assess": f"The next step is to assess control {control_id}.",
    }[next_step["action"]]


def unknown_control(control_id: str) -> str:
    ids = ", ".join(control.id for control in load_base_framework().controls)
    return f"There is no VDA ISA control “{control_id}”. The controls are {ids}."


@dataclass
class ChatTools:
    """What the chat agent's tools need: the lead's memory, the saver their assessments are
    kept in, and what an assessment run needs (the model, the allow-list, the spending guard,
    the running runs). `record` keeps an assessment's usage under its control."""

    profile: ComplianceProfile
    memory: Memory
    saver: BaseCheckpointSaver
    models: ModelChoice
    chosen: str | None
    allowed: frozenset[str]
    spending: SpendingGuard
    running: RunningAssessments
    record: Callable[[str, Usage], None]

    def call(self, name: str, args: dict[str, Any], write: Write) -> str:
        """Run the tool the model called; what it says back to the model. A tool the chat
        doesn't have is refused, with a guard step."""
        if name == PROGRESS:
            return self.progress(write)
        if name == START_ASSESSMENT and isinstance(args.get("control_id"), str):
            return self.start_assessment(args["control_id"].strip(), write)
        refused = f"Refused a call to {name}: it isn't one of the chat's tools."
        write(("step", {"node": GUARD, "text": refused}))
        return refused

    def progress(self, write: Write) -> str:
        """Where the lead stands, and the next step as a suggestion."""
        now = progress(self.memory, self.saver)
        view = overview(self.profile, load_base_framework().controls, now.controls)
        if view["next_step"] is not None:
            write(("suggestion", dict(view["next_step"])))
        return progress_words(view["by_status"], now.last_worked, view["next_step"])

    def start_assessment(self, control_id: str, write: Write) -> str:
        """Assess the control as the assessment stream would, its steps written into the chat;
        declined for an unknown control, while another of the lead's runs is going, or at the
        daily limit."""
        try:
            control = load_isa_control(control_id)
        except UnknownControlError:
            return unknown_control(control_id)
        scope = self.memory.scope
        if not self.running.try_hold(scope):
            return RUNNING_ALREADY
        try:
            thread_id = new_thread_id()
            try:
                self.memory.remember_assessment(thread_id, control.id)
            except Exception:
                log.exception("a chat's assessment of %s could not be remembered", control.id)
                return ASSESSMENT_FAILED
            refusal = self._count(control.id)
            if refusal is not None:
                self._forget(thread_id)
                return refusal
            assessment = self._run(control, thread_id, write)
        finally:
            self.running.release(scope)
        if assessment is None:
            return ASSESSMENT_FAILED
        write(
            (
                "assessment",
                {
                    "control_id": assessment.control_id,
                    "verdict": assessment.verdict,
                    "status": assessment.status,
                    "thread_id": assessment.thread_id,
                },
            )
        )
        verdict = assessment.verdict.replace("-", " ")
        return (
            f"The assessment of control {assessment.control_id} has finished: {verdict}. The "
            "verdict is proposed and waits for your review on the control's working paper."
        )

    def _count(self, control_id: str) -> str | None:
        """Count the assessment against the lead's daily limit; what to say when it can't be."""
        try:
            self.spending.start(self.memory.user_id, paid=self.chosen is not None)
        except DailyLimitReached as error:
            return error.message()
        except NoSharedCounter as error:
            return str(error)
        except Exception:
            log.exception("a chat's assessment of %s could not be counted", control_id)
            return ASSESSMENT_FAILED
        return None

    def _run(self, control: IsaControl, thread_id: str, write: Write) -> Assessment | None:
        """The run's steps written as they come; its Assessment, or None when it failed. Its
        usage is kept either way: the calls were made."""
        meter = UsageMeter(self.chosen)
        assessment = None
        try:
            events = _in_own_thread(
                lambda: assessment_events(
                    control,
                    self.profile,
                    self.models,
                    self.chosen,
                    self.allowed,
                    self.saver,
                    meter,
                    thread_id=thread_id,
                    scope=self.memory.scope,
                )
            )
            for event, data in events:
                if event == "result":
                    assessment = data
                elif event == "step":
                    write(("step", data))
        except Exception:
            log.exception("a chat's assessment of %s failed", control.id)
            return None
        finally:
            self.record(control.id, meter.usage())
        return assessment

    def _forget(self, thread_id: str) -> None:
        try:
            self.memory.forget_assessment(thread_id)
        except Exception:  # left in the index, it's never listed: its run never started
            log.exception("a refused assessment stays in the index")


_END = object()


def _in_own_thread[T](make: Callable[[], Iterator[T]]) -> Iterator[T]:
    """The items `make()` gives, made in a thread of its own. The assessment graph then starts
    afresh, not as part of the chat's graph: it would otherwise take the chat's run config from
    the context it starts in (its recursion limit, which stops an assessment part way, and its
    callbacks and token stream)."""
    items: queue.SimpleQueue[tuple[bool, Any]] = queue.SimpleQueue()

    def work() -> None:
        try:
            for item in make():
                items.put((True, item))
        except Exception as error:  # noqa: BLE001 - handed to the reader, raised there
            items.put((False, error))
            return
        items.put((True, _END))

    threading.Thread(target=work, name="vouch-chat-assessment", daemon=True).start()
    while True:
        ok, item = items.get()
        if not ok:
            raise item
        if item is _END:
            return
        yield item


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def chat_graph(
    model: BaseChatModel,
    tools: ChatTools,
    prompt: SystemMessage,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    """The ReAct loop: the agent answers or calls tools, the tools run, the agent again. The
    prompt (the page the lead is on) is the request's; the thread keeps only the conversation."""
    bound = model.bind_tools([PROGRESS_TOOL, START_ASSESSMENT_TOOL])

    def agent(state: ChatState) -> dict[str, Any]:
        return {"messages": [bound.invoke([prompt, *_answered(state["messages"])])]}

    def run_tools(state: ChatState) -> dict[str, Any]:
        write = get_stream_writer()
        calls = state["messages"][-1].tool_calls  # type: ignore[union-attr]
        replies = [
            ToolMessage(
                tools.call(c["name"], c["args"], write), tool_call_id=c["id"], name=c["name"]
            )
            for c in calls
        ]
        return {"messages": replies}

    def next_node(state: ChatState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

    graph = StateGraph(ChatState)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", next_node, ["tools", END])
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)


def _answered(messages: list[AnyMessage]) -> list[AnyMessage]:
    """The thread without tool calls left unanswered (a turn broken off as its tool ran): a
    model refuses a conversation with those in it."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    return [
        m
        for m in messages
        if not (isinstance(m, AIMessage) and any(c["id"] not in answered for c in m.tool_calls))
    ]


def chat_turn(
    graph: CompiledStateGraph, message: str, thread_id: str, scope: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    """One turn on the lead's chat thread: the reply and the tools' events, redacted, in order.
    The reply's text not yet sent goes out before a tool's event, so nothing is reordered."""
    config = {
        "configurable": {"thread_id": thread_key(CHAT_THREAD + thread_id, scope)},
        "recursion_limit": RECURSION_LIMIT,
    }
    text = RedactedText()
    parts = graph.stream(
        {"messages": [HumanMessage(message)]},
        config,
        stream_mode=["messages", "custom"],
        version="v2",
    )
    for part in parts:
        if part["type"] == "messages":
            chunk, meta = part["data"]
            if isinstance(chunk, AIMessageChunk) and meta.get("langgraph_node") == "agent":
                yield from _tokens(text.add(chunk.text))
        elif part["type"] == "custom":
            yield from _tokens(text.flush())
            event, data = part["data"]
            yield from _redacted(event, data)
    yield from _tokens(text.flush())


def _tokens(pieces: Iterator[tuple[str, list[str]]]) -> Iterator[tuple[str, dict[str, Any]]]:
    for piece, removed in pieces:
        if removed:
            yield "step", {"node": GUARD, "text": removal_step(removed, "reply")}
        if piece:
            yield "token", {"text": piece}


def _redacted(event: str, data: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    clean, removed = redact_data(data)
    if removed:
        yield "step", {"node": GUARD, "text": removal_step(removed, event)}
    yield event, clean
