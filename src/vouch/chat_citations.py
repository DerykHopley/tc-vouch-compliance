"""Quotes in a chat answer (#46), checked on the whole answer before it is shown.

The chat prompt asks the model to write every quote as a citation, `<cite doc="DOC_ID">exact
words</cite>`, checked against its document. Any other form of citation (another case, other
quotes or attributes, no document) fails unchecked, and so does one never closed; a cite tag
that opens or closes no citation is removed alone.

Any other quotation, in any quotation marks, or a Markdown blockquote (`>` lines), is checked as
citing no document: against what the tools returned this turn. It counts when it is long
enough to be a quote (MIN_QUOTE_WORDS words, or MIN_CJK_CHARS CJK characters) outside the
citations in it; a quotation wrapping a citation is checked whole, the cite's words included,
and the citation against its document too. A shorter one is plain text ("the term “ISMS”");
prose without quotation marks is never a quote.

The check decides (vouch.chat_agent, ChatTools.check_quote); this module finds the quotes,
replaces each dropped one with QUOTE_REMOVED, logs it once and says why in a guard step.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from vouch.citations import MIN_QUOTE_WORDS

log = logging.getLogger(__name__)

QUOTE_REMOVED = "[quote removed]"
"""What a dropped quote leaves in the answer, so the sentence it was in still reads as cut."""
UNCITED = "it isn't in what Vouch found for this question"
"""Why an uncited quote is dropped (ChatTools.check_quote)."""
NO_DOCUMENT = "its citation names no document"
NOT_THE_FORM = 'its citation isn\'t written as <cite doc="…">…</cite>'
NOT_CLOSED = "its citation is never closed"
STRAY_TAG = "Removed a cite tag that opens or closes no citation."
MIN_CJK_CHARS = 8
"""The fewest CJK characters a quotation in Chinese, Japanese or Korean needs to count as a
quote: their words aren't spaced apart."""

_CITE_HEAD = r'<cite doc="([^"<>]+)">'
CITE = re.compile(_CITE_HEAD + r"(.*?)</cite>", re.DOTALL)
"""A citation in the one form the chat prompt asks for; the frontend reads the same form."""
_HEAD = re.compile(_CITE_HEAD)
_TAG = re.compile(r"<\s*(/?)\s*cite\b(?:[^<>\n]*>)?", re.IGNORECASE)
"""Anything shaped like a cite tag, opening or closing, in any case or spacing."""
_CLOSING_TAG = re.compile(r"<\s*/\s*cite\b[^<>\n]*>", re.IGNORECASE)
_NAMES_DOCUMENT = re.compile(r"""\bdoc\s*=\s*["']?[^"'\s>]""", re.IGNORECASE)
_MASK = "\0"
"""Stands in for a cite tag's characters while quotations are found, so positions hold."""

PAIRS: dict[str, tuple[str, ...]] = {
    "“": ("”", "“"),
    "”": ("”",),
    "„": ("“", "”"),
    "‟": ("”", "“"),
    ",,": ("''", "“", "”"),
    '"': ('"',),
    "＂": ("＂",),
    "‘": ("’", "‘"),
    "’": ("’",),
    "‚": ("‘", "’"),
    "‛": ("’", "‘"),
    "'": ("'",),
    "«": ("»",),
    "»": ("«", "»"),
    "‹": ("›",),
    "›": ("‹", "›"),
    "「": ("」",),
    "『": ("』",),
    "〝": ("〞", "〟"),
    "`": ("`",),
}
"""Each opening quotation mark and the marks that may close it."""
_APOSTROPHES = "'’"
"""Marks that are also apostrophes: inside a word (company’s, don't) they neither open nor
close a quotation."""


def _mark(mark: str, *, opening: bool) -> str:
    escaped = re.escape(mark)
    if mark not in _APOSTROPHES:
        return escaped
    return rf"(?<!\w){escaped}" if opening else rf"{escaped}(?!\w)"


def _quoted(opening: str, closings: tuple[str, ...]) -> re.Pattern[str]:
    """The quotation `opening` starts: to its first closing mark, within one paragraph."""
    close = "(?:" + "|".join(_mark(c, opening=False) for c in closings) + ")"
    inner = rf"(?:(?!{close})(?:[^\n]|\n(?![ \t]*\n)))+"
    return re.compile(rf"{_mark(opening, opening=True)}(?P<inner>{inner}){close}")


_QUOTED = {opening: _quoted(opening, closings) for opening, closings in PAIRS.items()}
_OPENING = re.compile(
    "|".join(_mark(m, opening=True) for m in sorted(PAIRS, key=len, reverse=True))
)
_BLOCKQUOTE = re.compile(r"^[ \t]*>[^\n]*(?:\n[ \t]*>[^\n]*)*", re.MULTILINE)
_BLOCKQUOTE_MARK = re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")

CheckQuote = Callable[[str, str | None], str | None]
"""Checks a quote (its text, and the document id it cites, or None): None to keep it, or why
it is dropped."""


def dropped_step(doc_id: str | None, reason: str) -> str:
    """The guard step's sentence for a quote dropped from a chat answer."""
    where = f"from {doc_id}" if doc_id is not None else "citing no document"
    return f"Dropped a quote {where}: {reason}."


@dataclass(frozen=True)
class _Citation:
    """A citation: [start, end) in the answer, its words at [inner, inner_end), the document
    it names, and why it fails unchecked (None: it is checked)."""

    start: int
    end: int
    inner: int
    inner_end: int
    doc_id: str | None
    fails: str | None = None


@dataclass(frozen=True)
class _Quotation:
    """A quotation or blockquote: [start, end) in the answer, and its words to check."""

    start: int
    end: int
    words: str


