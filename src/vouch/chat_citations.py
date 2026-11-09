"""Quotes in a chat answer (#46), checked on the whole answer before it is shown.

The chat prompt asks the model to write every quote as a citation, `<cite doc="DOC_ID">exact
words</cite>`, checked against its document. Any other form of citation (another case, other
quotes or attributes, no document, whitespace or zero-width characters inside the tag) fails
unchecked, and so does one never closed; a cite tag that opens or closes no citation is removed
alone.

Any other quotation, in any quotation mark (every Unicode initial or final one, and the
brackets and primes used as quotation marks), in `<q>` or `<blockquote>` (any case, with
attributes), or a Markdown blockquote (`>` lines, in a list item too), is checked as citing no document: against what the tools returned this turn. Any quotation mark
may close it, a blank line doesn't end it, and one never closed runs to its paragraph's end
(_quoted says which mark closes it). It counts when it is long
enough to be a quote (MIN_QUOTE_WORDS words, or MIN_CJK_CHARS CJK characters) outside the
citations in it; a quotation wrapping a citation is checked whole, the cite's words included,
and the citation against its document too. A shorter one is plain text ("the term “ISMS”");
prose without quotation marks is never a quote.

The check decides (vouch.chat_agent, ChatTools.check_quote); this module finds the quotes,
replaces each dropped one with QUOTE_REMOVED, logs it once and says why in a guard step.
"""

import logging
import re
import sys
import unicodedata
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
_GAP = r"[\s\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]*"
"""Whitespace, newlines included, and the characters that show as nothing (zero-width spaces
and joiners, direction marks, a soft hyphen): inside a cite tag they still make it one."""
_NAME = _GAP.join("cite") + r"\b"
_ATTRIBUTES = r"[^<>]{0,500}>"
_TAG = re.compile(rf"<{_GAP}(/?){_GAP}{_NAME}(?:{_ATTRIBUTES})?", re.IGNORECASE)
"""Anything shaped like a cite tag, opening or closing, in any case or spacing."""
_CLOSING_TAG = re.compile(rf"<{_GAP}/{_GAP}{_NAME}{_ATTRIBUTES}", re.IGNORECASE)
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
    "❝": ("❞",),
    "《": ("》",),
    "【": ("】",),
    "<q>": ("</q>",),
    # #181: half-width, full-width and vertical forms, lookalike brackets, and primes.
    "｢": ("｣",),
    "＇": ("＇",),
    "﹁": ("﹂",),
    "﹃": ("﹄",),
    "⹂": ("“", "”"),
    "❛": ("❜",),
    "❠": ("❞", "“", "”"),
    "〈": ("〉",),
    "〖": ("〗",),
    "″": ("″",),
    "‵": ("′",),
    "❟": ("❛", "❜"),
    "❮": ("❯",),
    "🙶": ("🙷",),
    "🙸": ("🙶", "🙷"),
    "<blockquote>": ("</blockquote>",),
}
"""Each opening quotation mark and its own closing marks. Any other mark closes it too, when
none of its own does in its paragraph (a model mixing “ and "). A tag (`<q>`, `<blockquote>`)
matches in any case and with attributes; every other mark _is_quotation_mark finds is added
below, closed by itself."""


def _is_quotation_mark(mark: str) -> bool:
    """Unicode's initial and final quotation marks (Pi, Pf), and every other character it
    names a quotation mark (ornaments, ❮ ❯), but an invisible tag character."""
    category = unicodedata.category(mark)
    if category == "Cf":
        return False
    return category in ("Pi", "Pf") or "QUOTATION MARK" in unicodedata.name(mark, "")


PAIRS |= {
    mark: (mark,)
    for mark in map(chr, range(sys.maxunicode + 1))
    if mark not in PAIRS and _is_quotation_mark(mark)
}
_APOSTROPHES = "'’"
"""Marks that are also apostrophes: inside a word (company’s, don't) or before a digit (’90s)
they neither open nor close a quotation, and they close only a quotation opened by a single
mark."""
_SINGLE = "‘’‚‛'"
_INCHES = '"″＂'
"""Marks that are also inch marks: right after a digit (a 15" screen) they open nothing."""
_TAG_MARK = re.compile(r"</?([a-z]+)>")
"""A tag in PAIRS, as written there."""
_TAG_NAME = re.compile(r"</?(\w+)")
"""A tag's name as matched in an answer (_mark's patterns allow no space after `<`)."""
_PARAGRAPH_END = re.compile(r"\n[ \t]*\n")


