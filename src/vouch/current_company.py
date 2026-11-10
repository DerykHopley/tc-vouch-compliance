"""The current company (#480, M9 decision 1): one shared setting, the company every user sees,
chosen by a developer on the developer page.

Kept as the model choice is (vouch.model_choice): in the database's settings table under
CURRENT_COMPANY_SETTING when VOUCH_DATABASE_URL is set, else in this process until the server
restarts. The value is the company's id. A stored id is never written over when its company is
removed: it simply stops naming a listed company, and the current company reads as Nordlicht
(current_of).
"""

from typing import Protocol

from vouch.database import Database
from vouch.model_choice import KeptIn
from vouch.profile import DEFAULT_PROFILE, ProfileScan

CURRENT_COMPANY_SETTING = "current_company"


class CurrentCompanyStore(Protocol):
    """Where the current company's id is kept. Both methods may raise DatabaseUnavailable."""

    kept_in: KeptIn

    def get(self) -> str | None: ...

    def set(self, company: str) -> None: ...


class MemoryCurrentCompanyStore:
    """No database: the choice lasts until the server restarts."""

    kept_in: KeptIn = "memory"

    def __init__(self) -> None:
        self._company: str | None = None

    def get(self) -> str | None:
        return self._company

    def set(self, company: str) -> None:
        self._company = company


class DatabaseCurrentCompanyStore:
    """The choice in the database's settings table, shared by every instance and restart."""

    kept_in: KeptIn = "database"

    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self) -> str | None:
        return self.db.setting(CURRENT_COMPANY_SETTING)

    def set(self, company: str) -> None:
        self.db.set_setting(CURRENT_COMPANY_SETTING, company)


def current_company_store(db: Database | None) -> CurrentCompanyStore:
    return DatabaseCurrentCompanyStore(db) if db is not None else MemoryCurrentCompanyStore()


def current_of(scan: ProfileScan, stored: str | None) -> str | None:
    """The current company's id among the listed ones (`scan.loaded`): the stored one while it is
    listed, else Nordlicht (DEFAULT_PROFILE), else the first listed; None with none listed."""
    listed = [p.id for p in scan.loaded]
    for name in (stored, DEFAULT_PROFILE):
        if name in listed:
            return name
    return listed[0] if listed else None
