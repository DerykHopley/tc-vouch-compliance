"""The chat agent (#45): a ReAct agent over two tools, its chat threads kept in the checkpointer.

`progress` reads the lead's progress from the store (vouch.memory) and says where they stand
and what to do next, in the Dossier's order (vouch.dossier); a chat opens with it (the
greeting). `start_assessment` runs the assessment graph (vouch.runs) and streams its steps into
the chat. The two agent layers stay apart: the chat agent never writes or reviews a verdict; the
lead signs off on the working paper.

Each turn's events, as the chat stream sends them: a `token` per piece of the reply, a `step`
per assessment step (or a guard step, when something was removed), a `suggestion` (the next
step, from `progress`), an `assessment` when one has finished. All are redacted (security
layer 4), and so is what the thread keeps: the lead's message, the replies, the tools' words.
"""

import logging
import queue
import threading
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from typing import Annotated, Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from vouch.chat import PageContext, RedactedText
from vouch.dossier import CardStatus, NextStep, overview
from vouch.graph import GUARD, Assessment, thread_key
from vouch.isa import IsaControl, UnknownControlError, load_base_framework, load_isa_control
from vouch.memory import Memory, progress
from vouch.profile import ComplianceProfile
from vouch.redaction import redact, redacted_event, removal_step
from vouch.runs import (
    ASSESSMENT_FAILED,
    RUNNING_ALREADY,
    AlreadyRunning,
    AssessmentRun,
    RunningAssessments,
    assessment_events,
    begin_run,
)
from vouch.spending import (
    DailyLimitReached,
    NoSharedCounter,
    SpendingGuard,
    Usage,
    count_assessment,
)

log = logging.getLogger(__name__)

RECURSION_LIMIT = 12
"""At most this many agent and tool steps in one turn; the chat model's own call budget
(vouch.model_choice.CALL_BUDGET) stops it sooner on OpenRouter."""
HISTORY_WINDOW = 20
"""How many of the thread's latest messages the model is sent with each call, beside the
prompt: the thread keeps them all, the model sees a window, starting at one of the lead's."""
STOP_POLL_SECONDS = 0.05
"""How often a tool waiting on an assessment looks whether its chat stream has been left."""
CHAT_THREAD = "chat:"
"""A chat thread's key in the checkpointer starts with it, after the user's scope, so it never
names an assessment's thread."""

PROGRESS = "progress"
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
START_ASSESSMENT = "start_assessment"
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
CHAT_TOOLS = [PROGRESS_TOOL, START_ASSESSMENT_TOOL]
GREETING_REQUEST = "I have just opened the chat. Please tell me where I stand and what to do next."
"""The message a new chat starts with: the lead asks where they stand, and the agent answers
from its `progress` tool. It is kept in the chat thread; the chat doesn't show it."""


