"""Quotes in a chat answer (#46), found as the reply streams and each checked whole.

A document id in square brackets or parentheses (spaces allowed inside) that names one of the
profile's documents makes the text in front of it a citation of that document, checked against
it. The cited text is the text inside the nearest pair of quotation-like marks just before the
id (any Unicode quotation mark, ASCII ' " and `, 「」 and 『』); else the blockquote lines just
before it; else the sentence just before it. A quotation followed by an id naming no document
is checked too when it is long enough to be a quote: it cites a document that isn't there.

A quotation in the usual marks (“…”, „…“, "…", «…», »…«, ‚…‘) with no id after it, of at
least MIN_QUOTE_WORDS words, is an uncited quote, checked against what the tools returned. A
shorter one is plain text ("the term “ISMS”").

The check decides (vouch.chat_agent, ChatTools.check_quote); this module only finds what is
cited, holds text back while a citation may still be being written (an open quotation, the
current sentence or blockquote, a bracket not yet closed: at most MAX_QUOTE_CHARS), and
replaces each dropped one with QUOTE_REMOVED. A citation cut off at the end of the reply is
checked, never sent unchecked.
"""

import re
import unicodedata
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass

from vouch.citations import MIN_QUOTE_WORDS

QUOTE_REMOVED = "[quote removed]"
"""What a dropped quote leaves in the answer, so the sentence it was in still reads as cut."""
CLOSING = {"“": "”", "„": "“”", '"': '"', "«": "»", "»": "«", "‚": "‘’"}
"""The usual opening quotation marks, and the marks that close each: a quotation in them is
checked even with no document id after it."""
MAX_QUOTE_CHARS = 1000
"""The most text held back for a citation that may still come. An opening mark left unclosed
this long is a stray mark, passed on as text: no quotation the check would accept is this long
(one paragraph, list item or table row)."""
UNCITED = "it isn't in what Vouch found for this question"
"""Why an uncited quote is dropped (ChatTools.check_quote)."""


def _quotation_marks() -> frozenset[str]:
    """Every quotation-like mark: Unicode's initial and final quotes (Pi, Pf), its opening and
    closing punctuation named as quotation marks (Ps, Pe: „ ‚ 〝 〞 …), ASCII ' " `, and the
    corner brackets 「」『』."""
    marks = set("'\"`「」『』")
    for code in range(0x10000):  # every one Unicode has is in its first plane (Python 3.12)
        char = chr(code)
        category = unicodedata.category(char)
        if category in ("Pi", "Pf") or (
            category in ("Ps", "Pe") and "QUOTATION" in unicodedata.name(char, "")
        ):
            marks.add(char)
    return frozenset(marks)


QUOTATION_MARKS = _quotation_marks()

_OPENING = re.compile("[" + re.escape("".join(CLOSING)) + "]")
_MARK = re.compile("[" + re.escape("".join(sorted(QUOTATION_MARKS))) + "]")
_DOC_ID = re.compile(r"\s*[\[(]\s*([\w.-]+)\s*[\])]")
_DOC_ID_SO_FAR = re.compile(r"\s*(?:[\[(]\s*[\w.-]*\s*)?")
"""What may follow a closed quotation while its document id is still being written."""
_CITING = re.compile(r"[\[(]\s*([\w.-]+)\s*[\])]")
_CITING_SO_FAR = re.compile(r"[\[(]\s*([\w.-]*)\s*$")
_CLOSERS = r"[)\]}\"'”’»›」』]*"
_ENDS = re.compile(rf"[ \t]*(?:\n|$)|{_CLOSERS}[.!?…]{_CLOSERS}(?:\s|$)")
"""What follows a document id that ends its sentence or line: the end of the line, or the
sentence's closing punctuation (closing brackets and quotation marks around it allowed)."""
_MAY_END = re.compile(rf"[ \t]*|{_CLOSERS}(?:[.!?…]{_CLOSERS})?")
"""What may follow such an id while the text after it is still being written."""
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n\s*")
_LIST_MARK = re.compile(r"^(?:[-*+]|\d+[.)])\s+")

CheckQuote = Callable[[str, str | None], str | None]
"""Checks a quotation (its text, and the document id it cites, or None): None to keep it, or
why it is dropped."""


def dropped_step(doc_id: str | None, reason: str) -> str:
    """The guard step's sentence for a quote the check dropped from a chat answer."""
    where = f"from {doc_id}" if doc_id is not None else "citing no document"
    return f"Dropped a quote {where}: {reason}."


