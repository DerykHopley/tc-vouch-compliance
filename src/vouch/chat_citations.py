"""Quotes in a chat answer (#46), found as the reply streams and each checked whole.

A quotation is text between a pair of quotation marks the model may write: “…”, „…“ (or
„…”), "…", «…», »…«, ‚…‘ (or ‚…’). Followed by a document id in square brackets or
parentheses (“quote” [doc_id], spaces allowed inside), it is a citation, checked against that
document. Without one, a quotation of at least MIN_QUOTE_WORDS words is an uncited quote,
checked against what the tools returned. A shorter one is plain text ("the term “ISMS”").

The check decides (vouch.chat_agent, ChatTools.check_quote); this module only finds the
quotations, holds text back while one may still be being written, and replaces each dropped
one with QUOTE_REMOVED. A quotation cut off at the end of the reply is checked as an uncited
quote, never sent unchecked.
"""

import re
from collections.abc import Callable, Iterator

from vouch.citations import MIN_QUOTE_WORDS

QUOTE_REMOVED = "[quote removed]"
"""What a dropped quote leaves in the answer, so the sentence it was in still reads as cut."""
CLOSING = {"“": "”", "„": "“”", '"': '"', "«": "»", "»": "«", "‚": "‘’"}
"""Each opening quotation mark, and the marks that close it."""
MAX_QUOTE_CHARS = 1000
"""An opening mark left unclosed this long is a stray mark, passed on as text: no quotation
the check would accept is this long (one paragraph, list item or table row)."""
UNCITED = "it isn't in what Vouch found for this question"
"""Why an uncited quote is dropped (ChatTools.check_quote)."""

_OPENING = re.compile("[" + re.escape("".join(CLOSING)) + "]")
_DOC_ID = re.compile(r"\s*[\[(]\s*([\w.-]+)\s*[\])]")
_DOC_ID_SO_FAR = re.compile(r"\s*(?:[\[(]\s*[\w.-]*\s*)?")
"""What may follow a closed quotation while its document id is still being written."""

CheckQuote = Callable[[str, str | None], str | None]
"""Checks a quotation (its text, and the document id it cites, or None): None to keep it, or
why it is dropped."""


def dropped_step(doc_id: str | None, reason: str) -> str:
    """The guard step's sentence for a quote the check dropped from a chat answer."""
    where = f"from {doc_id}" if doc_id is not None else "citing no document"
    return f"Dropped a quote {where}: {reason}."


class CheckedCitations:
    """Passes a reply on as it is written, each quotation in it checked once, whole. Text from
    an opening mark is held until the quotation is complete (and its document id, if one
    follows), or can no longer be one. `flush` checks and sends what is held, at the end of a
    reply. Each piece comes with the guard steps for the quotes dropped from it."""

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
            found = _OPENING.search(rest)
            if found is None:
                sent.append(rest)
                rest = ""
                break
            sent.append(rest[: found.start()])
            rest = rest[found.start() :]
            quotation = _quotation(rest, final=final)
            if quotation is None:  # may still become one: hold it
                break
            if quotation is _NOT_A_QUOTATION:
                sent.append(rest[0])
                rest = rest[1:]
                continue
            end, quote, doc_id = quotation
            if doc_id is None and len(quote.split()) < MIN_QUOTE_WORDS:
                sent.append(rest[:end])  # a short quoted phrase: text
            elif (reason := self._check(quote.strip(), doc_id)) is None:
                sent.append(rest[:end])
            else:
                sent.append(QUOTE_REMOVED)
                steps.append(dropped_step(doc_id, reason))
            rest = rest[end:]
        self._pending = rest
        text = "".join(sent)
        if text or steps:
            yield text, steps


_NOT_A_QUOTATION = (0, "", None)


def _quotation(text: str, *, final: bool) -> tuple[int, str, str | None] | None:
    """The quotation `text` starts with: where it ends (after its document id, if any), its
    words and the document id. _NOT_A_QUOTATION when the opening mark starts none; None when
    more text is needed to tell. At the end of the reply (`final`), what is open is closed."""
    opening, closing = text[0], CLOSING[text[0]]
    close = next((i for i, c in enumerate(text[1:], 1) if c in closing), -1)
    inner = text[1:close] if close > 0 else text[1:]
    if opening not in closing and opening in inner:  # another opens first: that one is it
        return _NOT_A_QUOTATION
    if close < 0:
        if final:
            return len(text), inner, None  # cut off inside the quote
        return None if len(text) <= MAX_QUOTE_CHARS else _NOT_A_QUOTATION
    after = text[close + 1 :]
    cited = _DOC_ID.match(after)
    if cited:
        return close + 1 + cited.end(), inner, cited.group(1)
    if not final and _DOC_ID_SO_FAR.fullmatch(after):
        return None  # its document id may still come
    return close + 1, inner, None
