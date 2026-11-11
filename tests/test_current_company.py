"""The current company (#480, M9 decision 1): one shared setting a developer chooses on the
developer page, every user's company; kept like the model choice (the settings table with a
database, this process without), reading as Nordlicht when nothing listed is stored."""

import logging
from typing import Any

import pytest
import test_memory
from company_setup import MUSTERFIRMA, MUSTERFIRMA_ID
from fastapi.testclient import TestClient
from profiles_dir import SECOND, SECOND_COMPANY, make_profiles
from test_auth import FakeClerk, bearer, clerk_app, token
from test_database import needs_postgres
from test_memory import app_on, restart

from vouch import api, company_profiles
from vouch.api import create_app
from vouch.current_company import CURRENT_COMPANY_SETTING
from vouch.database import SETTINGS_TABLE, DatabaseUnavailable
from vouch.profile import PROFILE_FILE, read_profile_once
from vouch.settings import Settings

postgres = test_memory.postgres
empty_database = test_memory.empty_database
users = test_memory.users

NORDLICHT = "Nordlicht Komponenten GmbH"
ALPENWERK = "Alpenwerk Präzision GmbH"
CURRENT = "/api/developer/current-company"


@pytest.fixture(autouse=True)
def _fresh_process():
    restart()
    read_profile_once.cache_clear()
    yield
    restart()


def make_current(client: TestClient, headers: dict[str, str], company: str):
    return client.put(CURRENT, json={"company": company}, headers=headers)


