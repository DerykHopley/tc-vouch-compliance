"""The current company (#480, M9 decision 1): one shared setting, the company every user sees,
chosen by a developer on the developer page.

Kept as the model choice is (vouch.model_choice): in the database's settings table under
CURRENT_COMPANY_SETTING when VOUCH_DATABASE_URL is set, else in this process until the server
restarts. The value is the company's id. A stored id is never written over when its company is
removed: it simply stops naming a listed company, and the current company reads as Nordlicht
(current_of).
"""

from vouch.database import Database
from vouch.model_choice import DatabaseSettingStore, MemorySettingStore, SettingStore
from vouch.profile import DEFAULT_PROFILE, ProfileScan

CURRENT_COMPANY_SETTING = "current_company"

CurrentCompanyStore = SettingStore
"""Where the current company's id is kept: the model choice's kind of store (vouch.model_choice),
under CURRENT_COMPANY_SETTING."""


def current_company_store(db: Database | None) -> CurrentCompanyStore:
    if db is None:
        return MemorySettingStore()
    return DatabaseSettingStore(db, CURRENT_COMPANY_SETTING)


def current_of(scan: ProfileScan, stored: str | None) -> str | None:
    """The current company's id among the listed ones (`scan.loaded`): the stored one while it is
    listed, else Nordlicht (DEFAULT_PROFILE), else the first listed; None with none listed."""
    listed = [p.id for p in scan.loaded]
    for name in (stored, DEFAULT_PROFILE):
        if name in listed:
            return name
    return listed[0] if listed else None
