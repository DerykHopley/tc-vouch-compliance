"""Companies made in the app (company setup, #385): their compliance profiles kept in the
database, listed and opened beside the file profiles.

A developer creates one with its company, country, answer language, protection level, the
requirements it marks not applicable, and its evidence: chosen documents of the sample library's
folders. What the request gave is kept, never a server path; the profile is rebuilt from it and
checked on load (vouch.profile's load_stored_profile), once per stored row and version of its chosen
documents, so requests reuse one object and a profile that no longer validates (a document gone
from the library) is a BrokenProfile, and one whose document is back loads again.

Kept in Postgres with a database URL (vouch.database), else in this process (a developer's local
run, gone on restart), as the remediation decisions are.

Its id comes from the company name, made free of every profile folder's name and every stored
id by `-2`, `-3`, ..., so a demo can be run again under the same name.
"""

import json
import logging
import re
import threading
import unicodedata
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from itertools import count
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationError,
    model_validator,
)

from vouch.audit import now
from vouch.database import Database
from vouch.documents import document_path
from vouch.paths import SAMPLE_LIBRARY_DIR
from vouch.profile import (
    PROFILE_LOAD_ERRORS,
    PROFILE_NAME,
    BrokenProfile,
    ComplianceProfile,
    NonEmpty,
    NotApplicable,
    ProfileError,
    ProtectionLevel,
    field_problems,
    load_problem,
    load_stored_profile,
)

log = logging.getLogger(__name__)

MAX_COMPANY_LENGTH = 100
NO_NAME_ID = "company"
"""The id of a company whose name leaves nothing to make one from."""
MAX_ID = 63
"""PROFILE_NAME's longest name."""
NO_DOCUMENT = "choose at least one document of the sample library"


def _storable(text: str) -> str:
    """Text the database can keep: Postgres's jsonb takes no NUL character, and the text must
    be UTF-8 (JSON may carry a lone surrogate, which isn't)."""
    if "\x00" in text:
        raise ValueError("must not hold a NUL character")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("must be text UTF-8 can carry") from None
    return text


Text = Annotated[str, AfterValidator(_storable)]
"""Every text field of a company setup: text the database can keep (_storable)."""


class NotApplicableSetup(NotApplicable):
    """A requirement (or a whole control) the company marks not applicable; the profile checks
    it on load."""

    control: Text
    requirement: Text | None = None
    reason: Annotated[NonEmpty, AfterValidator(_storable)]


class Evidence(BaseModel):
    """The documents a company chose from each folder of the sample library (ids: file names
    without .md, which may be given); the profile checks each is there, once, on load
    (EvidenceSource)."""

    model_config = ConfigDict(extra="forbid")

    policies: list[Text] = []
    records: list[Text] = []

    @model_validator(mode="after")
    def _some_document(self) -> Self:
        if not any(self.model_dump().values()):
            raise ValueError(NO_DOCUMENT)
        return self


class CompanySetup(BaseModel):
    """A company to make (`POST /api/developer/profiles`): what is kept of it, every field but
    `evidence` its compliance profile's own (compliance_profile_of)."""

    model_config = ConfigDict(extra="forbid")

    company: Annotated[
        Text, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_COMPANY_LENGTH)
    ]
    country: Annotated[NonEmpty, AfterValidator(_storable)]
    answer_language: Literal["en", "de"]
    protection_level: ProtectionLevel
    not_applicable: list[NotApplicableSetup] = []
    evidence: Evidence


def compliance_profile_of(profile_id: str, setup: CompanySetup) -> ComplianceProfile:
    """The company's compliance profile under `profile_id`, its evidence the chosen documents of
    the sample library. ProfileError (path-free problems, each naming the setup's field) when
    the profile's checks refuse it: a document not there or named twice, a not-applicable
    entry the framework doesn't have."""
    return load_stored_profile(
        profile_id,
        setup.model_dump(exclude={"evidence"}),
        setup.evidence.model_dump(),
        SAMPLE_LIBRARY_DIR,
    )


@dataclass(frozen=True)
class StoredProfile:
    """A company profile as kept: its id, its setup's fields (CompanySetup, as JSON), who made it
    and when."""

    id: str
    profile: dict[str, Any]
    created_by: str
    created_at: datetime


def company_id(company: str) -> str:
    """The id a company's name gives: lowercase ASCII (accents dropped), every other run of
    characters a `-`, cut to fit PROFILE_NAME; NO_NAME_ID when nothing is left."""
    ascii_name = unicodedata.normalize("NFKD", company).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:MAX_ID].rstrip("-") or NO_NAME_ID


def candidate_ids(base: str, taken: Collection[str] = ()) -> Iterator[str]:
    """`base`, then `base-2`, `base-3`, ..., each cut to fit PROFILE_NAME, leaving out `taken`."""
    for n in count(1):
        suffix = "" if n == 1 else f"-{n}"
        candidate = base[: MAX_ID - len(suffix)].rstrip("-") + suffix
        if candidate not in taken:
            yield candidate


