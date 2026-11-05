"""The chat's fake model (#44): a canned formal answer, streamed, never touching the network.

Offline, and in tests, the chat runs on it (vouch.model_choice.ModelChoice.for_chat). Given the
chat agent's tools (#45), it calls one as a model would: `start_assessment` for "Assess <id>",
`progress` for the greeting or "where do I stand"; then it passes the tool's words on as its
answer.
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
    ToolCall,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ConfigDict, Field

from vouch.chat import GREETING_REQUEST, PROGRESS, START_ASSESSMENT
from vouch.fake_model import _usage

CHAT_REPLY = (
    "Thank you for your question. I cannot yet read your evidence from the chat; please open a "
    "control's working paper to see its quotes."
)
"""The chat's fake answer: formal, in English, whatever else is asked."""
ASSESS = re.compile(r"\bassess\s+(?:control\s+)?(\S+)", re.IGNORECASE)
WHERE = re.compile(r"where do I stand|my progress", re.IGNORECASE)


def _words(text: str) -> list[str]:
    return re.findall(r"\s*\S+", text)


class CannedChatModel(BaseChatModel):
    """Answers every message with `reply`, streamed a word at a time (or as `chunks`, when
    given), with token counts estimated as the scripted fake's are, so the usage meter sees
    them. Each call's messages are kept in `prompts`; with `fail_with`, every call raises it,
    as a model that can't be reached would. With tools bound, it calls the one the lead's
    message asks for, and answers a tool's result with the result's words."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    reply: str = CHAT_REPLY
    chunks: list[str] | None = None
    fail_with: Exception | None = None
    prompts: list[list[BaseMessage]] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)  # the names of the tools bound

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
            return _words(str(last.content)), []
        call = self._tool_call(last) if isinstance(last, HumanMessage) else None
        if call is not None:
            return [], [call]
        return (self.chunks if self.chunks is not None else _words(self.reply)), []

    def _tool_call(self, message: HumanMessage) -> ToolCall | None:
        text = str(message.content)
        call_id = f"call-{len(self.prompts)}"
        if START_ASSESSMENT in self.tools and (asked := ASSESS.search(text)):
            control_id = asked.group(1).rstrip(".?!,;:")
            return ToolCall(name=START_ASSESSMENT, args={"control_id": control_id}, id=call_id)
        if PROGRESS in self.tools and (text == GREETING_REQUEST or WHERE.search(text)):
            return ToolCall(name=PROGRESS, args={}, id=call_id)
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
        for piece in pieces:
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))
        for index, call in enumerate(calls):
            chunk = {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"]}
            message = AIMessageChunk(content="", tool_call_chunks=[chunk | {"index": index}])
            yield ChatGenerationChunk(message=message)
        # The whole call's token counts, on a last empty chunk, as OpenAI-style streams give them.
        usage = _usage(messages, "".join(pieces))
        yield ChatGenerationChunk(message=AIMessageChunk(content="", usage_metadata=usage))