def current(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get("/api/profiles", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["current"]


def company_of(client: TestClient, headers: dict[str, str]) -> str:
    """The company a request naming no profile is under."""
    response = client.get("/api/overview", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["company"]


def test_with_nothing_stored_nordlicht_is_current_and_a_request_naming_none_is_under_it(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        assert current(client, alice) == "nordlicht"
        assert company_of(client, alice) == NORDLICHT


def test_a_company_made_current_is_every_users_and_a_request_naming_none_is_under_it(users):
    developer, lead = users
    with TestClient(app_on(None)) as client:
        changed = make_current(client, developer, "alpenwerk")

        assert changed.status_code == 200, changed.text
        assert current(client, lead) == "alpenwerk"
        assert company_of(client, lead) == ALPENWERK
        # A request naming a profile is still under it (#163 decides per-user access).
        named = client.get("/api/overview", headers=lead | {"X-Vouch-Profile": "nordlicht"})
        assert named.json()["company"] == NORDLICHT


@needs_postgres
def test_with_a_database_the_choice_is_one_settings_row_and_outlasts_a_restart(postgres, users):
    developer, lead = users
    with TestClient(app_on(postgres.url)) as client:
        assert make_current(client, developer, "alpenwerk").status_code == 200

    restart()
    with TestClient(app_on(postgres.url)) as client:
        assert current(client, lead) == "alpenwerk"
        assert company_of(client, lead) == ALPENWERK
    with postgres.connection() as conn:
        rows = conn.execute(
            f"SELECT name, value FROM {SETTINGS_TABLE} WHERE name = %s", (CURRENT_COMPANY_SETTING,)
        ).fetchall()
    assert [(r["name"], r["value"]) for r in rows] == [(CURRENT_COMPANY_SETTING, "alpenwerk")]


def test_without_a_database_the_choice_lasts_until_the_server_restarts(users):
    developer, lead = users
    with TestClient(app_on(None)) as client:
        changed = make_current(client, developer, "alpenwerk")
        assert current(client, lead) == "alpenwerk"

    assert changed.json()["kept_in"] == "memory"
    restart()
    with TestClient(app_on(None)) as client:
        assert current(client, lead) == "nordlicht"


class DownStore:
    """The current company's store with the database down: every read and write raises."""

    kept_in = "database"

    def __init__(self) -> None:
        self.writes: list[str] = []

    def get(self) -> str | None:
        raise DatabaseUnavailable("the database is unavailable: test")

    def set(self, company: str) -> None:
        self.writes.append(company)
        raise DatabaseUnavailable("the database is unavailable: test")


def test_with_the_setting_unreadable_nordlicht_is_current_logged_and_a_change_is_503(users, caplog):
    alice, _ = users
    app = app_on(None)
    down = DownStore()
    app.state.current_company = down
    with caplog.at_level(logging.WARNING), TestClient(app, raise_server_exceptions=False) as c:
        listed = current(c, alice)
        under = company_of(c, alice)
        changed = make_current(c, alice, "alpenwerk")

    assert listed == "nordlicht"
    assert under == NORDLICHT
    assert "the current company can't be read" in caplog.text
    assert changed.status_code == 503
    assert changed.json()["unavailable"] == "database"


def made(client: TestClient, headers: dict[str, str], body: dict[str, Any] = MUSTERFIRMA) -> str:
    response = client.post("/api/developer/profiles", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def remove(client: TestClient, headers: dict[str, str], profile_id: str):
    return client.delete(f"/api/developer/profiles/{profile_id}", headers=headers)


@pytest.mark.parametrize("which", ["unknown", "removed", "broken"])
def test_a_company_not_listed_cannot_be_made_current(which, users):
    alice, _ = users
    if which == "broken":
        stale = MUSTERFIRMA | {"evidence": {"policies": ["retired-policy"]}}
        company_profiles.process_company_profiles().create("Stale GmbH", stale, "user_dev", set())
    with TestClient(app_on(None)) as client:
        assert make_current(client, alice, "alpenwerk").status_code == 200
        company = {"unknown": "nobody", "removed": MUSTERFIRMA_ID, "broken": "stale-gmbh"}[which]
        if which == "removed":
            made(client, alice)
            assert remove(client, alice, MUSTERFIRMA_ID).status_code == 200
        refused = make_current(client, alice, company)
        after = current(client, alice)

    assert refused.status_code == 404
    assert refused.json()["detail"] == "There is no such compliance profile."
    assert after == "alpenwerk"


def test_a_compliance_leads_change_is_403_and_changes_nothing():
    app = clerk_app(FakeClerk({"user_alice": {}}))
    client = TestClient(app, raise_server_exceptions=False)

    refused = make_current(client, bearer(token("user_alice")), "alpenwerk")

    assert refused.status_code == 403
    assert app.state.current_company.get() is None


def test_removing_the_current_company_makes_nordlicht_current_for_every_user(users):
    developer, lead = users
    with TestClient(app_on(None)) as client:
        made(client, developer)
        assert make_current(client, developer, MUSTERFIRMA_ID).status_code == 200
        assert current(client, lead) == MUSTERFIRMA_ID
        assert company_of(client, lead) == MUSTERFIRMA["company"]
        assert remove(client, developer, MUSTERFIRMA_ID).status_code == 200

        assert current(client, lead) == "nordlicht"
        assert current(client, developer) == "nordlicht"
        assert company_of(client, lead) == NORDLICHT


def test_the_developer_page_carries_the_current_and_listed_companies_on_read_and_change(users):
    alice, _ = users
    listed_ids = ["alpenwerk", "nordlicht", MUSTERFIRMA_ID]  # file companies, then made ones
    with TestClient(app_on(None)) as client:
        made(client, alice)
        read = client.get("/api/developer/settings", headers=alice).json()
        changed = make_current(client, alice, MUSTERFIRMA_ID).json()
        limit = client.put(
            "/api/developer/daily-limit", json={"daily_limit": 7}, headers=alice
        ).json()
        profiles = client.get("/api/profiles", headers=alice).json()["profiles"]

    assert read["current_company"] == "nordlicht"
    assert [c["id"] for c in read["listed_companies"]] == listed_ids
    assert read["listed_companies"] == profiles  # as GET /api/profiles lists them
    assert changed["current_company"] == MUSTERFIRMA_ID
    assert changed["listed_companies"] == profiles
    assert limit["current_company"] == MUSTERFIRMA_ID
    assert limit["listed_companies"] == profiles


class CompaniesDown:
    """The companies made in setup with the database down: every read raises."""

    def _down(self, *args: Any) -> Any:
        raise DatabaseUnavailable("the database is unavailable: test")

    create = all = one = remove = _down


def test_a_stored_setup_made_company_unreadable_falls_back_as_the_list_does(users, caplog):
    """The stored current company is made in setup and the database is down: a request naming no
    profile is under Nordlicht, as GET /api/profiles says, never a 503 (one rule, current_of)."""
    alice, _ = users
    app = app_on(None)
    app.state.current_company.set(MUSTERFIRMA_ID)
    app.dependency_overrides[api.app_company_profiles] = CompaniesDown
    with caplog.at_level(logging.WARNING), TestClient(app, raise_server_exceptions=False) as c:
        listed = current(c, alice)
        under = c.get("/api/overview", headers=alice)

    assert listed == "nordlicht"
    assert under.status_code == 200, under.text
    assert under.json()["company"] == NORDLICHT
    assert "the current company can't be read" in caplog.text


def test_with_nordlicht_unlisted_and_the_stored_company_removed_the_first_listed_is_current(
    tmp_path, users
):
    alice, _ = users
    profiles = make_profiles(tmp_path / "profiles", broken=False)
    read_profile_once.cache_clear()
    app = create_app(Settings(profiles_dir=profiles))
    app.dependency_overrides[api.current_user] = test_memory.as_user
    with TestClient(app, raise_server_exceptions=False) as client:
        made(client, alice)
        assert make_current(client, alice, MUSTERFIRMA_ID).status_code == 200
        assert remove(client, alice, MUSTERFIRMA_ID).status_code == 200
        (profiles / "nordlicht" / PROFILE_FILE).write_text("id: [unclosed\n", encoding="utf-8")

        assert current(client, alice) == SECOND
        assert company_of(client, alice) == SECOND_COMPANY