class CompanyProfiles(Protocol):
    """Where companies made in the app are kept."""

    def create(
        self, company: str, profile: Mapping[str, Any], created_by: str, taken: Collection[str]
    ) -> str:
        """Keep a company's profile under the first free id its name gives, none of `taken`
        (the profile folders' names) nor a stored one; that id."""
        ...

    def all(self) -> list[StoredProfile]:
        """Every stored company profile, oldest first."""
        ...

    def one(self, profile_id: str) -> StoredProfile | None:
        """The stored company profile `profile_id`; None when there's none."""
        ...


class ProcessCompanyProfiles:
    """Company profiles without a database: in this process, gone on restart."""

    def __init__(self) -> None:
        self._rows: dict[str, StoredProfile] = {}
        self._guard = threading.Lock()

    def create(
        self, company: str, profile: Mapping[str, Any], created_by: str, taken: Collection[str]
    ) -> str:
        with self._guard:
            profile_id = next(candidate_ids(company_id(company), {*taken, *self._rows}))
            self._rows[profile_id] = StoredProfile(profile_id, dict(profile), created_by, now())
        return profile_id

    def all(self) -> list[StoredProfile]:
        with self._guard:
            return list(self._rows.values())

    def one(self, profile_id: str) -> StoredProfile | None:
        with self._guard:
            return self._rows.get(profile_id)


class PostgresCompanyProfiles:
    """Company profiles in Postgres (vouch.database's company profiles table)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self, company: str, profile: Mapping[str, Any], created_by: str, taken: Collection[str]
    ) -> str:
        ids = candidate_ids(company_id(company), set(taken))
        return self.db.create_company_profile(ids, profile, created_by, now())

    def all(self) -> list[StoredProfile]:
        return [StoredProfile(**row) for row in self.db.company_profiles()]

    def one(self, profile_id: str) -> StoredProfile | None:
        row = self.db.company_profile(profile_id)
        return None if row is None else StoredProfile(**row)


@cache
def process_company_profiles() -> ProcessCompanyProfiles:
    """Company profiles without a database: one store per process."""
    return ProcessCompanyProfiles()


DocumentVersion = tuple[int, int, int] | None


def _chosen_versions(fields: Mapping[str, Any]) -> tuple[tuple[str, str, DocumentVersion], ...]:
    """Each chosen document of the sample library, with its file's version (modification time,
    change time, size; None when it isn't there), as vouch.profile versions a profile file."""
    evidence = fields.get("evidence")
    chosen = evidence if isinstance(evidence, Mapping) else {}
    versions = []
    for source in Evidence.model_fields:
        ids = chosen.get(source)
        for doc_id in ids if isinstance(ids, list) else ():
            versions.append((source, str(doc_id), _document_version(source, str(doc_id))))
    return tuple(versions)


def _document_version(source: str, doc_id: str) -> DocumentVersion:
    try:
        path = document_path(SAMPLE_LIBRARY_DIR / source, doc_id)
        stat = None if path is None else path.stat()
    except (OSError, ValueError):
        return None
    return None if stat is None else (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)


def _load_row(profile_id: str, fields: Mapping[str, Any]) -> ComplianceProfile | BrokenProfile:
    try:
        try:
            setup = CompanySetup.model_validate(fields)
        except ValidationError as error:
            raise ProfileError(Path(profile_id), field_problems(error)) from error
        return compliance_profile_of(profile_id, setup)
    except PROFILE_LOAD_ERRORS as error:
        log.warning("company profile %s left out: %s", profile_id, error)
        return BrokenProfile(profile_id, load_problem(error))


class _StoredReader:
    """read_stored_once: one read kept per stored id, replaced when its row or a chosen
    document changes, so a listing never pushes out its own reads, however many companies."""

    def __init__(self) -> None:
        self._kept: dict[str, tuple[object, ComplianceProfile | BrokenProfile]] = {}
        self._guard = threading.Lock()

    def __call__(self, stored: StoredProfile) -> ComplianceProfile | BrokenProfile:
        """The stored company's compliance profile, or why it can't be used (path-free):
        validated once per row and version of its chosen documents, so requests reuse the same
        object and a document gone from the library, or back, is seen."""
        key = (json.dumps(stored.profile, sort_keys=True), _chosen_versions(stored.profile))
        with self._guard:
            kept = self._kept.get(stored.id)
        if kept is not None and kept[0] == key:
            return kept[1]
        read = _load_row(stored.id, stored.profile)
        with self._guard:
            self._kept[stored.id] = (key, read)
        return read

    def cache_clear(self) -> None:
        with self._guard:
            self._kept.clear()


read_stored_once = _StoredReader()


def is_profile_name(name: str) -> bool:
    """Whether `name` could be a profile's id at all (a stored one is made to fit)."""
    return PROFILE_NAME.fullmatch(name) is not None
