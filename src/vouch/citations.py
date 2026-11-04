"""Citations: an exact quote from one version of an evidence document, checked in code.

The judge names a document and a quote; a QuoteChecker finds the quote in that document and
fills in the document's version and the section the quote sits in. A quote it can't find is
never kept, so Vouch never invents evidence.

A quote matches when, after whitespace is collapsed, Unicode put in one form (NFC) and
zero-width characters removed on both sides, it appears whole-word inside one block of the
document: a paragraph, a list item or a table row. So it can't start or end mid-word, or be
stitched together across rows or paragraphs. It needs at least MIN_QUOTE_WORDS words, so a
"Yes" or "at least 1" can't pass for evidence.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from vouch.documents import Section, document_path, sections
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


class CitationClaim(BaseModel):
    """A passage the judge relied on, as it names it."""

    doc_id: str = Field(description="The document's id, as the evidence lists it.")
    quote: str = Field(
        description=f"The passage, copied word for word from the document: at least "
        f"{MIN_QUOTE_WORDS} words from one paragraph, list item or table row."
    )


@dataclass(frozen=True)
class Citation:
    """A checked quote: it appears in this version of the document, under this section."""

    doc_id: str
    version: str
    section: str
    quote: str


@dataclass(frozen=True)
class _Document:
    doc_id: str
    version: str
    blocks: tuple[tuple[str, str], ...]  # (section heading, normalised block text)


def normalise(text: str) -> str:
    """The text in NFC, zero-width characters removed, whitespace collapsed to single spaces."""
    return " ".join(unicodedata.normalize("NFC", text).translate(_ZERO_WIDTH).split())


def _blocks(section: Section) -> list[str]:
    """The section's paragraphs, list items and table rows, each normalised."""
    blocks: list[list[str]] = []
    open_block = False
    for line in section.lines:
        stripped = line.strip()
        if not stripped:
            open_block = False
            continue
        if not open_block or _OWN_BLOCK.match(stripped):
            blocks.append([])
        blocks[-1].append(stripped)
        open_block = not stripped.startswith("|")  # a table row is always a block alone
    return [normalise(" ".join(lines)) for lines in blocks]


def _occurs(quote: str, text: str) -> bool:
    """Whether the quote is in the text, starting and ending on word boundaries."""
    start = r"(?<!\w)" if _WORD_CHAR.match(quote[0]) else ""
    end = r"(?!\w)" if _WORD_CHAR.match(quote[-1]) else ""
    return re.search(start + re.escape(quote) + end, text) is not None


def _load(doc_id: str, folders: list[Path]) -> _Document | DropReason:
    path = next((p for f in folders if (p := document_path(f, doc_id))), None)
    if path is None:
        return DropReason.NO_DOCUMENT
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DropReason.NO_DOCUMENT
    data, body = split_front_matter(text)
    version = str((data or {}).get("version") or "").strip()
    if not version:
        return DropReason.NO_VERSION
    blocks = tuple((s.heading, block) for s in sections(body) for block in _blocks(s))
    return _Document(path.stem, version, blocks)


class QuoteChecker:
    """Checks citations against the documents in these folders, reading each document once."""

    def __init__(self, folders: Iterable[str]):
        self._folders = [Path(f) for f in folders]
        self._documents: dict[str, _Document | DropReason] = {}

    def check(self, claim: CitationClaim) -> Citation | DropReason:
        """The checked Citation, or why it is dropped."""
        key = claim.doc_id.removesuffix(".md")
        if key not in self._documents:
            self._documents[key] = _load(key, self._folders)
        document = self._documents[key]
        if isinstance(document, DropReason):
            return document
        quote = normalise(claim.quote)
        if not quote:
            return DropReason.EMPTY_QUOTE
        if len(quote.split()) < MIN_QUOTE_WORDS:
            return DropReason.TOO_SHORT
        for heading, block in document.blocks:
            if _occurs(quote, block):
                return Citation(document.doc_id, document.version, heading, quote)
        return DropReason.NOT_FOUND
