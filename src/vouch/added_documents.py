"""Added evidence documents (#584, feature #345): a second evidence source per company, kept in
the database beside the built-in documents (a profile's fixtures, the sample library).

A compliance lead adds a document (title, kind, approval date, review cycle and a `.md` or
`.txt` file of UTF-8 text) to a compliance profile. Adding one with a title the profile already
has (ignoring case) keeps it as the next version of that document; earlier versions stay, so an
audit trail entry citing one stays true. Withdrawing a document marks every version of it
withdrawn (GLOSSARY: Withdrawn document): nothing is deleted or rewritten but that mark.

The limits (Q7) are checked in `add`, before anything is written: DocumentRefused, its message
the sentence the lead sees.

Two backends, in the shape of vouch.rounds': ProcessAddedDocuments (tests and the local app)
and PostgresAddedDocuments (vouch.database's `added_documents` and `add_document`). Before
vouch-db-setup made the table, the Postgres store lists none and an add or withdraw is
DatabaseUnavailable (a 503).

The search tools, the quote check and the profile pass read the documents from the
AddedIndex (`added_index()`). There is no search index to rebuild (search reads text on each
call), so the index is every version in this process's memory: the app loads it when it starts,
a store given the index refreshes the profile's entry on each add and withdraw, and before each
use a cheap watermark from the database tells it whether another instance changed a profile.
Searches find the newest version of each document not withdrawn; the quote check reads the
version its assessment's search returned (`added_read` in its state).
"""

import datetime
import logging
import re
import threading
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from pathlib import PurePath
from typing import Any, Literal, Protocol

from vouch.database import Database, DatabaseUnavailable
from vouch.documents import (
    CYCLE_MONTHS,
    DOCUMENTED,
    IMPLEMENTED,
    DocumentFacts,
    EvidenceKind,
    document_files,
    facts_of,
)
from vouch.front_matter import split_front_matter
from vouch.profile import ComplianceProfile

log = logging.getLogger(__name__)

AddedKind = Literal["policy", "record"]
"""What the lead says an added document is: a policy (searched by search_policies) or a record
(search_records)."""
SOURCE_OF: dict[AddedKind, str] = {"policy": "policies", "record": "records"}
"""The evidence source whose search finds an added document of each kind."""
EVIDENCE_KIND_OF: dict[AddedKind, EvidenceKind] = {"policy": DOCUMENTED, "record": IMPLEMENTED}
"""Its evidence kind for the judge: a policy says what should be done, a record shows it was."""

MAX_BYTES = 200 * 1024
MAX_DOCUMENTS = 50
MAX_VERSIONS = 20
SUFFIXES = (".md", ".txt")
TITLE_LENGTH = 120
ID_LENGTH = 60

TOO_LARGE = f"This file is over {MAX_BYTES // 1024} KB, the most an added document may be."
TOO_MANY = (
    f"This company already has {MAX_DOCUMENTS} documents added and not withdrawn, the most it "
    "may have. Withdraw one, or add a new version of one instead."
)
TOO_MANY_VERSIONS = (
    f"This document already has {MAX_VERSIONS} versions, the most an added document may have. "
    "Add this file under another title instead."
)
NOT_TEXT = "This file isn't valid UTF-8 text. Save it as UTF-8 and add it again."
EMPTY = "This file is empty."
WRONG_TYPE = "Only Markdown (.md) and plain-text (.txt) files can be added."
NO_TITLE = f"Give the document a title of 1 to {TITLE_LENGTH} characters."
NO_CYCLE = f"Give the review cycle as one of: {', '.join(CYCLE_MONTHS)}."
NO_KIND = "Give the document's kind: policy or record."
WITHDRAWN_TITLE = (
    "A document with this title was withdrawn, so it can't take a new version. "
    "Add it under another title."
)


Entries = Callable[["AddedDocument"], Sequence[Mapping[str, Any]]]
"""The audit entries of an add (#585) or withdraw, given the version kept: one per lead whose
trail records it (#624), each a mapping of vouch.database's AUDIT_COLUMNS, kept with the
version, all or none."""


class DocumentRefused(ValueError):
    """An add refused before anything was written; the message is the sentence the lead sees."""


