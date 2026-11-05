"""Vouch's chat (#44): the page the lead is on, and the reply streamed with secrets removed.

The agent itself, its prompt and its tools are vouch.chat_agent (#45).

The reply is streamed as it is written, a few words behind the model (security layer 4,
vouch.redaction): a secret can span several words ("Authorization: Bearer <token>", or a key
with spaces in it), so text is sent only once no redaction could reach across into what is still
to come, and it is never redacted again.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal

from vouch.citations import Citation, CitationClaim, DropReason
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


CITATION = re.compile(r"“([^“”]+)”\s*\[([^\[\]\s]+)\]")
"""A citation in a chat answer, as the chat prompt asks for one: “the exact quote” [doc_id]."""
_CITATION_TAIL = re.compile(r"\s*(\[[^\[\]\s]*)?")
"""What may still follow a closed quote while its [doc_id] is being written."""
MAX_QUOTE_CHARS = 2000
"""A quote still open after this many characters is passed on as text: not a citation."""
QUOTE_REMOVED = "[quote removed]"
"""What a dropped citation leaves in the answer, so the sentence it was in still reads as cut."""

CheckQuote = Callable[[CitationClaim], Citation | DropReason]


def dropped_step(doc_id: str, reason: DropReason) -> str:
    """The guard step's sentence for a quote in a chat answer that the quote check dropped."""
    return f"Dropped a quote from {doc_id}: {reason}."


def check_citations(text: str, check: CheckQuote) -> tuple[str, list[str]]:
    """The text with each citation the quote check drops replaced by QUOTE_REMOVED, and a
    guard step's sentence for each one dropped. A citation that passes is left as written."""
    steps: list[str] = []

    def checked(match: re.Match[str]) -> str:
        quote, doc_id = match.group(1), match.group(2)
        result = check(CitationClaim(doc_id=doc_id, quote=quote))
        if isinstance(result, Citation):
            return match.group(0)
        steps.append(dropped_step(doc_id, result))
        return QUOTE_REMOVED

    return CITATION.sub(checked, text), steps


class CheckedCitations:
    """Passes a reply on as it is written, each citation in it checked once whole: text from an
    opening “ is held until it is a complete citation (checked, then sent or replaced), or
    can no longer become one (sent as it is). `flush` sends what is held, at the end of a reply
    or before something else is sent."""

    def __init__(self, check: CheckQuote) -> None:
        self._check = check
        self._pending = ""

    def add(self, chunk: str) -> Iterator[tuple[str, list[str]]]:
        self._pending += chunk
        yield from self._release(final=False)

    def flush(self) -> Iterator[tuple[str, list[str]]]:
        yield from self._release(final=True)

    def _release(self, *, final: bool) -> Iterator[tuple[str, list[str]]]:
        sent: list[str] = []
        steps: list[str] = []
        rest = self._pending
        while rest:
            start = rest.find("“")
            if start < 0:
                sent.append(rest)
                rest = ""
                break
            sent.append(rest[:start])
            rest = rest[start:]
            match = CITATION.match(rest)
            if match:
                text, dropped = check_citations(match.group(0), self._check)
                sent.append(text)
                steps += dropped
                rest = rest[match.end() :]
            elif not final and _may_become_citation(rest):
                break
            else:  # not a citation: the “ is text
                sent.append(rest[0])
                rest = rest[1:]
        self._pending = rest
        text = "".join(sent)
        if text or steps:
            yield text, steps


def _may_become_citation(text: str) -> bool:
    """Whether `text`, starting at a “, could still be completed into a citation."""
    if len(text) > MAX_QUOTE_CHARS:
        return False
    close = text.find("”")
    if "“" in text[1 : close if close >= 0 else None]:  # another quote opens first
        return False
    return close < 0 or _CITATION_TAIL.fullmatch(text[close + 1 :]) is not None
