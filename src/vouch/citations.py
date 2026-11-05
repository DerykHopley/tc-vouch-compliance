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
"""

import datetime
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from vouch.documents import EvidenceKind, Section, document_path, facts_of, sections
from vouch.front_matter import split_front_matter

MIN_QUOTE_WORDS = 4
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
_OWN_BLOCK = re.compile(r"^(\||[-*+]\s|\d+[.)]\s)")  # a table row or list item starts a block
_WORD_CHAR = re.compile(r"\w")


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


@dataclass(frozen=True)
class _Document:
    doc_id: str
    version: str
    kind: EvidenceKind
    stale_because: str | None  # None: not stale
    blocks: tuple[tuple[str, str], ...]  # (section heading, normalised block text)


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


def _occurs(quote: str, text: str) -> bool:
    """Whether the quote is in the text, starting and ending on word boundaries."""
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
    documents with secrets and personal data removed: vouch.redaction)."""

    def __init__(
        self,
        sources: Mapping[str, str | Path],
        *,
        today: datetime.date,
        seen_as: Callable[[str], str] | None = None,
    ):
        self._sources = {name: Path(folder) for name, folder in sources.items()}
        self._today = today
        self._seen_as = seen_as
        self._documents: dict[str, _Document | DropReason] = {}

    def _document(self, doc_id: str) -> _Document | DropReason:
        key = doc_id.removesuffix(".md")
        if key not in self._documents:
            document = _load(key, self._sources, self._today)
            if self._seen_as is not None and isinstance(document, _Document):
                seen = tuple((h, normalise(self._seen_as(b))) for h, b in document.blocks)
                document = replace(document, blocks=seen)
            self._documents[key] = document
        return self._documents[key]

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
            if _occurs(quote, block):
                return Citation(
                    document.doc_id,
                    document.version,
                    heading,
                    quote,
                    kind=document.kind,
                    stale=document.stale_because is not None,
                    stale_because=document.stale_because,
                )
        return DropReason.NOT_FOUND
