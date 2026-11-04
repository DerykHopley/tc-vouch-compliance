"""Evidence documents in a folder: where one lives, its sections, and whether it is stale.
Shared by the search tools and the quote check, so both read a document the same way."""

import calendar
import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
SUFFIX = ".md"
CYCLE_MONTHS = {"quarterly": 3, "half-yearly": 6, "yearly": 12, "two-yearly": 24}
NO_REVIEW_DATE = "no review date"


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
    if not isinstance(approved, datetime.date) or cycle not in CYCLE_MONTHS:
        return NO_REVIEW_DATE
    due = review_due(approved, cycle)
    if due < today:
        return f"approved {approved.isoformat()} with a {cycle} review, due {due.isoformat()}"
    return None
