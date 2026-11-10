"""The Company page's facts (#505): `company_facts` and GET /api/company, read-only, for every
signed-in user, built from the profile and the base framework alone."""

from collections.abc import Iterator
from pathlib import Path

import pytest
import test_memory
from company_setup import MUSTERFIRMA, MUSTERFIRMA_ID
from fastapi.testclient import TestClient
from test_auth import FakeClerk, bearer, clerk_app, token
from test_memory import app_on, restart

from vouch import profile
from vouch.api import PROFILE_HEADER, create_app
from vouch.company_page import company_facts
from vouch.isa import BaseFrameworkError
from vouch.profile import load_named_profile, protection_scope, read_profile_once
from vouch.settings import Settings

PATH = "/api/company"
users = test_memory.users


@pytest.fixture(autouse=True)
def _fresh_process() -> Iterator[None]:
    restart()
    read_profile_once.cache_clear()
    yield
    restart()
    read_profile_once.cache_clear()
    protection_scope.cache_clear()


def test_the_facts_of_nordlicht_are_its_profiles_with_its_scope_and_not_applicable_entry():
    nordlicht = load_named_profile("nordlicht")

    facts = company_facts(nordlicht)

    assert facts["company"] == "Nordlicht Komponenten GmbH"
    assert facts["country"] == "Germany"
    assert facts["answer_language"] == "en"
    assert facts["base_framework"] == "VDA ISA 2027.1"
    assert facts["protection_level"] == "normal"
    assert facts["overlays"] == []
    assert facts["defined_in"] == "files"
    assert facts["scope"]["level"] == "normal"
    assert facts["scope"]["total"] == 85
    [entry] = facts["not_applicable"]
    assert entry == {
        "control_id": "5.1.2",
        "requirement_id": "S3",
        "reason": nordlicht.not_applicable[0].reason,
    }


def test_an_overlay_is_named_with_the_number_of_requirements_it_adds():
    facts = company_facts(load_named_profile("alpenwerk"))

    assert facts["overlays"] == [{"name": "AT NIS2 (illustrativ)", "requirements": 1}]
    assert facts["scope"]["level"] == "high"


def test_nordlicht_answers_its_facts(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        response = client.get(PATH, headers=alice)

    assert response.status_code == 200, response.text
    facts = response.json()
    nordlicht = load_named_profile("nordlicht")
    assert {k: facts[k] for k in ("company", "country", "answer_language", "base_framework")} == {
        "company": "Nordlicht Komponenten GmbH",
        "country": "Germany",
        "answer_language": "en",
        "base_framework": "VDA ISA 2027.1",
    }
    assert facts["protection_level"] == "normal"
    assert facts["overlays"] == []
    assert facts["defined_in"] == "files"
    assert facts["not_applicable"] == [
        {
            "control_id": "5.1.2",
            "requirement_id": "S3",
            "reason": nordlicht.not_applicable[0].reason,
        }
    ]


def levels(client: TestClient, headers: dict[str, str]) -> dict[str, dict]:
    answer = client.get("/api/developer/protection-levels", headers=headers).json()
    return {lv["level"]: lv for lv in answer["levels"]}


def test_the_scope_is_the_developer_routes_entry_for_the_profiles_level(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        nordlicht = client.get(PATH, headers=alice).json()
        alpenwerk = client.get(PATH, headers=alice | {PROFILE_HEADER: "alpenwerk"}).json()
        each = levels(client, alice)

    assert nordlicht["scope"] == each["normal"]
    assert alpenwerk["scope"] == each["high"]


def test_alpenwerks_overlay_adds_one_requirement(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        response = client.get(PATH, headers=alice | {PROFILE_HEADER: "alpenwerk"})

    assert response.json()["overlays"] == [{"name": "AT NIS2 (illustrativ)", "requirements": 1}]


def test_a_company_made_in_setup_is_defined_there_and_a_whole_control_has_no_requirement(users):
    alice, _ = users
    whole = [{"control": "1.1.1", "requirement": None, "reason": "Not ours."}]
    with TestClient(app_on(None)) as client:
        made = client.post(
            "/api/developer/profiles", json=MUSTERFIRMA | {"not_applicable": whole}, headers=alice
        )
        response = client.get(PATH, headers=alice | {PROFILE_HEADER: MUSTERFIRMA_ID})

    assert made.status_code == 201, made.text
    assert response.status_code == 200, response.text
    facts = response.json()
    assert facts["defined_in"] == "setup"
    assert facts["company"] == "Musterfirma Süd GmbH"
    assert facts["not_applicable"] == [
        {"control_id": "1.1.1", "requirement_id": None, "reason": "Not ours."}
    ]


def test_a_compliance_lead_who_isnt_a_developer_gets_200_and_signed_out_401():
    clerk = FakeClerk({"user_alice": {}})
    client = TestClient(clerk_app(clerk), raise_server_exceptions=False)

    lead = client.get(PATH, headers=bearer(token("user_alice")))
    signed_out = client.get(PATH)

    assert lead.status_code == 200, lead.text
    assert lead.json()["company"] == "Nordlicht Komponenten GmbH"
    assert signed_out.status_code == 401


def test_an_unreadable_base_framework_is_a_plain_500_naming_no_server_path(monkeypatch, caplog):
    where = Path("/srv/app/src/vouch/data/vda-isa-2027.1/controls.yaml")
    client = TestClient(create_app(Settings()), raise_server_exceptions=False)
    assert client.get(PATH).status_code == 200  # the profile read (and kept) while it could be

    def unreadable(*args, **kwargs):
        raise BaseFrameworkError(where, "not valid YAML")

    monkeypatch.setattr(profile, "load_base_framework", unreadable)
    protection_scope.cache_clear()  # worked out afresh from the unreadable framework

    response = client.get(PATH)

    assert response.status_code == 500
    assert response.json() == {"detail": "The company could not be loaded."}
    assert "/srv" not in response.text
    assert "controls.yaml" not in response.text
    assert "base framework" in caplog.text
