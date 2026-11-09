"""Citations: an exact quote from one version of an evidence document, checked in code.

The judge names a document and a quote; a QuoteChecker finds the quote in that document and
fills in the document's version and the section the quote sits in. A quote it can't find is
never kept, so Vouch never invents evidence. Each citation also carries its document's kind
(documented or implemented, from its front matter) and whether it is stale on the day the
checker is given.

A quote matches when, after whitespace is collapsed, Unicode put in one form (NFC) and
zero-width characters removed on both sides, it appears whole-word inside one block of the
document: a paragraph, a list item or a table row. So it can't start or end mid-word, or be
stitched together across rows or paragraphs. It needs at least MIN_QUOTE_WORDS words, so a
"Yes" or "at least 1" can't pass for evidence.

The lead's own answers to open questions on the control are lead-supplied evidence (#422): a
LeadStatement, cited as "Q<id>" and checked by the same rule, each paragraph of the answer a
block. Its citation's version is the day it was answered, its section the label (LEAD_STATEMENT
in English; vouch.language has the other languages'), its kind documented, never stale.
"""

import copy
import datetime
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from vouch.documents import (
    DOCUMENTED,
    EvidenceKind,
    Section,
    document_path,
    facts_of,
    sections,
)
from vouch.front_matter import split_front_matter

MIN_QUOTE_WORDS = 4
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
_OWN_BLOCK = re.compile(r"^(\||[-*+]\s|\d+[.)]\s)")  # a table row or list item starts a block
_WORD_CHAR = re.compile(r"\w")
_PARAGRAPHS = re.compile(r"\n\s*\n")
LEAD_STATEMENT = "Lead's statement"
"""A lead statement's section in English: what a citation of one shows (#422)."""


class DropReason(StrEnum):
    """Why a citation is dropped; the text goes into the guard step."""

    NO_DOCUMENT = "no such document"
    NO_VERSION = "the document has no version"
    EMPTY_QUOTE = "the quote is empty"
    TOO_SHORT = f"the quote is shorter than {MIN_QUOTE_WORDS} words"
    NOT_FOUND = "the quote isn't in that document"
    IN_TWO_SOURCES = "the id names a document in more than one source"


class CitationClaim(BaseModel):
    """A passage the judge relied on, as it names it."""

    doc_id: str = Field(description="The document's id, as the evidence lists it.")
    quote: str = Field(
        description=f"The passage, copied word for word from the document: at least "
        f"{MIN_QUOTE_WORDS} words from one paragraph, list item or table row."
    )


@dataclass(frozen=True)
class Citation:
    """A checked quote: it appears in this version of the document, under this section. The
    document is documented or implemented evidence, and may be stale."""

    doc_id: str
    version: str
    section: str
    quote: str
    kind: EvidenceKind
    stale: bool
    stale_because: str | None  # why it is stale (None: not stale)
    lead_statement: bool = False
    """Quoted from the lead's statement, not a document (#422); False for a citation kept
    before it."""


@dataclass(frozen=True)
class LeadStatement:
    """The lead's answer to an open question on a control, as evidence (#422): the question's
    id, its control, the answer, the day it was answered in UTC, and the question it answers
    (for the judge to read; only the answer can be quoted)."""

    id: int
    control_id: str
    answer: str
    answered_on: datetime.date
    question: str

    @property
    def doc_id(self) -> str:
        """How the judge cites it: "Q12"."""
        return f"Q{self.id}"


@dataclass(frozen=True)
class _Document:
    doc_id: str
    version: str
    kind: EvidenceKind
    stale_because: str | None  # None: not stale
    blocks: tuple[tuple[str, str], ...]  # (section heading, normalised block text)
    lead_statement: bool = False


def _statement_document(statement: LeadStatement, label: str) -> _Document:
    """The statement as the checker reads it: each paragraph of the answer a block, under the
    label."""
    paragraphs = (normalise(p) for p in _PARAGRAPHS.split(statement.answer))
    return _Document(
        statement.doc_id,
        statement.answered_on.isoformat(),
        DOCUMENTED,
        None,
        tuple((label, p) for p in paragraphs if p),
        lead_statement=True,
    )


def normalise(text: str) -> str:
    """The text in NFC, zero-width characters removed, whitespace collapsed to single spaces."""
    return " ".join(unicodedata.normalize("NFC", text).translate(_ZERO_WIDTH).split())


def blocks(section: Section) -> list[str]:
    """The section's paragraphs, list items and table rows, each normalised."""
    found: list[list[str]] = []
    open_block = False
    for line in section.lines:
        stripped = line.strip()
        if not stripped:
            open_block = False
            continue
        if not open_block or _OWN_BLOCK.match(stripped):
            found.append([])
        found[-1].append(stripped)
        open_block = not stripped.startswith("|")  # a table row is always a block alone
    return [normalise(" ".join(lines)) for lines in found]


