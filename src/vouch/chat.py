"""Vouch's chat (#44): the page the lead is on, and the reply streamed with secrets removed.

The agent itself, its prompt and its tools are vouch.chat_agent (#45).

The reply is streamed as it is written, a few words behind the model (security layer 4,
vouch.redaction): a secret can span several words ("Authorization: Bearer <token>", or a key
with spaces in it), so text is sent only once no redaction could reach across into what is still
to come, and it is never redacted again.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from vouch.isa import IsaControl
from vouch.redaction import match_words, redact

CHAT_PROMPT_VERSION = "chat-3"
"""The chat agent's prompt version, beside the assessments' (vouch.graph.PROMPT_VERSION): on the
developer page, and in the log line with each chat message's usage."""
CHAT_FAILED = "Vouch could not answer just now. Please try again shortly."
"""Must match NOT_ANSWERED in frontend/src/chat.ts: the one sentence for a chat that failed."""

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


_WORD = re.compile(r"\S+")


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