@dataclass(frozen=True)
class NewDocument:
    """What the lead gives to add a document: the file's name and bytes, and the form's fields."""

    filename: str
    content: bytes
    title: str
    kind: AddedKind
    approval_date: datetime.date
    review_cycle: str


@dataclass(frozen=True)
class AddedDocument:
    """One version of an added document, as kept. `doc_id` is the document's across its
    versions; `withdrawn_at` is None while it is not withdrawn."""

    profile_id: str
    doc_id: str
    version: int
    title: str
    kind: AddedKind
    approval_date: datetime.date
    review_cycle: str
    text: str
    added_by: str
    added_by_name: str
    added_at: datetime.datetime
    withdrawn_at: datetime.datetime | None = None

    @property
    def source(self) -> str:
        """The evidence source whose search finds it: policies or records."""
        return SOURCE_OF[self.kind]

    @property
    def body(self) -> str:
        """Its text without front matter: what is searched and quoted."""
        return split_front_matter(self.text)[1]

    def facts(self, today: datetime.date) -> DocumentFacts:
        """Its kind and staleness on `today`, as a built-in document's front matter gives them."""
        front = {
            "kind": EVIDENCE_KIND_OF[self.kind],
            "approval_date": self.approval_date,
            "review_cycle": self.review_cycle,
        }
        return facts_of(front, self.source, self.doc_id, today)


def builtin_ids(profile: ComplianceProfile) -> set[str]:
    """The ids of every built-in document in the profile's source folders, chosen or not: an
    added document's id is never one of them. A folder that can't be read gives none."""
    found: set[str] = set()
    for source in profile.evidence_sources.values():
        try:
            found |= {path.stem for path in document_files(source.folder)}
        except OSError:
            continue
    return found


def checked_text(new: NewDocument) -> str:
    """The file's text, or DocumentRefused naming the first limit it breaks."""
    if PurePath(new.filename).suffix.lower() not in SUFFIXES:
        raise DocumentRefused(WRONG_TYPE)
    if len(new.content) > MAX_BYTES:
        raise DocumentRefused(TOO_LARGE)
    try:
        text = new.content.decode("utf-8")
    except UnicodeDecodeError:
        raise DocumentRefused(NOT_TEXT) from None
    if not text.strip():  # nothing to search or quote (#585)
        raise DocumentRefused(EMPTY)
    return text


def checked_fields(new: NewDocument) -> str:
    """The trimmed title, or DocumentRefused for a field the form got wrong."""
    title = new.title.strip()
    if not 1 <= len(title) <= TITLE_LENGTH:
        raise DocumentRefused(NO_TITLE)
    if new.kind not in SOURCE_OF:
        raise DocumentRefused(NO_KIND)
    if new.review_cycle not in CYCLE_MONTHS:
        raise DocumentRefused(NO_CYCLE)
    return title


def new_id(title: str, taken: Collection[str]) -> str:
    """An id for a new document: its title in lower case, words joined by hyphens, never one in
    `taken` (the profile's built-in ids and its added documents' ids): -2, -3 … when it would."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:ID_LENGTH].strip("-")
    slug = slug or "document"
    candidate, n = slug, 1
    while candidate in taken:
        n += 1
        candidate = f"{slug}-{n}"
    return candidate


def next_version(
    *,
    profile_id: str,
    kept: Sequence[AddedDocument],
    new: NewDocument,
    builtin_ids: Collection[str],
    added_by: str,
    added_by_name: str,
    at: datetime.datetime,
) -> AddedDocument:
    """The version the add keeps, from the profile's documents already kept: the next of the
    document with the same title (ignoring case), at most MAX_VERSIONS of it, else version 1 of
    a new one. Only documents not withdrawn count toward MAX_DOCUMENTS. DocumentRefused for any
    limit it breaks, before anything is written; the stores call it holding the profile's lock,
    so two adds at once can't both pass a limit."""
    title = checked_fields(new)
    text = checked_text(new)
    same = [d for d in kept if d.title.casefold() == title.casefold()]
    if same:
        if any(d.withdrawn_at is not None for d in same):
            raise DocumentRefused(WITHDRAWN_TITLE)
        if len(same) >= MAX_VERSIONS:
            raise DocumentRefused(TOO_MANY_VERSIONS)
        doc_id, version = same[0].doc_id, max(d.version for d in same) + 1
    else:
        ids = {d.doc_id for d in kept}
        if len({d.doc_id for d in kept if d.withdrawn_at is None}) >= MAX_DOCUMENTS:
            raise DocumentRefused(TOO_MANY)
        doc_id, version = new_id(title, {*builtin_ids, *ids}), 1
    return AddedDocument(
        profile_id=profile_id,
        doc_id=doc_id,
        version=version,
        title=title,
        kind=new.kind,
        approval_date=new.approval_date,
        review_cycle=new.review_cycle,
        text=text,
        added_by=added_by,
        added_by_name=added_by_name,
        added_at=at,
    )


