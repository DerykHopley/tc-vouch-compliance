"""The chat's fake model (#44): a canned formal answer, streamed, never touching the network.

Offline, and in tests, the chat runs on it (vouch.model_choice.ModelChoice.for_chat).
"""

import re
from collections.abc import Iterator
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ConfigDict, Field

from vouch.fake_model import _usage

CHAT_REPLY = (
    "Thank you for your question. I cannot yet read your evidence or start an assessment from "
    "the chat; please open a control's working paper to assess it."
)
"""The chat's fake answer: formal, in English, whatever is asked."""


class CannedChatModel(BaseChatModel):
    """Answers every message with `reply`, streamed a word at a time (or as `chunks`, when
    given), with token counts estimated as the scripted fake's are, so the usage meter sees
    them. Each call's messages are kept in `prompts`; with `fail_with`, every call raises it,
    as a model that can't be reached would."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    reply: str = CHAT_REPLY
    chunks: list[str] | None = None
    fail_with: Exception | None = None
    prompts: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "canned-chat-fake"

    def _record_prompt_then_answer(self, messages: list[BaseMessage]) -> list[str]:
        """Keeps the call's messages, then raises `fail_with` if set, else gives the answer's
        pieces in order."""
        self.prompts.append(list(messages))
        if self.fail_with is not None:
            raise self.fail_with
        return self.chunks if self.chunks is not None else re.findall(r"\s*\S+", self.reply)

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        text = "".join(self._record_prompt_then_answer(messages))
        reply = AIMessage(text, usage_metadata=_usage(messages, text))
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        pieces = self._record_prompt_then_answer(messages)
        for piece in pieces:
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))
        # The whole call's token counts, on a last empty chunk, as OpenAI-style streams give them.
        usage = _usage(messages, "".join(pieces))
        yield ChatGenerationChunk(message=AIMessageChunk(content="", usage_metadata=usage))