@dataclass(frozen=True)
class _Cited:
    """Text the reply cites: [start, end) in the text, the quote, and the document id."""

    start: int
    end: int
    quote: str
    doc_id: str | None


class CheckedCitations:
    """Passes a reply on as it is written, each citation in it checked once, whole (the module
    says what is cited). `documents` are the ids of the documents the profile allows. `flush`
    checks and sends what is held, at the end of a reply. Each piece comes with the guard
    steps for the quotes dropped from it."""

    def __init__(self, check: CheckQuote, documents: Collection[str]) -> None:
        self._check = check
        self._documents = frozenset(documents)
        self._pending = ""

    def add(self, chunk: str) -> Iterator[tuple[str, list[str]]]:
        self._pending += chunk
        yield from self._release(final=False)

    def flush(self) -> Iterator[tuple[str, list[str]]]:
        yield from self._release(final=True)

    def _names_document(self, doc_id: str) -> bool:
        return doc_id.removesuffix(".md") in self._documents

    def _release(self, *, final: bool) -> Iterator[tuple[str, list[str]]]:
        text = self._pending
        sent: list[str] = []
        steps: list[str] = []
        pos = scan = 0  # sent up to pos; looking for what comes next from scan
        hold = None
        while hold is None:
            opening = _OPENING.search(text, scan)
            citing = self._next_citing(text, scan)
            if citing is not None and (opening is None or citing.start() < opening.start()):
                cited = self._cited_before(text, pos, citing, final=final)
                if isinstance(cited, int):  # whether the id ends its sentence is still to come
                    hold = cited
                    break
                if cited is None:
                    scan = citing.end()
                    continue
            elif opening is not None:
                quotation = _quotation(text[opening.start() :], final=final)
                if quotation is None:  # may still become a citation: hold it
                    hold = opening.start()
                    break
                if quotation is _NOT_A_QUOTATION:
                    scan = opening.start() + 1
                    continue
                end, quote, doc_id = quotation
                cited = _Cited(opening.start(), opening.start() + end, quote, doc_id)
                words = len(quote.split())
                if words < MIN_QUOTE_WORDS and not (doc_id and self._names_document(doc_id)):
                    scan = cited.end  # a short quoted phrase: text
                    continue
            else:
                hold = len(text) if final else self._hold(text, pos)
                if final:
                    cited = self._cut_off(text, pos)
                    if cited is not None:
                        hold = None
                if hold is not None:
                    break
            reason = self._check(cited.quote.strip(), cited.doc_id)
            sent.append(text[pos : cited.start])
            if reason is None:
                sent.append(text[cited.start : cited.end])
            else:
                sent.append(QUOTE_REMOVED)
                steps.append(dropped_step(cited.doc_id, reason))
            pos = scan = cited.end
        hold = max(hold, pos)
        sent.append(text[pos:hold])
        self._pending = text[hold:]
        released = "".join(sent)
        if released or steps:
            yield released, steps

    def _next_citing(self, text: str, start: int) -> re.Match[str] | None:
        """The next complete [doc_id] or (doc_id) from `start`."""
        return _CITING.search(text, start)

    def _cited_before(
        self, text: str, pos: int, citing: re.Match[str], *, final: bool
    ) -> _Cited | int | None:
        """What the document id `citing` cites, from no earlier than `pos`: the nearest pair of
        quotation marks just before it, wherever the id is; else, when the id ends its sentence
        or line, the blockquote lines or the sentence. None when it cites nothing: an id naming
        no document, after anything but a long enough quotation, or an id mid-sentence (a
        reference). Where the text it may cite starts, when whether the id ends its sentence
        can't be told yet."""
        doc_id = citing.group(1)
        quoted = _quoted_before(text, pos, citing.start())
        if quoted is not None:
            start, quote = quoted
            if self._names_document(doc_id) or len(quote.split()) >= MIN_QUOTE_WORDS:
                return _Cited(start, citing.end(), quote, doc_id)
            return None
        if not self._names_document(doc_id):
            return None
        after = text[citing.end() :]
        if not final and _MAY_END.fullmatch(after):
            return _block_or_sentence_start(text, pos, citing.start())
        if not _ENDS.match(after):
            return None
        start, quote = _block_or_sentence_before(text, pos, citing.start())
        return _Cited(start, citing.end(), quote, doc_id)

    def _cut_off(self, text: str, pos: int) -> _Cited | None:
        """At the end of the reply, a document id still being written that may name one of the
        profile's documents: the text it was citing, checked as citing none."""
        partial = _CITING_SO_FAR.search(text, pos)
        if partial is None:
            return None
        prefix = partial.group(1).removesuffix(".md")
        if not any(doc_id.startswith(prefix) for doc_id in self._documents):
            return None
        quoted = _quoted_before(text, pos, partial.start())
        start, quote = quoted or _block_or_sentence_before(text, pos, partial.start())
        return _Cited(start, len(text), quote, None) if quote.strip() else None

    def _hold(self, text: str, pos: int) -> int:
        """Where the text that may still be cited starts: an open quotation, the blockquote or
        sentence a document id may still follow, or a bracket not yet closed; never more than
        MAX_QUOTE_CHARS back."""
        floor = max(pos, len(text) - MAX_QUOTE_CHARS)
        marks = _marks(text, pos, len(text))
        tail = text[marks[-1] + 1 :] if marks else ""
        if len(marks) % 2:  # one opened and not closed
            start = marks[-1]
        elif marks and _DOC_ID_SO_FAR.fullmatch(tail):  # one closed; its id may follow
            start = marks[-2]
        else:
            start = _block_or_sentence_start(text, pos, len(text), ending=True)
        return max(start, floor)


