"""GET /api/developer/prompts (#496, story 13): every prompt the agent gets, built for the current
company and a chosen control, for developers only. The previews themselves: test_prompt_previews.py."""

from pathlib import Path

import pytest
from company_setup import MUSTERFIRMA, MUSTERFIRMA_ID
from fastapi.testclient import TestClient
from test_auth import FakeClerk, bearer, clerk_app, token
from test_current_company import make_current
from test_memory import app_on, restart

from vouch import load_named_profile
from vouch.chat import CHAT_PROMPT_VERSION
from vouch.graph import PROMPT_VERSION
from vouch.isa import BaseFrameworkError
from vouch.profile import ComplianceProfile, read_profile_once
from vouch.profile_pass import PROFILE_PASS_PROMPT_VERSION

PROMPTS = "/api/developer/prompts"
DEVELOPER = {"X-Test-User": "developer_496"}  # local mode: a developer


@pytest.fixture(autouse=True)
def _fresh_process():
    restart()
    read_profile_once.cache_clear()
    yield
    restart()


@pytest.fixture
def client():
    with TestClient(app_on(None), raise_server_exceptions=False) as client:
        yield client


def prompts(client: TestClient, control: str | None = None, **headers: str):
    params = {} if control is None else {"control": control}
    return client.get(PROMPTS, params=params, headers=DEVELOPER | headers)


def test_with_nordlicht_current_1_1_1_has_six_prompts_in_order_each_with_its_version(client):
    response = prompts(client, "1.1.1")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["company"] == {"id": "nordlicht", "name": "Nordlicht Komponenten GmbH"}
    assert body["control"]["id"] == "1.1.1"
    assert [(p["name"], p["version"]) for p in body["prompts"]] == [
        ("gather", PROMPT_VERSION),
        ("overlay", PROMPT_VERSION),
        ("judge", PROMPT_VERSION),
        ("remediation", PROMPT_VERSION),
        ("profile_pass", PROFILE_PASS_PROMPT_VERSION),
        ("chat", CHAT_PROMPT_VERSION),
    ]
    gather = body["prompts"][0]
    assert [m["role"] for m in gather["messages"]] == ["system", "human"]
    assert gather["sent"] is True and gather["title"] == "Gather evidence" and gather["note"]


def test_an_empty_control_is_no_control_so_the_first_applicable_one_applies(client):
    response = client.get(PROMPTS, params={"control": ""}, headers=DEVELOPER)

    assert response.status_code == 200, response.text
    assert response.json()["control"]["id"] == prompts(client).json()["control"]["id"]


def test_with_no_control_the_first_applicable_one_and_every_applicable_control_listed(client):
    body = prompts(client).json()

    nordlicht = load_named_profile("nordlicht")
    controls = nordlicht.isa_edition().controls
    assert body["control"] == {"id": controls[0].id, "question": controls[0].question}
    assert body["controls"] == [{"id": c.id, "question": c.question} for c in controls]


def test_under_alpenwerk_1_6_2_names_german_and_its_overlay_lists_o1_whatever_header(client):
    assert make_current(client, DEVELOPER, "alpenwerk").status_code == 200

    body = prompts(client, "1.6.2").json()
    named = prompts(client, "1.6.2", **{"X-Vouch-Profile": "nordlicht"}).json()

    assert body["company"]["id"] == "alpenwerk"
    by_name = {p["name"]: p for p in body["prompts"]}
    assert "written in German" in by_name["gather"]["messages"][0]["text"]
    assert by_name["overlay"]["sent"] is True
    assert "- O1 (" in by_name["overlay"]["messages"][0]["text"]
    assert named == body


def test_a_control_with_no_overlay_requirement_has_the_overlay_not_sent(client):
    overlay = prompts(client, "1.1.1").json()["prompts"][1]

    assert overlay["sent"] is False
    assert overlay["messages"] == []
    assert overlay["note"] == "Not sent for this control: it has no overlay requirement."


def test_an_unknown_control_is_a_404_naming_it(client):
    response = prompts(client, "9.9.9")

    assert response.status_code == 404
    assert "9.9.9" in response.json()["detail"]


def test_a_control_not_applicable_to_the_current_company_is_a_404_naming_it(client):
    body = MUSTERFIRMA | {
        "not_applicable": [{"control": "5.2.9", "requirement": None, "reason": "Not ours."}]
    }
    made = client.post("/api/developer/profiles", json=body, headers=DEVELOPER)
    assert made.status_code == 201, made.text
    assert make_current(client, DEVELOPER, MUSTERFIRMA_ID).status_code == 200
    assert prompts(client).json()["company"]["id"] == MUSTERFIRMA_ID

    response = prompts(client, "5.2.9")

    assert response.status_code == 404
    assert "5.2.9" in response.json()["detail"]
    assert "5.2.9" not in [c["id"] for c in prompts(client).json()["controls"]]


def test_a_compliance_leads_request_is_a_403():
    client = TestClient(clerk_app(FakeClerk({"user_alice": {}})), raise_server_exceptions=False)

    response = client.get(PROMPTS, headers=bearer(token("user_alice")))

    assert response.status_code == 403


def test_an_unreadable_framework_is_a_plain_500_naming_no_file(client, monkeypatch):
    def unreadable(self):
        raise BaseFrameworkError(Path("/secret/path/isa.yaml"), "it can't be read")

    monkeypatch.setattr(ComplianceProfile, "isa_edition", unreadable)

    response = prompts(client, "1.1.1")

    assert response.status_code == 500
    assert "/secret" not in response.text
