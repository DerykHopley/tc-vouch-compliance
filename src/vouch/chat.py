"""Vouch's chat (#44): the chat agent's prompt, and its reply streamed with secrets removed.

The agent (vouch.chat_agent, #45) answers the compliance lead in Vouch's formal voice, in the
compliance profile's answer language, knowing which page and control the compliance lead is on.
Its tools read the lead's progress and start assessments; it never signs off a verdict.

The reply is streamed as it is written, a few words behind the model (security layer 4,
vouch.redaction): a secret can span several words ("Authorization: Bearer <token>", or a key
with spaces in it), so text is sent only once no redaction could reach across into what is still
to come, and it is never redacted again.
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from vouch.isa import IsaControl
from vouch.profile import ComplianceProfile
from vouch.redaction import match_words, redact

CHAT_PROMPT_VERSION = "chat-2"
"""The chat agent's prompt version, beside the assessments' (vouch.graph.PROMPT_VERSION): on the
developer page, and in the log line with each chat message's usage."""
CHAT_FAILED = "Vouch could not answer just now. Please try again shortly."
"""Must match NOT_ANSWERED in frontend/src/chat.ts: the one sentence for a chat that failed."""

PROGRESS = "progress"
START_ASSESSMENT = "start_assessment"
GREETING_REQUEST = "I have just opened the chat. Please tell me where I stand and what to do next."
"""The message a new chat starts with (#45): the lead asks where they stand, and the agent
answers from its `progress` tool. It is kept in the chat thread; the chat doesn't show it."""

Page = Literal["dossier", "paper", "developer"]
"""The page the compliance lead is on: the Dossier overview, a control's working paper, the
developer page."""

_PAGE_NAMES: dict[Page, str] = {
    "dossier": "the Dossier overview (readiness, the 12 controls as cards, the evidence quotes)",
    "paper": "the working paper",
    "developer": "the developer page",
}


@dataclass(frozen=True)
class PageContext:
    """Where the compliance lead is: the page, and the control when one is open."""

    page: Page
    control: IsaControl | None = None

    def describe(self) -> str:
        where = _PAGE_NAMES[self.page]
        if self.control is None:
            return where
        return f"{where} of VDA ISA control {self.control.id}: “{self.control.question}”"


def chat_prompt(profile: ComplianceProfile, context: PageContext) -> SystemMessage:
    """The chat agent's system prompt: its voice, its tools, and where the lead is."""
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


def chat_messages(
    message: str, profile: ComplianceProfile, context: PageContext
) -> list[BaseMessage]:
    """The chat agent's prompt and the compliance lead's message."""
    return [chat_prompt(profile, context), HumanMessage(message)]


_WORD = re.compile(r"\S+")


def streamed_reply(
    model: BaseChatModel, messages: list[BaseMessage]
) -> Iterator[tuple[str, list[str]]]:
    """The model's reply in pieces as it streams, each redacted, with the labels of what was
    newly removed from it (vouch.redaction)."""
    return _redacted_pieces(str(chunk.content) for chunk in model.stream(messages))


def _redacted_pieces(chunks: Iterable[str]) -> Iterator[tuple[str, list[str]]]:
    """The chunks as RedactedText sends them, then what it holds at the end."""
    text = RedactedText()
    for chunk in chunks:
        yield from text.add(chunk)
    yield from text.flush()


class RedactedText:
    """Sends a reply a few words behind the model: only text before a safe cut, redacted on its
    own, and only the text not yet sent is ever redacted again (linear in the reply). `flush`
    sends the rest, at the end of a reply or before something else is sent (a tool's step).
    Each label removed is reported once, the first time."""

    def __init__(self) -> None:
        self._span = match_words()
        self._pending = ""  # written, not yet sent
        self._found: list[str] = []

    def add(self, chunk: str) -> Iterator[tuple[str, list[str]]]:
        self._pending += chunk
        cut = _safe_cut(self._pending, self._span)
        if cut:
            clean, labels = redact(self._pending[:cut])
            self._pending = self._pending[cut:]
            yield clean, _new(labels, self._found)

    def flush(self) -> Iterator[tuple[str, list[str]]]:
        if self._pending:
            clean, labels = redact(self._pending)
            self._pending = ""
            yield clean, _new(labels, self._found)


def _safe_cut(pending: str, span: int) -> int:
    """A place to cut `pending` that no redaction can cross, now or once more text comes; 0 for
    none yet. A cut at a word's start is safe when a match crossing it would have to span more
    than `span` words to reach text still to be written, and when the text redacted whole
    equals its two sides redacted apart (no complete match crosses it)."""
    starts = [word.start() for word in _WORD.finditer(pending)]
    # The first word still to be completed: the last one, unless whitespace ends the text.
    unfinished = len(starts) - 1 if pending and not pending[-1].isspace() else len(starts)
    whole = None
    for k in range(unfinished + 1 - span, 0, -1):  # latest first
        whole = whole if whole is not None else redact(pending)[0]
        cut = starts[k]
        if redact(pending[:cut])[0] + redact(pending[cut:])[0] == whole:
            return cut
    return 0


def _new(labels: list[str], found: list[str]) -> list[str]:
    """The labels not removed before, now noted as found."""
    new = [label for label in labels if label not in found]
    found += new
    return new
