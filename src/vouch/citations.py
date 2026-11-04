"""Citations: an exact quote from one version of an evidence document, checked in code.

The judge names a document and a quote; `check_citation` finds the quote in that document
(whitespace-normalised) and fills in the document's version and the section the quote sits
in. A quote it can't find is never kept, so Vouch never invents evidence.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from vouch.front_matter import split_front_matter
from vouch.search import _HEADING

_DOC_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
# Why a citation is dropped.
NO_DOCUMENT = "no such document"
EMPTY_QUOTE = "the quote is empty"
NOT_FOUND = "the quote isn't in that document"


class CitationClaim(BaseModel):
    """A passage the judge relied on, as it names it."""

    doc_id: str = Field(description="The document's id, as the evidence lists it.")
    quote: str = Field(description="The passage, copied word for word from the document.")


@dataclass(frozen=True)
class Citation:
    """A checked quote: it appears in this version of the document, under this section."""

    doc_id: str
    version: str
    section: str
    quote: str


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _find_document(doc_id: str, folders: list[str]) -> Path | None:
    """The document `<doc_id>.md` directly in one of the folders; a link elsewhere is not."""
    if not _DOC_ID.fullmatch(doc_id):
        return None
    for folder in folders:
        root = Path(folder).resolve()
        path = Path(folder) / f"{doc_id}.md"
        if path.is_file() and path.resolve().parent == root:
            return path
    return None


def _sections(body: str) -> list[tuple[str, str]]:
    """Each section's heading and its text (the lines under it, headings left out)."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in body.splitlines():
        match = _HEADING.match(line)
        if match:
            sections.append((match.group(1), []))
        else:
            sections[-1][1].append(line)
    return [(heading, _normalise("\n".join(lines))) for heading, lines in sections]


def check_citation(claim: CitationClaim, folders: list[str]) -> Citation | str:
    """The checked Citation, or the reason it is dropped."""
    quote = _normalise(claim.quote)
    path = _find_document(claim.doc_id, folders)
    try:
        text = path.read_text(encoding="utf-8") if path else None
    except (OSError, UnicodeDecodeError):
        text = None
    if text is None:
        return NO_DOCUMENT
    if not quote:
        return EMPTY_QUOTE
    data, body = split_front_matter(text)
    for heading, section_text in _sections(body):
        if quote in section_text:
            version = str((data or {}).get("version", ""))
            return Citation(claim.doc_id, version, heading, quote)
    return NOT_FOUND

