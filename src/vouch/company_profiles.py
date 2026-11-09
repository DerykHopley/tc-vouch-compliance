"""Companies made in the app (company setup, #385): their compliance profiles kept in the
database, listed and opened beside the file profiles.

A developer creates one with its company, country, answer language, protection level, the
requirements it marks not applicable, and its evidence: chosen documents of the sample library's
folders. What the request gave is kept, never a server path; the profile is rebuilt from it and
checked on load (vouch.profile's load_stored_profile), once per stored row, so requests reuse one
object and a profile that no longer validates (a document gone from the library) is a
BrokenProfile.

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
from datetime import UTC, datetime
from functools import cache, lru_cache
from itertools import count
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from vouch.database import Database
from vouch.documents import SUFFIX, document_path
from vouch.paths import SAMPLE_LIBRARY_DIR
from vouch.profile import (
    PROFILE_LOAD_ERRORS,
    PROFILE_NAME,
    STORED_SOURCES,
    BrokenProfile,
    ComplianceProfile,
    NonEmpty,
    NotApplicable,
    ProtectionLevel,
    load_problem,
    load_stored_profile,
)

log = logging.getLogger(__name__)

MAX_COMPANY = 100
NO_NAME_ID = "company"
"""The id of a company whose name leaves nothing to make one from."""
MAX_ID = 63
"""PROFILE_NAME's longest name."""
NO_DOCUMENT = "choose at least one document of the sample library"


class Evidence(BaseModel):
    """The documents a company chose from each folder of the sample library (ids: file names
    without .md, which may be given)."""

    model_config = ConfigDict(extra="forbid")

    policies: list[str] = []
    records: list[str] = []

    @model_validator(mode="after")
    def _documents_in_the_library(self) -> Self:
        for source in STORED_SOURCES:
            ids = [doc_id.removesuffix(SUFFIX) for doc_id in getattr(self, source)]
            for index, doc_id in enumerate(ids):
                if doc_id in ids[:index]:
                    raise ValueError(f"{source}: {doc_id} is named twice")
                folder = SAMPLE_LIBRARY_DIR / source
                if doc_id.endswith(SUFFIX) or document_path(folder, doc_id) is None:
                    raise ValueError(f"{source}: no document {doc_id} in the sample library")
            setattr(self, source, ids)
        if not any(getattr(self, source) for source in STORED_SOURCES):
            raise ValueError(NO_DOCUMENT)
        return self


class CompanySetup(BaseModel):
    """A company to make (`POST /api/developer/profiles`): what is kept of it."""

    model_config = ConfigDict(extra="forbid")

    company: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_COMPANY)
    ]
    country: NonEmpty
    answer_language: Literal["en", "de"]
    protection_level: ProtectionLevel
    not_applicable: list[NotApplicable] = []
    evidence: Evidence

    @model_validator(mode="after")
    def _no_nul(self) -> Self:
        """The database keeps no NUL character in text, so none is taken."""
        named = [("company", self.company), ("country", self.country)] + [
            (f"not_applicable.{i}", value)
            for i, entry in enumerate(self.not_applicable)
            for value in (entry.control, entry.requirement or "", entry.reason)
        ]
        for field, value in named:
            if "\x00" in value:
                raise ValueError(f"{field}: must not hold a NUL character")
        return self


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


def now() -> datetime:
    return datetime.now(UTC)


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


def read_stored_once(stored: StoredProfile) -> ComplianceProfile | BrokenProfile:
    """The stored company's compliance profile, or why it can't be used (path-free): validated
    once per stored row, so requests reuse the same object."""
    return _read_row(stored.id, json.dumps(stored.profile, sort_keys=True))


@lru_cache(maxsize=256)
def _read_row(profile_id: str, fields: str) -> ComplianceProfile | BrokenProfile:
    try:
        return load_stored_profile(profile_id, json.loads(fields))
    except PROFILE_LOAD_ERRORS as error:
        log.warning("company profile %s left out: %s", profile_id, error)
        return BrokenProfile(profile_id, load_problem(error))


def is_profile_name(name: str) -> bool:
    """Whether `name` could be a profile's id at all (a stored one is made to fit)."""
    return PROFILE_NAME.fullmatch(name) is not None