def _mark(mark: str, *, opening: bool) -> str:
    if tag := _TAG_MARK.fullmatch(mark):  # any case; an opening tag with attributes too
        if mark.startswith("</"):
            return rf"(?i:</{tag.group(1)}\s*>)"
        return rf"(?i:<{tag.group(1)}(?:\s[^<>]*)?>)"
    escaped = re.escape(mark)
    if mark in _INCHES and opening:
        return rf"(?<!\d){escaped}"
    if mark not in _APOSTROPHES:
        return escaped
    return rf"(?<!\w){escaped}(?!\d)" if opening else rf"{escaped}(?!\w)"


def _closing(marks: set[str]) -> re.Pattern[str]:
    longest_first = sorted(marks, key=len, reverse=True)
    return re.compile("|".join(_mark(m, opening=False) for m in longest_first))


_ALL_MARKS = {*PAIRS, *(c for closings in PAIRS.values() for c in closings)}
_OWN = {opening: _closing(set(closings)) for opening, closings in PAIRS.items()}
_ANY = {
    opening: _closing(_ALL_MARKS - (set() if opening in _SINGLE else set(_APOSTROPHES)))
    for opening in PAIRS
}
_OPENING = re.compile(
    "|".join(_mark(m, opening=True) for m in sorted(PAIRS, key=len, reverse=True))
)


def _pair_of(found: str) -> str:
    """The PAIRS key of an opening mark as written: a tag in its plain form."""
    tag = _TAG_NAME.match(found)
    return f"<{tag.group(1).lower()}>" if tag else found


def _quoted(text: str, opening: re.Match[str]) -> tuple[int, int, bool]:
    """Where the quotation `opening` starts ends: its words' end, its end, and whether a mark
    closed it. It ends at its own closing mark in its paragraph, else at any other quotation
    mark there, else at its own closing mark in a later paragraph (a blank line doesn't end a
    quotation), else, never closed, at its paragraph's end. Own marks first, so a quotation
    nested in it ("‘laptops’") doesn't end it. An apostrophe's quotation stays in its
    paragraph: a stray one far apart from its pair is likelier an apostrophe."""
    mark, start = _pair_of(opening.group()), opening.end()
    blank = _PARAGRAPH_END.search(text, start)
    paragraph_end = blank.start() if blank else len(text)
    tries = [(_OWN[mark], paragraph_end), (_ANY[mark], paragraph_end)]
    if mark not in _APOSTROPHES:
        tries.append((_OWN[mark], len(text)))
    for closing, end in tries:
        if (closed := closing.search(text, start, end)) is not None:
            return closed.start(), closed.end(), True
    words_end = max(start, len(text[:paragraph_end].rstrip()))
    return words_end, words_end, False


_ITEM = r"[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)*"
"""Indentation and list markers at any depth (`- `, `1. `, `- 2) `) before a blockquote's `>`."""
_BLOCKQUOTE = re.compile(rf"^{_ITEM}(>[^\n]*(?:\n[ \t]*>[^\n]*)*)", re.MULTILINE)
"""A blockquote; group 1 starts at its `>`, so a dropped one leaves its list item. A new list
item starts a blockquote of its own, so each item keeps its marker."""
_BLOCKQUOTE_MARK = re.compile(rf"^{_ITEM}>[ \t]?", re.MULTILINE)
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
    closers: set[int] = set()  # where a mark closed a quotation: it opens none
    scan = 0
    while (opening := _OPENING.search(masked, scan)) is not None:
        scan = opening.start() + 1  # every mark is tried, nested ones too
        if opening.start() in closers:
            continue
        words_end, end, closed = _quoted(masked, opening)
        if closed:
            closers.update(range(words_end, end))
        found.append((opening.start(), end, opening.end(), words_end))
    found += [(*b.span(1), *b.span(1)) for b in _BLOCKQUOTE.finditer(masked)]

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
