"""Evidence documents in a folder: where one lives, its sections, its kind, and whether it
is stale. Shared by the search tools and the quote check, so both read a document the same
way."""

import calendar
import datetime
import logging
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from vouch.front_matter import split_front_matter

HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
LANGUAGE_CODE = re.compile(r"[a-z]{2}")  # ISO 639-1, the only form a page's `lang` takes
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

# The documents of a source's folder the profile chose (ids: file names without .md); None, the
# whole folder (EvidenceSource.documents). An assessment's state keeps them as lists.
DocumentChoice = tuple[str, ...] | None

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Section:
    """A heading and the lines under it, up to the next heading ("" before the first)."""

    heading: str
    lines: tuple[str, ...]


def escaped(folder: Path) -> bool:
    """Whether a folder the profile resolved on load now resolves elsewhere (swapped for a
    symlink since): nothing in it may be read."""
    return folder.resolve() != folder


def is_chosen(doc_id: str, choice: DocumentChoice) -> bool:
    """Whether a document (its id, `.md` allowed) may be used under a source's choice: every one
    when there is no choice (None)."""
    return choice is None or doc_id.removesuffix(SUFFIX) in choice


def choice_in(choices: Mapping[str, Collection[str]] | None, source: str) -> DocumentChoice:
    """A source's choice in an assessment's `document_choices` (source name to chosen ids); a
    source absent from it, or a state with none (saved before #384), uses its whole folder."""
    chosen = (choices or {}).get(source)
    return None if chosen is None else tuple(chosen)


def document_path(folder: Path, doc_id: str) -> Path | None:
    """The document with this id (its file name without `.md`, which may be given) directly in
    the folder, or None. An id holding a path, or a link pointing out of the folder, is None;
    so is every document when the folder has `escaped`."""
    doc_id = doc_id.removesuffix(SUFFIX)
    if not doc_id or doc_id in {".", ".."} or "/" in doc_id or "\\" in doc_id:
        return None
    if escaped(folder):
        return None
    path = folder / f"{doc_id}{SUFFIX}"
    if not path.is_file() or path.resolve().parent != folder:
        return None
    return path


def document_files(folder: Path) -> list[Path]:
    """The documents directly in the folder, by file name: its `.md` files, leaving out a link
    pointing out of it; none when the folder has `escaped`. A missing folder raises
    FileNotFoundError."""
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    if escaped(folder):
        return []
    return sorted(
        f
        for f in folder.iterdir()
        if f.suffix == SUFFIX and f.is_file() and f.resolve().parent == folder
    )


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
    approved, cycle = _as_date(data.get("approval_date")), data.get("review_cycle")
    if approved is None:
        return NO_REVIEW_DATE
    if not isinstance(cycle, str) or cycle not in CYCLE_MONTHS:  # a list or mapping too
        return NO_REVIEW_DATE
    due = review_due(approved, cycle)
    if due < today:
        return f"approved {approved.isoformat()} with a {cycle} review, due {due.isoformat()}"
    return None


def _as_date(value: Any) -> datetime.date | None:
    """A front-matter date (YAML's date or datetime, or ISO text), or None when it isn't one."""
    if isinstance(value, str):
        try:
            value = datetime.date.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime.datetime):
        return value.date()
    return value if isinstance(value, datetime.date) else None


def kind_of(
    front_matter: dict[str, Any] | None, source: str, doc_id: str, *, warn: bool = True
) -> EvidenceKind:
    """The document's kind: its front matter's, which is the source of truth. With none, or
    one that isn't a kind, the source's (SOURCE_KINDS), logged so the document gets fixed
    (`warn`; a page listing documents on every request passes False)."""
    kind = (front_matter or {}).get("kind")
    if isinstance(kind, str) and kind in KINDS:
        return kind  # type: ignore[return-value]  # checked against KINDS just above
    fallback = SOURCE_KINDS.get(source, DOCUMENTED)
    if warn:
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
    front_matter: dict[str, Any] | None,
    source: str,
    doc_id: str,
    today: datetime.date,
    *,
    warn: bool = True,
) -> DocumentFacts:
    """The document's kind and why it is stale (or None), from its front matter; `warn` as in
    kind_of."""
    return DocumentFacts(
        kind_of(front_matter, source, doc_id, warn=warn), stale_because(front_matter, today)
    )


def read_facts(path: Path, source: str, today: datetime.date) -> DocumentFacts:
    """The facts of the document at this path, read from its front matter."""
    data, _ = split_front_matter(path.read_text(encoding="utf-8"))
    return facts_of(data, source, path.stem, today)


@dataclass(frozen=True)
class DocumentSummary:
    """What Vouch has read from a document, for a page (#408): its title, kind, version,
    approval date (ISO), review cycle, why it is stale (None when current), and the language
    it is written in (#424, its front matter's, for the page's `lang`). The kind and staleness
    are the judge's (kind_of, stale_because)."""

    id: str
    source: str  # the folder it is in: policies, records
    title: str
    kind: EvidenceKind
    version: str | None
    approval_date: str | None
    review_cycle: str | None
    stale_because: str | None
    language: str | None  # ISO 639-1, as the front matter gives it


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def document_summary(path: Path, source: str, today: datetime.date) -> DocumentSummary:
    """The summary of the document at this path in a source's folder, judged on `today`. A
    missing title gives the id; a version, approval date or review cycle that can't be read
    gives None, and so does a language that isn't two lower-case letters (it becomes an HTML
    `lang`); no front matter at all, the source's kind and NO_REVIEW_DATE. The kind and
    staleness are facts_of's, read without logging (a page lists them on every request)."""
    data, _ = split_front_matter(path.read_text(encoding="utf-8"))
    front = data or {}
    facts = facts_of(data, source, path.stem, today, warn=False)
    approved = _as_date(front.get("approval_date"))
    return DocumentSummary(
        id=path.stem,
        source=source,
        title=_text(front.get("title")) or path.stem,
        kind=facts.kind,
        version=_text(front.get("version")),
        approval_date=approved.isoformat() if approved else None,
        review_cycle=_text(front.get("review_cycle")),
        stale_because=facts.stale_because,
        language=_language(front.get("language")),
    )


def _language(value: Any) -> str | None:
    return value if isinstance(value, str) and LANGUAGE_CODE.fullmatch(value) else None


def by_title(summaries: list[DocumentSummary]) -> list[DocumentSummary]:
    """The summaries by title, ignoring case, then by id: how a page lists a source's documents."""
    return sorted(summaries, key=lambda s: (s.title.casefold(), s.id))


def library_summaries(
    library: Path, sources: tuple[str, ...], today: datetime.date
) -> list[DocumentSummary]:
    """Every document of each of the library's source folders (document_files: a link out of a
    folder, or a folder that has `escaped`, lists nothing), in the sources' order, each source's
    by title. An unreadable folder or document raises (OSError, or ValueError for one that isn't
    UTF-8 text)."""
    found: list[DocumentSummary] = []
    for source in sources:
        summaries = [document_summary(p, source, today) for p in document_files(library / source)]
        found += by_title(summaries)
    return found


def paragraphs(body: str) -> list[tuple[str, str]]:
    """Each paragraph of the body (a table counts as one), with the heading of its section:
    (heading, text), the text's lines stripped."""
    found: list[tuple[str, str]] = []
    for section in sections(body):
        paragraph: list[str] = []
        for line in (*section.lines, ""):  # a blank line closes the last paragraph
            if line.strip():
                paragraph.append(line.strip())
            elif paragraph:
                found.append((section.heading, "\n".join(paragraph)))
                paragraph = []
    return found
