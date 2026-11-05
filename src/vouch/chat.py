"""Vouch's chat (#44): the chat agent's prompt, and its reply streamed with secrets removed.

For now the agent has no tools (#45 and #46 add them): it answers the lead's message in Vouch's
formal voice, in the compliance profile's answer language, knowing which page and control the
lead is on. Each message is answered on its own; the chat keeps no history yet.

The reply is streamed as it is written, but never a part of a word: a secret or an address is
one word, and some (a bearer token) take two, so each time a word is complete the whole reply
so far is redacted (security layer 4, vouch.redaction) and only what is new is sent.
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from vouch.isa import IsaControl
from vouch.profile import ComplianceProfile
from vouch.redaction import redact

CHAT_PROMPT_VERSION = "chat-1"
CHAT_FAILED = "Vouch could not answer just now. Please try again shortly."

Page = Literal["dossier", "paper", "developer"]
"""The page the lead is on: the Dossier overview, a control's working paper, the developer page."""

_PAGE_NAMES: dict[Page, str] = {
    "dossier": "the Dossier overview (readiness, the 12 controls as cards, the evidence quotes)",
    "paper": "the working paper",
    "developer": "the developer page",
}


@dataclass(frozen=True)
class PageContext:
    """Where the lead is: the page, and the control when one is open."""

    page: Page
    control: IsaControl | None = None

    def describe(self) -> str:
        where = _PAGE_NAMES[self.page]
        if self.control is None:
            return where
        return f"{where} of VDA ISA control {self.control.id}: “{self.control.question}”"


def chat_messages(
    message: str, profile: ComplianceProfile, context: PageContext
) -> list[BaseMessage]:
    """The chat agent's prompt and the lead's message."""
    system = (
        f"You are Vouch, an assistant helping the compliance lead of {profile.company} prepare "
        "for a TISAX assessment against VDA ISA. Write in a formal register, as an experienced "
        "auditor would: complete sentences, no slang, no emoji, no exclamation marks. Answer in "
        f"the language with ISO 639-1 code '{profile.answer_language}', whatever language the "
        "lead writes in. Never say that the company is compliant, certified, or has passed an "
        "audit: you prepare for the audit, you do not decide it. You cannot yet read the "
        "evidence or start an assessment from the chat; when asked to, say so plainly and point "
        "the lead to the control's working paper, where an assessment starts. "
        f"The lead is on {context.describe()}."
    )
    return [SystemMessage(system), HumanMessage(message)]


_LAST_WORD_END = re.compile(r".*\s", re.DOTALL)


def streamed_reply(
    model: BaseChatModel, messages: list[BaseMessage]
) -> Iterator[tuple[str, list[str]]]:
    """The model's reply in pieces as it streams, each redacted, with the labels of what was
    newly removed from it (vouch.redaction). Only whole words are sent until the reply ends."""
    return _redacted_pieces(str(chunk.content) for chunk in model.stream(messages))


def _redacted_pieces(chunks: Iterable[str]) -> Iterator[tuple[str, list[str]]]:
    written = ""
    sent = ""  # the redacted reply sent so far
    found: list[str] = []
    for chunk in chunks:
        written += chunk
        complete = _LAST_WORD_END.match(written)
        if complete:
            yield from _new_part(complete.group(), sent, found)
            sent = redact(complete.group())[0]
    yield from _new_part(written, sent, found)


def _new_part(text: str, sent: str, found: list[str]) -> Iterator[tuple[str, list[str]]]:
    """What the redacted `text` adds to `sent`, and the labels not removed before. Redaction
    replaces only a secret's own words, so what was sent stays a prefix of the redacted text."""
    clean, labels = redact(text)
    new_labels = [label for label in labels if label not in found]
    found += new_labels
    if not clean.startswith(sent):  # never expected: rather send nothing than a wrong reply
        raise RuntimeError("a redaction changed a part of the reply already sent")
    if len(clean) > len(sent) or new_labels:
        yield clean[len(sent) :], new_labels
