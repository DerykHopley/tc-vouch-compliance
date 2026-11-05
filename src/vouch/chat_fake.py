"""The chat's fake model (#44): a canned formal answer, streamed, never touching the network.

Offline, and in tests, the chat runs on it (vouch.model_choice.ModelChoice.for_chat). Given the
chat agent's tools (#45, #46), it calls one as a model would: `start_assessment` for "Assess
<id>", `progress` for the greeting or "where do I stand", `explain_control` for "What's missing
for <id>?" (the page's control when none is named) or with `sign_off` for "Accept <id>", and
`search_evidence` for "Is there evidence of <words>?". Then it passes the tool's words on as its
answer, each passage of evidence cited as the chat prompt asks:
<cite doc="doc_id">its first line</cite>.
"""

import json
import re
from collections.abc import Iterator
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ConfigDict, Field

from vouch.chat_agent import (
    EXPLAIN_CONTROL,
    GREETING_REQUEST,
    PROGRESS,
    SEARCH_EVIDENCE,
    START_ASSESSMENT,
)
from vouch.fake_model import _usage

CHAT_REPLY = (
    "Thank you for your question. Please ask what is missing for a control, or whether there "
    "is evidence of something in your documents."
)
"""The chat's fake answer: formal, in English, whatever else is asked."""
_ID = r"(\d+(?:\.\d+)+)"
ASSESS = re.compile(r"\bassess\s+(?:control\s+)?(\S+)", re.IGNORECASE)
WHERE = re.compile(r"where do I stand|my progress", re.IGNORECASE)
SIGN_OFF = re.compile(rf"\b(accept|reject|override|approve|sign off)\b\D*{_ID}", re.IGNORECASE)
MISSING = re.compile(rf"\b(?:missing|explain)\b(?:\D*{_ID})?", re.IGNORECASE)
EVIDENCE = re.compile(r"\bevidence (?:of|for|about|on) (.+?)[?.!]*$", re.IGNORECASE)
PAGE_CONTROL = re.compile(rf"VDA ISA control {_ID}:")
_EVIDENCE_DATA = re.compile(r"<evidence>\n(.*?)\n</evidence>", re.DOTALL)
_FINDINGS_DATA = re.compile(r"<findings>\n(.*?)\n</findings>", re.DOTALL)
_LIST_MARK = re.compile(r"^(?:[-*+]|\d+[.)])\s+")


def _words(text: str) -> list[str]:
    return re.findall(r"\s*\S+", text)


def _cited(tool_words: str) -> str:
    """A tool's words with its evidence data given as citations: each passage's first line,
    in a cite naming its document id."""

    def cite(found: re.Match[str]) -> str:
        lines = []
        for passage in json.loads(found.group(1)):
            if "text" not in passage:
                continue
            first = _LIST_MARK.sub("", passage["text"].splitlines()[0].strip())
            label = f"{passage['requirement']}: " if "requirement" in passage else ""
            lines.append(f'- {label}<cite doc="{passage["doc_id"]}">{first}</cite>')
        return "\n".join(lines)

    def findings(found: re.Match[str]) -> str:
        return "\n".join(
            f"- {f['requirement']} ({f['column']}): {f['text']} "
            f"{f['verdict'].capitalize()}: {f['reason']}"
            for f in json.loads(found.group(1))
        )

    return _FINDINGS_DATA.sub(findings, _EVIDENCE_DATA.sub(cite, tool_words))


def _page(messages: list[BaseMessage]) -> str | None:
    """The control of the page the lead is on, as the system prompt names it."""
    prompt = next((m for m in messages if isinstance(m, SystemMessage)), None)
    found = PAGE_CONTROL.search(str(prompt.content)) if prompt else None
    return found.group(1) if found else None