def newest(documents: Iterable[AddedDocument], *, withdrawn: bool = False) -> list[AddedDocument]:
    """The newest version of each document not withdrawn, by id; with `withdrawn`, of the
    withdrawn ones too (#586: the Documents panel lists them)."""
    by_id: dict[str, AddedDocument] = {}
    for d in documents:
        if (withdrawn or d.withdrawn_at is None) and (
            d.doc_id not in by_id or d.version > by_id[d.doc_id].version
        ):
            by_id[d.doc_id] = d
    return [by_id[k] for k in sorted(by_id)]


Watermark = tuple[int, int, datetime.datetime | None]
"""A profile's added documents in brief: how many versions are kept, how many of them are
withdrawn, and the latest time one was added or withdrawn. Any add or withdraw changes it (the
counts do, even when the times tie)."""


def watermark_of(documents: Iterable[AddedDocument]) -> Watermark:
    """The watermark of these versions, as vouch.database's `added_watermark` reads it."""
    kept = list(documents)
    times = [max(d.added_at, d.withdrawn_at or d.added_at) for d in kept]
    withdrawn = sum(d.withdrawn_at is not None for d in kept)
    return len(kept), withdrawn, max(times, default=None)


class AddedIndex:
    """The added documents search and the quote check read, per profile: every version kept,
    so a quote is checked against the version its assessment read (`read`), and searches find
    the newest of each document not withdrawn (`searchable`).

    Loaded from a store when the app starts (`load`, which binds it to that store); a store
    given it puts a profile's versions on each add and withdraw (`put`). Before each use, a
    bound store with a watermark (Postgres's) is asked for the profile's, and the profile is
    read again when it differs from the one held: so another instance's add or withdraw is seen
    on the next use, and a load that failed at start is made good. The process store has none:
    its puts are all there is."""

    def __init__(self) -> None:
        self._by_profile: dict[str, tuple[AddedDocument, ...]] = {}
        self._store: AddedDocuments | None = None
        self._guard = threading.Lock()

    def load(self, store: "AddedDocuments") -> None:
        """Bind the index to the store and read every profile's documents from it at once.
        DatabaseUnavailable leaves the index as it was (logged): a database that can't answer
        at start mustn't stop the app, and each profile is read again on its next use."""
        self._store = store
        try:
            kept = store.everything()
        except DatabaseUnavailable as error:
            log.error("added documents not loaded: %s", error)
            return
        by_profile: dict[str, list[AddedDocument]] = {}
        for d in kept:
            by_profile.setdefault(d.profile_id, []).append(d)
        with self._guard:
            self._by_profile = {p: tuple(docs) for p, docs in by_profile.items()}

    def put(self, profile_id: str, documents: Iterable[AddedDocument]) -> None:
        """The profile's documents as kept now, every version."""
        with self._guard:
            self._by_profile[profile_id] = tuple(documents)

    def searchable(self, profile_id: str, source: str | None = None) -> list[AddedDocument]:
        """The profile's documents a search may find (only `source`'s, when given): the newest
        version of each not withdrawn."""
        found = newest(self._current(profile_id))
        return [d for d in found if source is None or d.source == source]

    def shown(self, profile_id: str) -> list[AddedDocument]:
        """The profile's documents as the Documents panel lists them (#586): the newest version
        of each, a withdrawn one too (its `withdrawn_at` set)."""
        return newest(self._current(profile_id), withdrawn=True)

    def read(self, profile_id: str, doc_id: str, version: int) -> AddedDocument | None:
        """That version of the document, unless the document is withdrawn (or unknown)."""
        versions = [d for d in self._current(profile_id) if d.doc_id == doc_id]
        if any(d.withdrawn_at is not None for d in versions):
            return None
        return next((d for d in versions if d.version == version), None)

    def clear(self) -> None:
        """Forget every profile's documents and the store bound (tests start from nothing)."""
        with self._guard:
            self._by_profile = {}
            self._store = None

    def _current(self, profile_id: str) -> tuple[AddedDocument, ...]:
        with self._guard:
            held, store = self._by_profile.get(profile_id, ()), self._store
        if store is None:
            return held
        try:
            mark = store.watermark(profile_id)
            if mark is None or mark == watermark_of(held):
                return held
            fresh = tuple(store.listed(profile_id))
        except DatabaseUnavailable as error:
            log.error("added documents of %s not read again: %s", profile_id, error)
            return held
        self.put(profile_id, fresh)
        return fresh