def chat_prompt(profile: ComplianceProfile, context: PageContext) -> SystemMessage:
    """The chat agent's system prompt (vouch.chat.CHAT_PROMPT_VERSION): its voice, its tools,
    and where the lead is."""
    system = (
        f"You are Vouch, an assistant helping the compliance lead of {profile.company} prepare "
        "for a TISAX assessment against VDA ISA. Write in a formal register, as an experienced "
        "auditor would: complete sentences, no slang, no emoji, no exclamation marks. Answer in "
        f"the language with ISO 639-1 code '{profile.answer_language}', whatever language the "
        "compliance lead writes in. Never say that the company is compliant, certified, or has "
        "passed an audit: you prepare for the audit, you do not decide it. Use the "
        f"`{PROGRESS}` tool when asked where the compliance lead stands, and the "
        f"`{START_ASSESSMENT}` tool when asked to assess a control; pass on what a tool says in "
        "full, every control id it lists included, and give its message as it is when it "
        "declines. You never accept, reject or change a verdict: the compliance lead signs off "
        "on the control's working paper. You cannot yet read the evidence from the chat; when "
        "asked to, say so plainly. "
        f"The compliance lead is on {context.describe()}."
    )
    return SystemMessage(system)


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
    """What the chat agent's tools need: the lead's memory, what an assessment runs on (`run`),
    the spending guard and whose runs are going. `record` keeps an assessment's usage under its
    control."""

    profile: ComplianceProfile
    memory: Memory
    run: AssessmentRun
    spending: SpendingGuard
    running: RunningAssessments
    record: Callable[[str, Usage], None]

    def call(self, name: str, args: dict[str, Any], write: Write, stop: threading.Event) -> str:
        """Run the tool the model called; what it says back to the model. A tool the chat
        doesn't have, or a call without the arguments its tool needs, is refused with a guard
        step. `stop` is set once the chat stream has been left."""
        if name == PROGRESS:
            return self.progress(write)
        if name == START_ASSESSMENT and isinstance(args.get("control_id"), str):
            return self.start_assessment(args["control_id"].strip(), write, stop)
        if name == START_ASSESSMENT:
            refused = (
                f"Refused a call to {name}: its arguments are invalid. It needs a control_id, "
                "the control's number as text, such as 5.1.1."
            )
        else:
            refused = f"Refused a call to {name}: it isn't one of the chat's tools."
        write(("step", {"node": GUARD, "text": refused}))
        return refused

    def progress(self, write: Write) -> str:
        """Where the lead stands, and the next step as a suggestion."""
        lead_progress = progress(self.memory, self.run.saver)
        view = overview(self.profile, load_base_framework().controls, lead_progress.controls)
        if view["next_step"] is not None:
            write(("suggestion", dict(view["next_step"])))
        return progress_words(view["by_status"], lead_progress.last_worked, view["next_step"])

    def start_assessment(self, control_id: str, write: Write, stop: threading.Event) -> str:
        """Assess the control as the assessment stream would (vouch.runs.begin_run), its steps
        written into the chat; declined for an unknown control, while another of the lead's runs
        is going, or at the daily limit."""
        try:
            control = load_isa_control(control_id)
        except UnknownControlError:
            return unknown_control(control_id)
        run = self.run.metered_afresh()
        paid = run.chosen is not None
        try:
            thread_id = begin_run(
                self.running,
                self.memory,
                control.id,
                count=lambda: count_assessment(self.spending, self.memory.user_id, paid),
            )
        except Exception as refused:  # noqa: BLE001 - each refusal is said; others are logged
            return self._refusal_at_daily_limit(refused, control.id)
        assessment = self._run(control, run, thread_id, write, stop)
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

    def _refusal_at_daily_limit(self, refused: Exception, control_id: str) -> str:
        """What the chat says when an assessment can't start: another is running, the daily
        limit is reached, the counter can't count it, or something failed (logged)."""
        if isinstance(refused, AlreadyRunning):
            return RUNNING_ALREADY
        if isinstance(refused, DailyLimitReached):
            return refused.message()
        if isinstance(refused, NoSharedCounter):
            return str(refused)
        log.error("a chat's assessment of %s could not start", control_id, exc_info=refused)
        return ASSESSMENT_FAILED

    def _run(
        self,
        control: IsaControl,
        run: AssessmentRun,
        thread_id: str,
        write: Write,
        stop: threading.Event,
    ) -> Assessment | None:
        """The run's steps written as they come; its Assessment, or None when it failed or the
        chat was left. The run's usage is kept, and the lead's hold released, once its worker
        has really ended: the calls were made."""

        def ended() -> None:
            self.record(control.id, run.meter.usage())
            self.running.release(self.memory.scope)

        assessment = None
        try:
            events = _in_own_thread(
                lambda: assessment_events(
                    control, self.profile, run, self.memory, thread_id=thread_id
                ),
                stop=stop,
                ended=ended,
            )
            for event, data in events:
                if event == "result":
                    assessment = data
                elif event == "step":
                    write(("step", data))
        except Exception:
            log.exception("a chat's assessment of %s failed", control.id)
            return None
        return assessment


_END = object()


def _in_own_thread[T](
    make: Callable[[], Generator[T]], *, stop: threading.Event, ended: Callable[[], None]
) -> Iterator[T]:
    """The items `make()` gives, made in a thread of its own. The assessment graph then starts
    afresh, not as part of the chat's graph: it would otherwise take the chat's run config from
    the context it starts in (its recursion limit, which stops an assessment part way, and its
    callbacks and token stream).

    Once `stop` is set the reader returns at once, never waiting for the worker; the worker
    stops at its next step. `ended` runs in the worker when it has really ended, before the
    reader hears of the end."""
    items: queue.SimpleQueue[tuple[bool, Any]] = queue.SimpleQueue()

    def work() -> None:
        outcome: tuple[bool, Any] = (True, _END)
        made = make()
        try:
            for item in made:
                items.put((True, item))
                if stop.is_set():
                    break
        except Exception as error:  # noqa: BLE001 - handed to the reader, raised there
            outcome = (False, error)
        finally:
            made.close()
            ended()
        items.put(outcome)

    threading.Thread(target=work, name="vouch-chat-assessment", daemon=True).start()
    while not stop.is_set():
        try:
            ok, item = items.get(timeout=STOP_POLL_SECONDS)
        except queue.Empty:
            continue
        if not ok:
            raise item
        if item is _END:
            return
        yield item