def checked_reply(reply: str, check: CheckQuote) -> tuple[str, list[str]]:
    """The answer with every quote that fails the check replaced by QUOTE_REMOVED (a stray cite
    tag removed), and a guard step for each, in the answer's order."""
    citations, strays = _citations(reply)
    masked = list(reply)
    for start, end in strays + [s for c in citations for s in _tags(c)]:
        masked[start:end] = _MASK * (end - start)
    edits: list[tuple[int, int, str, str]] = []  # start, end, replacement, guard step
    dropped: list[_Quotation] = []
    for quotation in _quotations("".join(masked), citations):
        reason = check(quotation.words, None)
        if reason is not None:
            dropped.append(quotation)
            edits.append(_dropped(quotation.start, quotation.end, None, reason))

    def inside_dropped(start: int) -> bool:
        return any(q.start <= start < q.end for q in dropped)

    for citation in citations:
        if inside_dropped(citation.start):
            continue
        quote = reply[citation.inner : citation.inner_end]
        reason = citation.fails or check(quote, citation.doc_id)
        if reason is not None:
            edits.append(_dropped(citation.start, citation.end, citation.doc_id, reason))
    for start, end in strays:
        if not inside_dropped(start):
            log.warning("removed a stray cite tag from a chat answer")
            edits.append((start, end, "", STRAY_TAG))
    edits.sort()
    out: list[str] = []
    pos = 0
    for start, end, replacement, _ in edits:
        out += [reply[pos:start], replacement]
        pos = end
    out.append(reply[pos:])
    return "".join(out), [step for *_, step in edits]


def _dropped(start: int, end: int, doc_id: str | None, reason: str) -> tuple[int, int, str, str]:
    log.warning("dropped a quote in a chat answer from %s (%s)", doc_id, reason)
    return start, end, QUOTE_REMOVED, dropped_step(doc_id, reason)


def _citations(reply: str) -> tuple[list[_Citation], list[tuple[int, int]]]:
    """The citations in the answer, and the cite tags that open or close none."""
    citations: list[_Citation] = []
    strays: list[tuple[int, int]] = []
    pos = 0
    while (tag := _TAG.search(reply, pos)) is not None:
        exact = CITE.match(reply, tag.start())
        if tag.group(1):  # a closing tag with no citation open
            strays.append(tag.span())
            pos = tag.end()
        elif exact is not None:
            citations.append(_Citation(*exact.span(), *exact.span(2), exact.group(1)))
            pos = exact.end()
        elif tag.group().endswith(">") and (closing := _CLOSING_TAG.search(reply, tag.end())):
            fails = NOT_THE_FORM if _NAMES_DOCUMENT.search(tag.group()) else NO_DOCUMENT
            citations.append(
                _Citation(tag.start(), closing.end(), tag.end(), closing.start(), None, fails)
            )
            pos = closing.end()
        elif (head := _HEAD.match(reply, tag.start())) is not None:
            end = len(reply)
            citations.append(_Citation(tag.start(), end, tag.end(), end, head.group(1), NOT_CLOSED))
            pos = end
        else:
            strays.append(tag.span())
            pos = tag.end()
    return citations, strays


def _tags(citation: _Citation) -> list[tuple[int, int]]:
    return [(citation.start, citation.inner), (citation.inner_end, citation.end)]


def _quotations(masked: str, citations: list[_Citation]) -> list[_Quotation]:
    """The quotations and blockquotes outside the citations (cite tags masked), long enough to
    be quotes outside the citations in them. Overlapping ones, or one cutting through a
    citation, are taken together, as their text."""
    found: list[tuple[int, int, int, int]] = []  # start, end, and where its words are
    scan = 0
    while (opening := _OPENING.search(masked, scan)) is not None:
        quoted = _QUOTED[opening.group()].match(masked, opening.start())
        if quoted is None:
            scan = opening.end()
            continue
        found.append((*quoted.span(), *quoted.span("inner")))
        scan = quoted.end()
    found += [(*b.span(), *b.span()) for b in _BLOCKQUOTE.finditer(masked)]

    def within_citation(start: int, end: int) -> bool:
        return any(c.inner <= start and end <= c.inner_end for c in citations)

    def outside_citations(start: int, end: int) -> str:
        kept = [
            ch
            for i, ch in enumerate(masked[start:end], start)
            if not any(c.start <= i < c.end for c in citations)
        ]
        return _plain("".join(kept))

    spans = sorted(
        (start, end, _plain(masked[words:words_end]))
        for start, end, words, words_end in found
        if not within_citation(start, end) and _long_enough(outside_citations(start, end))
    )
    merged: list[_Quotation] = []
    for start, end, words in spans:
        for citation in citations:  # a quotation cutting through a citation takes it whole
            if start < citation.end and citation.start < end and not start <= citation.start:
                start = min(start, citation.start)
            if start < citation.end and citation.start < end and not citation.end <= end:
                end = max(end, citation.end)
        if merged and end <= merged[-1].end:
            continue  # inside the one before: checked with it
        if merged and start < merged[-1].end:
            last = merged.pop()
            start, end = last.start, max(end, last.end)
            words = _plain(masked[start:end])
        merged.append(_Quotation(start, end, words))
    return merged


def _plain(text: str) -> str:
    """Text as its words are checked: cite tags and blockquote marks left out."""
    return _BLOCKQUOTE_MARK.sub("", text.replace(_MASK, ""))


def _long_enough(text: str) -> bool:
    return len(text.split()) >= MIN_QUOTE_WORDS or len(_CJK.findall(text)) >= MIN_CJK_CHARS