_NOT_A_QUOTATION = (0, "", None)


def _quotation(text: str, *, final: bool) -> tuple[int, str, str | None] | None:
    """The quotation in the usual marks (CLOSING) `text` starts with: where it ends (after its
    document id, if any), its words and the document id. _NOT_A_QUOTATION when the opening mark
    starts none; None when more text is needed to tell. At the end of the reply (`final`),
    what is open is closed."""
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


def _is_apostrophe(text: str, i: int) -> bool:
    """Whether the mark at `i` sits inside a word (company’s, don't): an apostrophe."""
    return 0 < i < len(text) - 1 and text[i - 1].isalnum() and text[i + 1].isalnum()


def _marks(text: str, start: int, end: int) -> list[int]:
    """Where the quotation marks are in text[start:end], apostrophes left out."""
    return [
        m.start() for m in _MARK.finditer(text, start, end) if not _is_apostrophe(text, m.start())
    ]


def _quoted_before(text: str, pos: int, end: int) -> tuple[int, str] | None:
    """The quotation just before `end` (only whitespace between): where its opening mark is,
    and its words; None when no closing mark is there, or no opening mark after `pos`."""
    before = text[pos:end].rstrip()
    closing = pos + len(before) - 1
    if not before or closing not in _marks(text, closing, closing + 1):
        return None
    opened = _marks(text, pos, closing)
    if not opened:
        return None
    return opened[-1], text[opened[-1] + 1 : closing]


def _block_or_sentence_start(text: str, pos: int, end: int, *, ending: bool = False) -> int:
    """Where the blockquote lines or the sentence that ends at `end` start, no earlier than
    `pos`. `ending`: the text may still go on, so a sentence just ended (only whitespace or a
    bracket being written after it) is still the one a document id may follow."""
    lines_start = text.rfind("\n", pos, end) + 1 or pos
    line = text[lines_start:end]
    if not line.strip() or (ending and _CITING_SO_FAR.fullmatch(line.strip())):
        lines_start = text.rfind("\n", pos, max(lines_start - 1, pos)) + 1 or pos
        line = text[lines_start:end]
    if line.lstrip().startswith(">"):
        while lines_start > pos:  # the lines of the blockquote above it
            above = text.rfind("\n", pos, lines_start - 1) + 1 or pos
            if not text[above:lines_start].lstrip().startswith(">"):
                break
            lines_start = above
        return max(lines_start, pos)
    stripped = text[pos:end].rstrip()
    if ending and (partial := _CITING_SO_FAR.search(stripped)) is not None:
        stripped = stripped[: partial.start()].rstrip()  # an id still being written
    starts = [m.end() for m in _SENTENCE_END.finditer(stripped)]
    return pos + (starts[-1] if starts else 0)


def _block_or_sentence_before(text: str, pos: int, end: int) -> tuple[int, str]:
    """The blockquote lines or the sentence just before `end`: where they start, and their
    words, blockquote marks and a list mark left out."""
    start = _block_or_sentence_start(text, pos, end)
    lines = text[start:end].strip().splitlines()
    words = " ".join(_LIST_MARK.sub("", line.strip().removeprefix(">").strip()) for line in lines)
    return start, words
