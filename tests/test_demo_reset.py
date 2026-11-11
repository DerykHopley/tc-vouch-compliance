"""Start afresh restores Nordlicht's demo state (#605): the fresh start, asked with
`restore_demo` (as the user menu asks it), then the demo round seed (#579, vouch.demo_round's
`seed`) played through the app's own routes on the fake model, so Company › Rounds compares
with "Q2 2026" again. Other companies reset to empty, as before.

Through the HTTP API on both backends (memory, and Postgres under `scripts/test-db.sh`).
"""

from typing import Any

import test_memory
from fastapi.testclient import TestClient
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture: postgres's)
from test_memory import app_on

from vouch.api import PROFILE_HEADER
from vouch.demo_round import ROUND_NAME

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

FRESH_START = "/api/progress/fresh-start"
ALPENWERK = "alpenwerk"


def reset(client: TestClient, user: dict[str, str], **headers: str) -> dict[str, Any]:
    response = client.post(FRESH_START, json={"restore_demo": True}, headers=user | headers)
    assert response.status_code == 200, response.text
    return response.json()


def compared(client: TestClient, user: dict[str, str]) -> tuple[list[str], dict[str, Any]]:
    """The rounds' names since the fresh start, and the default comparison's changes."""
    listed = client.get("/api/rounds", headers=user).json()
    names = [r["name"] for r in listed["rounds"] if not r["before_fresh_start"]]
    shown = client.get(f"/api/rounds/{listed['default']}/comparison", headers=user)
    assert shown.status_code == 200, shown.text
    return names, shown.json()["changes"]


def test_start_afresh_on_nordlicht_restores_the_demo_round(database_url, users):
    lead, _ = users
    with TestClient(app_on(database_url)) as client:
        answer = reset(client, lead)
        names, changes = compared(client, lead)

    assert answer["fresh_start"]
    assert answer["demo"] == {"restored": True}
    assert names[-2:] == [ROUND_NAME, "Q3 2026"]
    assert (changes["better"], changes["worse"]) == (3, 0)


def test_start_afresh_on_another_company_leaves_it_empty(database_url, users):
    lead, _ = users
    alpenwerk = {PROFILE_HEADER: ALPENWERK}
    with TestClient(app_on(database_url)) as client:
        answer = reset(client, lead, **alpenwerk)
        rounds = client.get("/api/rounds", headers=lead | alpenwerk).json()["rounds"]
        assessed = client.get("/api/assessments", headers=lead | alpenwerk).json()

    assert "demo" not in answer
    assert not [r for r in rounds if r["closed_at"] and not r["before_fresh_start"]]
    assert assessed["assessments"] == []


def test_a_second_reset_gives_the_same_demo_state(database_url, users):
    lead, _ = users
    with TestClient(app_on(database_url)) as client:
        reset(client, lead)
        first = compared(client, lead)
        assert reset(client, lead)["demo"] == {"restored": True}
        second = compared(client, lead)

    # The round open at the reset is closed first, as the seed closes the open one: "First round"
    # the first time, the last reset's "Q3 2026" the next; from "Q2 2026" on, the same.
    assert first[0][1:] == second[0][1:] == [ROUND_NAME, "Q3 2026"]
    assert first[1] == second[1]


def test_with_the_profile_passes_used_up_the_reset_clears_and_says_why(database_url, users):
    lead, _ = users
    with TestClient(app_on(database_url)) as client:
        for _ in range(3):  # each restore takes one of the day's three profile passes
            assert reset(client, lead)["demo"]["restored"]
        answer = reset(client, lead)
        rounds = client.get("/api/rounds", headers=lead).json()["rounds"]
        assessed = client.get("/api/assessments", headers=lead).json()

    assert answer["demo"]["restored"] is False
    assert "profile passes" in answer["demo"]["reason"]
    assert not [r for r in rounds if r["closed_at"] and not r["before_fresh_start"]]
    assert assessed["assessments"] == []