@dataclass(frozen=True)
class ChatContext:
    """One request's chat, as the compiled graph's nodes see it (LangGraph's run context): the
    model with the chat's tools bound, the tools, the prompt, and `stop`, set when the stream
    is left."""

    model: Runnable[Any, BaseMessage]
    tools: ChatTools
    prompt: SystemMessage
    stop: threading.Event = field(default_factory=threading.Event)


def chat_context(model: BaseChatModel, tools: ChatTools, prompt: SystemMessage) -> ChatContext:
    return ChatContext(model.bind_tools(CHAT_TOOLS), tools, prompt)


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def _agent(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    chat = runtime.context
    reply = chat.model.invoke([chat.prompt, *_window(state["messages"])])
    if isinstance(reply.content, str):  # kept as sent: redacted
        reply = reply.model_copy(update={"content": redact(reply.content)[0]})
    return {"messages": [reply]}


def _tools(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    chat = runtime.context
    calls = state["messages"][-1].tool_calls  # type: ignore[union-attr]
    replies = [
        ToolMessage(
            redact(chat.tools.call(c["name"], c["args"], runtime.stream_writer, chat.stop))[0],
            tool_call_id=c["id"],
            name=c["name"],
        )
        for c in calls
    ]
    return {"messages": replies}


def _next_node(state: ChatState) -> str:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


@cache
def _compiled() -> CompiledStateGraph:
    graph = StateGraph(ChatState, context_schema=ChatContext)
    graph.add_node("agent", _agent)
    graph.add_node("tools", _tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _next_node, ["tools", END])
    graph.add_edge("tools", "agent")
    return graph.compile()


def chat_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    """The ReAct loop, compiled once: the agent answers or calls tools, the tools run, the agent
    again; the thread kept in `checkpointer`. The request's model, tools and prompt come in its
    ChatContext (chat_turn)."""
    return _compiled().copy(update={"checkpointer": checkpointer})


def _window(messages: list[AnyMessage]) -> list[AnyMessage]:
    """What the model is sent of the thread: at most HISTORY_WINDOW of its latest messages,
    from one of the lead's on (never a reply cut from its question), without tool calls left
    unanswered (a turn broken off as its tool ran: a model refuses those)."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    kept = [
        m
        for m in messages
        if not (isinstance(m, AIMessage) and any(c["id"] not in answered for c in m.tool_calls))
    ]
    leads = [i for i, m in enumerate(kept) if isinstance(m, HumanMessage)]
    start = next((i for i in leads if i >= len(kept) - HISTORY_WINDOW), leads[-1] if leads else 0)
    return kept[start:]


def chat_config(thread_id: str, scope: str) -> dict[str, Any]:
    """The checkpointer's config for the lead's chat thread."""
    return {
        "configurable": {"thread_id": thread_key(CHAT_THREAD + thread_id, scope)},
        "recursion_limit": RECURSION_LIMIT,
    }


def chat_turn(
    graph: CompiledStateGraph, chat: ChatContext, message: str, thread_id: str, scope: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    """One turn on the lead's chat thread: the reply and the tools' events, redacted, in order.
    The reply's text not yet sent goes out before a tool's event, so nothing is reordered. When
    the turn is left (the stream closed), `chat.stop` is set, so no tool waits on an assessment
    still running."""
    text = RedactedText()
    try:
        parts = graph.stream(
            {"messages": [HumanMessage(redact(message)[0])]},
            chat_config(thread_id, scope),
            context=chat,
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
                yield from redacted_event(event, data)
        yield from _tokens(text.flush())
    finally:
        chat.stop.set()


def _tokens(pieces: Iterator[tuple[str, list[str]]]) -> Iterator[tuple[str, dict[str, Any]]]:
    for piece, removed in pieces:
        if removed:
            yield "step", {"node": GUARD, "text": removal_step(removed, "reply")}
        if piece:
            yield "token", {"text": piece}