@cache
def added_index() -> AddedIndex:
    """The process's index of added documents, which every search and quote check reads."""
    return AddedIndex()


class AddedDocuments(Protocol):
    """Where each profile's added documents are kept, every version."""

    def listed(self, profile_id: str) -> list[AddedDocument]:
        """Every version of the profile's documents, by id then version."""
        ...

    def everything(self) -> list[AddedDocument]:
        """Every version of every profile's documents (the index loads them at start)."""
        ...

    def watermark(self, profile_id: str) -> Watermark | None:
        """The profile's watermark as kept now; None when the index's own puts are all there is
        (the process store)."""
        ...

    def add(
        self,
        profile_id: str,
        new: NewDocument,
        builtin_ids: Collection[str],
        added_by: str,
        added_by_name: str,
        at: datetime.datetime,
        entries: Entries | None = None,
    ) -> AddedDocument:
        """Keep `new` as the next version of the document with its title, or version 1 of a new
        one whose id is none of `builtin_ids`, with `entries`' audit entries (one per lead, #624)
        when given, together.
        DocumentRefused, writing nothing, for a limit."""
        ...

    def withdraw(
        self, profile_id: str, doc_id: str, at: datetime.datetime, entries: Entries | None = None
    ) -> list[AddedDocument]:
        """Mark every version of the document withdrawn at `at` (one already withdrawn keeps
        its time), with `entries`' audit entries of its newest version when this marked any
        (#586), together: its versions as kept now, none when there is no such document."""
        ...


def _ordered(documents: Iterable[AddedDocument]) -> list[AddedDocument]:
    return sorted(documents, key=lambda d: (d.doc_id, d.version))


def _refresh(index: AddedIndex | None, profile_id: str, kept: Iterable[AddedDocument]) -> None:
    """Put the profile's versions as the write left them into the index, when there is one."""
    if index is not None:
        index.put(profile_id, _ordered(kept))