class CannedChatModel(BaseChatModel):
    """Answers every message with `reply`, streamed a word at a time (or as `chunks`, when
    given), with token counts estimated as the scripted fake's are, so the usage meter sees
    them. Each call's messages are kept in `prompts`; with `fail_with`, every call raises it,
    as a model that can't be reached would. With tools bound, it calls the one the lead's
    message asks for (or `call`, whatever is asked), and answers a tool's result with the
    result's words (or `after_tool`). With `blocks`, it writes its answer in content blocks, as
    some providers do."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    reply: str = CHAT_REPLY
    chunks: list[str] | None = None
    fail_with: Exception | None = None
    prompts: list[list[BaseMessage]] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)  # the names of the tools bound
    call: dict[str, Any] | None = None  # {"name", "args"}: the tool call it makes
    after_tool: str | None = None
    blocks: bool = False

    @property
    def _llm_type(self) -> str:
        return "canned-chat-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "CannedChatModel":
        """The same fake (its `prompts` shared), now able to call these tools."""
        names = [tool["function"]["name"] for tool in tools]
        return self.model_copy(update={"tools": names})

    def _record_prompt_then_answer(
        self, messages: list[BaseMessage]
    ) -> tuple[list[str], list[ToolCall]]:
        """Keeps the call's messages, then raises `fail_with` if set, else gives the answer's
        pieces in order, or the tool call the lead's last message asks for."""
        self.prompts.append(list(messages))
        if self.fail_with is not None:
            raise self.fail_with
        last = messages[-1] if messages else None
        if isinstance(last, ToolMessage):
            return _words(self.after_tool or _cited(str(last.content))), []
        call = self._tool_call(last, messages) if isinstance(last, HumanMessage) else None
        if call is not None:
            return [], [call]
        return (self.chunks if self.chunks is not None else _words(self.reply)), []

    def _tool_call(self, message: HumanMessage, messages: list[BaseMessage]) -> ToolCall | None:
        text = str(message.content)
        call_id = f"call-{len(self.prompts)}"

        def call(name: str, **args: str) -> ToolCall | None:
            return ToolCall(name=name, args=args, id=call_id) if name in self.tools else None

        if self.call is not None:
            return ToolCall(name=self.call["name"], args=self.call["args"], id=call_id)
        if asked := SIGN_OFF.search(text):
            verb = asked.group(1).lower()
            sign_off = verb if verb in ("reject", "override") else "accept"
            return call(EXPLAIN_CONTROL, control_id=asked.group(2), sign_off=sign_off)
        if asked := ASSESS.search(text):
            return call(START_ASSESSMENT, control_id=asked.group(1).rstrip(".?!,;:"))
        if text == GREETING_REQUEST or WHERE.search(text):
            return call(PROGRESS)
        if (asked := MISSING.search(text)) and (control_id := asked.group(1) or _page(messages)):
            return call(EXPLAIN_CONTROL, control_id=control_id)
        if asked := EVIDENCE.search(text):
            return call(SEARCH_EVIDENCE, query=asked.group(1))
        return None

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        pieces, calls = self._record_prompt_then_answer(messages)
        text = "".join(pieces)
        reply = AIMessage(text, tool_calls=calls, usage_metadata=_usage(messages, text))
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        pieces, calls = self._record_prompt_then_answer(messages)
        empty: str | list[Any] = [] if self.blocks else ""
        for piece in pieces:
            content = [{"type": "text", "text": piece, "index": 0}] if self.blocks else piece
            yield ChatGenerationChunk(message=AIMessageChunk(content=content))
        for index, call in enumerate(calls):
            chunk = {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"]}
            message = AIMessageChunk(content=empty, tool_call_chunks=[chunk | {"index": index}])
            yield ChatGenerationChunk(message=message)
        # The whole call's token counts, on a last empty chunk, as OpenAI-style streams give them.
        usage = _usage(messages, "".join(pieces))
        yield ChatGenerationChunk(message=AIMessageChunk(content=empty, usage_metadata=usage))