def occurs(quote: str, text: str) -> bool:
    """Whether the quote is evidence in the text: both normalised, it has at least
    MIN_QUOTE_WORDS words and appears in the text, starting and ending on word boundaries.
    The one quote-match rule, for citations and for quotes in an answer (vouch.language)."""
    quote, text = normalise(quote), normalise(text)
    if len(quote.split()) < MIN_QUOTE_WORDS:
        return False
    start = r"(?<!\w)" if _WORD_CHAR.match(quote[0]) else ""
    end = r"(?!\w)" if _WORD_CHAR.match(quote[-1]) else ""
    return re.search(start + re.escape(quote) + end, text) is not None


def _load(doc_id: str, sources: dict[str, Path], today: datetime.date) -> _Document | DropReason:
    found = [(s, p) for s, f in sources.items() if (p := document_path(f, doc_id))]
    if not found:
        return DropReason.NO_DOCUMENT
    if len(found) > 1:  # which one the judge read can't be told, so neither is taken
        return DropReason.IN_TWO_SOURCES
    [(source, path)] = found
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DropReason.NO_DOCUMENT
    data, body = split_front_matter(text)
    version = str((data or {}).get("version") or "").strip()
    if not version:
        return DropReason.NO_VERSION
    facts = facts_of(data, source, path.stem, today)
    quotable = tuple((s.heading, block) for s in sections(body) for block in blocks(s))
    return _Document(path.stem, version, facts.kind, facts.stale_because, quotable)


class QuoteChecker:
    """Checks citations against the documents in these sources' folders (source name to
    folder), reading each document once; staleness is judged on `today`. With `seen_as`, each
    block of a document is checked as the quote's writer saw it (the chat's model sees
    documents with secrets and personal data removed: vouch.redaction).

    `statements` are the lead's statements on the control checked (#422), each cited by its
    "Q<id>" and shown under `label`. An id naming both a statement and a document in a source
    is dropped as IN_TWO_SOURCES, as one naming two documents is."""

    def __init__(
        self,
        sources: Mapping[str, str | Path],
        *,
        today: datetime.date,
        seen_as: Callable[[str], str] | None = None,
        statements: Iterable[LeadStatement] = (),
        label: str = LEAD_STATEMENT,
    ):
        self._sources = {name: Path(folder) for name, folder in sources.items()}
        self._today = today
        self._seen_as = seen_as
        self._label = label
        self._documents: dict[str, _Document | DropReason] = {}
        self._statements = self._read_statements(statements)

    def _read_statements(self, statements: Iterable[LeadStatement]) -> dict[str, _Document]:
        return {s.doc_id: self._as_seen(_statement_document(s, self._label)) for s in statements}

    def for_statements(self, statements: Iterable[LeadStatement]) -> "QuoteChecker":
        """A checker of these statements instead, under the same label, reading the documents
        this one has read (the profile pass checks each control against its own statements)."""
        other = copy.copy(self)  # shares the documents read
        other._statements = self._read_statements(statements)
        return other

    def _as_seen(self, document: _Document) -> _Document:
        if self._seen_as is None:
            return document
        seen = tuple((h, normalise(self._seen_as(b))) for h, b in document.blocks)
        return replace(document, blocks=seen)

    def _document(self, doc_id: str) -> _Document | DropReason:
        key = doc_id.removesuffix(".md")
        if key not in self._documents:
            document = _load(key, self._sources, self._today)
            if isinstance(document, _Document):
                document = self._as_seen(document)
            self._documents[key] = document
        statement = self._statements.get(key)
        if statement is None:
            return self._documents[key]
        if self._documents[key] is not DropReason.NO_DOCUMENT:
            return DropReason.IN_TWO_SOURCES  # which one the judge read can't be told
        return statement

    def check(self, claim: CitationClaim) -> Citation | DropReason:
        """The checked Citation, or why it is dropped."""
        document = self._document(claim.doc_id)
        if isinstance(document, DropReason):
            return document
        quote = normalise(claim.quote)
        if not quote:
            return DropReason.EMPTY_QUOTE
        if len(quote.split()) < MIN_QUOTE_WORDS:
            return DropReason.TOO_SHORT
        for heading, block in document.blocks:
            if occurs(quote, block):
                return Citation(
                    document.doc_id,
                    document.version,
                    heading,
                    quote,
                    kind=document.kind,
                    stale=document.stale_because is not None,
                    stale_because=document.stale_because,
                    lead_statement=document.lead_statement,
                )
        return DropReason.NOT_FOUND
