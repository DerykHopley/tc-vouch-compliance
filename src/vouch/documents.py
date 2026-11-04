"""Evidence documents in a folder: where one lives, its sections, its kind, and whether it
is stale. Shared by the search tools and the quote check, so both read a document the same
way."""

import calendar
import datetime
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from vouch.front_matter import split_front_matter

HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
SUFFIX = ".md"
CYCLE_MONTHS = {"quarterly": 3, "half-yearly": 6, "yearly": 12, "two-yearly": 24}
NO_REVIEW_DATE = "no review date"

EvidenceKind = Literal["documented", "implemented"]  # GLOSSARY.md: documented, implemented
DOCUMENTED: EvidenceKind = "documented"
IMPLEMENTED: EvidenceKind = "implemented"
KINDS: tuple[EvidenceKind, ...] = (DOCUMENTED, IMPLEMENTED)
# The kind a document is taken as when its front matter gives none: the records show what was
# done; every other source (policies, and any source not listed) is documented.
SOURCE_KINDS: dict[str, EvidenceKind] = {"records": IMPLEMENTED}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Section:
    """A heading and the lines under it, up to the next heading ("" before the first)."""

    heading: str
    lines: tuple[str, ...]


def document_path(folder: Path, doc_id: str) -> Path | None:
    """The document with this id (its file name without `.md`, which may be given) directly in
    the folder, or None. An id holding a path, or a link pointing out of the folder, is None;
    so is every document when the folder (resolved by the profile on load) now resolves
    elsewhere, swapped for a symlink since."""
    doc_id = doc_id.removesuffix(SUFFIX)
    if not doc_id or doc_id in {".", ".."} or "/" in doc_id or "\\" in doc_id:
        return None
    root = folder.resolve()
    if root != folder:
        return None
    path = root / f"{doc_id}{SUFFIX}"
    if not path.is_file() or path.resolve().parent != root:
        return None
    return path


def sections(body: str) -> list[Section]:
    """The body's sections in order; the text before the first heading is the first one."""
    found: list[tuple[str, list[str]]] = [("", [])]
    for line in body.splitlines():
        match = HEADING.match(line)
        if match:
            found.append((match.group(1), []))
        else:
            found[-1][1].append(line)
    return [Section(heading, tuple(lines)) for heading, lines in found]


def review_due(approved: datetime.date, cycle: str) -> datetime.date:
    """The approval date plus the review cycle, clamped to the month's last day (31 January
    plus a quarter is 30 April)."""
    months = approved.month - 1 + CYCLE_MONTHS[cycle]
    year, month = approved.year + months // 12, months % 12 + 1
    return approved.replace(
        year=year, month=month, day=min(approved.day, calendar.monthrange(year, month)[1])
    )


def stale_because(front_matter: dict[str, Any] | None, today: datetime.date) -> str | None:
    """Why a document is stale on this day (GLOSSARY.md, "Stale evidence"), or None when it
    isn't: its review falls due before today. A document whose review date can't be worked
    out (no approval date, no known review cycle) counts as stale, with NO_REVIEW_DATE."""
    data = front_matter or {}
    approved, cycle = data.get("approval_date"), data.get("review_cycle")
    if isinstance(approved, str):
        try:
            approved = datetime.date.fromisoformat(approved)
        except ValueError:
            approved = None
    if isinstance(approved, datetime.datetime):
        approved = approved.date()
    if not isinstance(approved, datetime.date):
        return NO_REVIEW_DATE
    if not isinstance(cycle, str) or cycle not in CYCLE_MONTHS:  # a list or mapping too
        return NO_REVIEW_DATE
    due = review_due(approved, cycle)
    if due < today:
        return f"approved {approved.isoformat()} with a {cycle} review, due {due.isoformat()}"
    return None


def kind_of(front_matter: dict[str, Any] | None, source: str, doc_id: str) -> EvidenceKind:
    """The document's kind: its front matter's, which is the source of truth. With none, or
    one that isn't a kind, the source's (SOURCE_KINDS), logged so the document gets fixed."""
    kind = (front_matter or {}).get("kind")
    if isinstance(kind, str) and kind in KINDS:
        return kind  # type: ignore[return-value]  # checked against KINDS just above
    fallback = SOURCE_KINDS.get(source, DOCUMENTED)
    log.warning(
        "%s has no valid kind (%r); taken as %s from the %s", doc_id, kind, fallback, source
    )
    return fallback


@dataclass(frozen=True)
class DocumentFacts:
    """What the judge's rules need to know about a document, on a given day."""

    kind: EvidenceKind
    stale_because: str | None  # None: not stale


def facts_of(
    front_matter: dict[str, Any] | None, source: str, doc_id: str, today: datetime.date
) -> DocumentFacts:
    """The document's kind and why it is stale (or None), from its front matter."""
    return DocumentFacts(kind_of(front_matter, source, doc_id), stale_because(front_matter, today))


def read_facts(path: Path, source: str, today: datetime.date) -> DocumentFacts:
    """The facts of the document at this path, read from its front matter."""
    data, _ = split_front_matter(path.read_text(encoding="utf-8"))
    return facts_of(data, source, path.stem, today)
