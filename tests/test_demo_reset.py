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
from test_memory import DEMO_CONTROL, app_on, set_daily_limit

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


DEMO_ROUNDS = [(ROUND_NAME, True), ("Q3 2026", False)]
"""The rounds since the fresh start in the demo state: (name, closed)."""


def compared(
    client: TestClient, user: dict[str, str]
) -> tuple[list[tuple[str, bool]], dict[str, Any]]:
    """The rounds since the fresh start, (name, closed), and the default comparison's changes."""
    listed = client.get("/api/rounds", headers=user).json()
    names = [
        (r["name"], r["closed_at"] is not None)
        for r in listed["rounds"]
        if not r["before_fresh_start"]
    ]
    shown = client.get(f"/api/rounds/{listed['default']}/comparison", headers=user)
    assert shown.status_code == 200, shown.text
    return names, shown.json()["changes"]


def assert_empty(client: TestClient, user: dict[str, str]) -> None:
    """No round closed since the fresh start, and nothing assessed."""
    rounds = client.get("/api/rounds", headers=user).json()["rounds"]
    assert not [r for r in rounds if r["closed_at"] and not r["before_fresh_start"]]
    assert client.get("/api/assessments", headers=user).json()["assessments"] == []


def test_start_afresh_on_nordlicht_restores_the_demo_round(database_url, users):
    lead, _ = users
    with TestClient(app_on(database_url)) as client:
        answer = reset(client, lead)
        names, changes = compared(client, lead)

    assert answer["fresh_start"]
    assert answer["demo"] == {"restored": True, "passes_left": 2, "passes": 3}
    assert names == DEMO_ROUNDS
    assert (changes["better"], changes["worse"]) == (3, 0)


def test_start_afresh_on_another_company_leaves_it_empty(database_url, users):
    lead, _ = users
    alpenwerk = {PROFILE_HEADER: ALPENWERK}
    with TestClient(app_on(database_url)) as client:
        answer = reset(client, lead, **alpenwerk)
        assert_empty(client, lead | alpenwerk)

    assert "demo" not in answer


def test_a_second_reset_gives_the_same_demo_state(database_url, users):
    lead, _ = users
    with TestClient(app_on(database_url)) as client:
        reset(client, lead)
        first = compared(client, lead)
        assert reset(client, lead)["demo"]["restored"]
        second = compared(client, lead)

    assert first == second
    assert first[0] == DEMO_ROUNDS


def test_with_the_profile_passes_used_up_the_rounds_are_as_a_plain_fresh_start_leaves_them(
    database_url, users
):
    lead, _ = users
    with TestClient(app_on(database_url)) as client:
        for _ in range(3):  # the day's three profile passes, used on the lead's own work
            assert client.post("/api/profile-pass", headers=lead).status_code == 200
        answer = reset(client, lead)
        rounds = client.get("/api/rounds", headers=lead).json()["rounds"]

    assert answer["demo"]["restored"] is False
    assert [(r["name"], r["closed_at"]) for r in rounds] == [("First round", None)]


def test_with_the_profile_passes_used_up_the_reset_clears_and_says_why(database_url, users):
    lead, _ = users
    with TestClient(app_on(database_url)) as client:
        for _ in range(3):  # each restore takes one of the day's three profile passes
            assert reset(client, lead)["demo"]["restored"]
        answer = reset(client, lead)
        assert_empty(client, lead)

    assert answer["demo"]["restored"] is False
    assert "profile passes" in answer["demo"]["reason"]


def test_the_restores_assessments_dont_count_against_the_daily_limit(database_url, users):
    lead, _ = users
    with TestClient(app_on(database_url)) as client:
        set_daily_limit(client, lead, 1)
        try:
            restored = reset(client, lead)["demo"]
            own = client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=lead)
        finally:
            set_daily_limit(client, lead, 30)

    assert restored["restored"] is True
    assert own.status_code == 200, own.text  # the lead's one assessment today is still theirs
