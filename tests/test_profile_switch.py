"""The profile switcher's API (#47): the user's compliance profiles listed, a broken one left out
and noted for developers, and each request answered under the profile its X-Vouch-Profile
header names, so nothing is shared between profiles."""

import logging
import shutil

import pytest
import test_memory
from fastapi.testclient import TestClient
from profiles_dir import BROKEN, SECOND, SECOND_COMPANY, make_profiles
from test_memory import as_user, assess, restart

from vouch.api import PROFILE_HEADER, cached_profile, create_app, current_user
from vouch.settings import Settings

users = test_memory.users


@pytest.fixture
def profiles(tmp_path):
    """A profiles folder with nordlicht, testwerk and a broken one."""
    return make_profiles(tmp_path / "profiles")


def app_with(profiles_dir) -> TestClient:
    cached_profile.cache_clear()
    restart()
    app = create_app(Settings(profiles_dir=profiles_dir))
    app.dependency_overrides[current_user] = as_user
    return TestClient(app, raise_server_exceptions=False)


def under(headers: dict[str, str], profile_id: str) -> dict[str, str]:
    return headers | {PROFILE_HEADER: profile_id}


def test_the_users_profiles_are_listed_with_company_country_and_language(profiles, users):
    alice, _ = users
    with app_with(profiles) as client:
        response = client.get("/api/profiles", headers=alice)

    assert response.status_code == 200
    assert response.json() == {
        "profiles": [
            {
                "id": "nordlicht",
                "company": "Nordlicht Komponenten GmbH",
                "country": "Germany",
                "answer_language": "en",
            },
            {
                "id": SECOND,
                "company": SECOND_COMPANY,
                "country": "Austria",
                "answer_language": "de",
            },
        ],
        "default": "nordlicht",
    }


def test_a_broken_profile_is_left_out_logged_and_noted_on_the_developer_page(
    profiles, users, caplog
):
    alice, _ = users
    with app_with(profiles) as client, caplog.at_level(logging.WARNING, "vouch.api"):
        listed = client.get("/api/profiles", headers=alice)
        developer = client.get("/api/developer/settings", headers=alice)

    assert listed.status_code == 200
    assert BROKEN not in [p["id"] for p in listed.json()["profiles"]]
    assert BROKEN in caplog.text
    assert developer.status_code == 200
    [note] = developer.json()["broken_profiles"]
    assert note["id"] == BROKEN
    assert note["problem"] == "not valid YAML (ParserError)"  # the problem, not the server's path


def test_with_one_profile_the_list_has_one(tmp_path, users):
    alice, _ = users
    make_profiles(tmp_path, broken=False)
    shutil.rmtree(tmp_path / SECOND)
    with app_with(tmp_path) as client:
        listed = client.get("/api/profiles", headers=alice).json()

    assert [p["id"] for p in listed["profiles"]] == ["nordlicht"]


def test_with_the_default_profile_broken_there_is_no_default(tmp_path, users):
    alice, _ = users
    make_profiles(tmp_path, broken=False)
    (tmp_path / "nordlicht" / "profile.yaml").write_text("id: [unclosed\n", encoding="utf-8")
    with app_with(tmp_path) as client:
        listed = client.get("/api/profiles", headers=alice).json()

    assert [p["id"] for p in listed["profiles"]] == [SECOND]
    assert listed["default"] is None


def test_each_profile_keeps_its_own_progress(profiles, users):
    alice, _ = users
    with app_with(profiles) as client:
        thread_id = assess(client, under(alice, "nordlicht"))
        accepted = client.post(
            f"/api/assessments/{thread_id}/review",
            json={"decision": "accept"},
            headers=under(alice, "nordlicht"),
        )
        nordlicht = client.get("/api/overview", headers=under(alice, "nordlicht")).json()
        second = client.get("/api/overview", headers=under(alice, SECOND)).json()
        listed_second = client.get("/api/assessments", headers=under(alice, SECOND)).json()
        opened_second = client.get(f"/api/assessments/{thread_id}", headers=under(alice, SECOND))
        trail_second = client.get("/api/audit-trail", headers=under(alice, SECOND)).json()

    assert accepted.status_code == 200, accepted.text
    assert (nordlicht["company"], nordlicht["readiness"]["accepted"]) == (
        "Nordlicht Komponenten GmbH",
        1,
    )
    assert (second["company"], second["readiness"]["accepted"]) == (SECOND_COMPANY, 0)
    assert second["by_status"]["not-assessed"] == second["readiness"]["controls"]
    assert listed_second["assessments"] == []
    assert opened_second.status_code == 404
    assert trail_second["entries"] == []


def test_without_the_header_requests_are_under_the_default_profile(profiles, users):
    alice, _ = users
    with app_with(profiles) as client:
        thread_id = assess(client, alice)
        listed = client.get("/api/assessments", headers=under(alice, "nordlicht")).json()

    assert [a["thread_id"] for a in listed["assessments"]] == [thread_id]


def test_an_unknown_profile_is_404_before_any_stream(profiles, users):
    alice, _ = users
    with app_with(profiles) as client:
        response = client.post(
            "/api/assessments", json={"control_id": "4.1.2"}, headers=under(alice, "../nordlicht")
        )

    assert response.status_code == 404
    assert not response.headers["content-type"].startswith("text/event-stream")


def test_choosing_the_broken_profile_is_a_500_before_any_stream(profiles, users):
    alice, _ = users
    with app_with(profiles) as client:
        response = client.post(
            "/api/assessments", json={"control_id": "4.1.2"}, headers=under(alice, BROKEN)
        )

    assert response.status_code == 500
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert str(profiles) not in response.text