class ProcessAddedDocuments:
    """Added documents without a database: in this process, so a restart forgets them. An add's
    audit entry goes to `keep` (the process's trail) once the version is kept: a list append,
    which can't fail."""

    def __init__(
        self,
        index: AddedIndex | None = None,
        keep: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._kept: dict[str, list[AddedDocument]] = {}
        self._guard = threading.Lock()
        self._index = index
        self._keep = keep

    def listed(self, profile_id: str) -> list[AddedDocument]:
        with self._guard:
            return _ordered(self._kept.get(profile_id, []))

    def forget(self, profile_id: str) -> None:
        """Reset demo (#605): the profile's documents gone, every version."""
        with self._guard:
            self._kept.pop(profile_id, None)
            _refresh(self._index, profile_id, [])

    def everything(self) -> list[AddedDocument]:
        with self._guard:
            return _ordered(d for docs in self._kept.values() for d in docs)

    def watermark(self, profile_id: str) -> Watermark | None:
        return None

    def add(
        self,
        profile_id: str,
        new: NewDocument,
        builtin_ids: Collection[str],
        added_by: str,
        added_by_name: str,
        at: datetime.datetime,
        entries: Entries | None = None,
    ) -> AddedDocument:
        with self._guard:
            kept = self._kept.setdefault(profile_id, [])
            added = next_version(
                profile_id=profile_id,
                kept=kept,
                new=new,
                builtin_ids=builtin_ids,
                added_by=added_by,
                added_by_name=added_by_name,
                at=at,
            )
            kept.append(added)
            _refresh(self._index, profile_id, kept)  # under the guard: puts land in order
            if entries is not None and self._keep is not None:
                for row in entries(added):
                    self._keep(row)
        return added

    def withdraw(
        self, profile_id: str, doc_id: str, at: datetime.datetime, entries: Entries | None = None
    ) -> list[AddedDocument]:
        with self._guard:
            kept = self._kept.get(profile_id, [])
            marked = False
            for i, d in enumerate(kept):
                if d.doc_id == doc_id and d.withdrawn_at is None:
                    kept[i] = replace(d, withdrawn_at=at)
                    marked = True
            _refresh(self._index, profile_id, kept)
            versions = _ordered(d for d in kept if d.doc_id == doc_id)
            if marked and entries is not None and self._keep is not None:
                for row in entries(versions[-1]):
                    self._keep(row)
            return versions


class PostgresAddedDocuments:
    """Added documents in Postgres (vouch.database's added-documents table). The index is
    refreshed from the rows each write read in its own transaction, so a committed write is
    never followed by a failing read."""

    def __init__(self, db: Database, index: AddedIndex | None = None) -> None:
        self.db = db
        self._index = index

    def listed(self, profile_id: str) -> list[AddedDocument]:
        return [_document(r) for r in self.db.added_documents(profile_id)]

    def everything(self) -> list[AddedDocument]:
        return [_document(r) for r in self.db.added_documents(None)]

    def watermark(self, profile_id: str) -> Watermark | None:
        return self.db.added_watermark(profile_id)

    def add(
        self,
        profile_id: str,
        new: NewDocument,
        builtin_ids: Collection[str],
        added_by: str,
        added_by_name: str,
        at: datetime.datetime,
        entries: Entries | None = None,
    ) -> AddedDocument:
        def made(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
            added = next_version(
                profile_id=profile_id,
                kept=[_document(r) for r in rows],
                new=new,
                builtin_ids=builtin_ids,
                added_by=added_by,
                added_by_name=added_by_name,
                at=at,
            )
            return _row(added)

        audit = None if entries is None else (lambda row: entries(_document(row)))
        added, rows = self.db.add_document(profile_id, made, audit)
        _refresh(self._index, profile_id, map(_document, rows))
        return _document(added)

    def withdraw(
        self, profile_id: str, doc_id: str, at: datetime.datetime, entries: Entries | None = None
    ) -> list[AddedDocument]:
        audit = None if entries is None else (lambda row: entries(_document(row)))
        rows = [_document(r) for r in self.db.withdraw_document(profile_id, doc_id, at, audit)]
        _refresh(self._index, profile_id, rows)
        return [d for d in rows if d.doc_id == doc_id]


def _row(document: AddedDocument) -> dict[str, Any]:
    """A document as a mapping of vouch.database's ADDED_DOCUMENT_COLUMNS."""
    return {
        "profile_id": document.profile_id,
        "doc_id": document.doc_id,
        "version": document.version,
        "title": document.title,
        "kind": document.kind,
        "approval_date": document.approval_date,
        "review_cycle": document.review_cycle,
        "text": document.text,
        "added_by": document.added_by,
        "added_by_name": document.added_by_name,
        "added_at": document.added_at,
        "withdrawn_at": document.withdrawn_at,
    }


def _document(row: Mapping[str, Any]) -> AddedDocument:
    return AddedDocument(**row)


@cache
def process_added_documents() -> ProcessAddedDocuments:
    """Added documents without a database: one store per process, feeding the process's index,
    its adds' entries kept in the process's audit trail."""
    from vouch.audit import keep_row  # vouch.audit imports vouch.graph, which imports this module

    return ProcessAddedDocuments(added_index(), keep_row)


def added_documents_store(db: Database | None) -> AddedDocuments:
    """Where added documents are kept: Postgres with a database, else this process; either way
    refreshing the process's index on each add and withdraw."""
    return process_added_documents() if db is None else PostgresAddedDocuments(db, added_index())
