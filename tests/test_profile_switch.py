"""The profile switcher's API (#47): the user's compliance profiles listed, a broken one left out
and noted for developers, and each request answered under the profile its X-Vouch-Profile
header names, so nothing is shared between profiles."""

import logging
import shutil
from pathlib import Path

import pytest
import test_memory
from fastapi.testclient import TestClient
from profiles_dir import BROKEN, SECOND, SECOND_COMPANY, make_profiles
from test_memory import as_user, assess, restart

from vouch import api
from vouch.api import PROFILE_HEADER, create_app, current_user
from vouch.chat_fake import CannedChatModel
from vouch.isa import BaseFrameworkError
from vouch.model_choice import ModelChoice
from vouch.profile import ProfileError, load_problem, read_profile_once
from vouch.settings import PROFILES_DIR_ENV, Settings

users = test_memory.users


@pytest.fixture
def profiles(tmp_path):
    """A profiles folder with nordlicht, testwerk and a broken one."""
    return make_profiles(tmp_path / "profiles")


def app_with(profiles_dir) -> TestClient:
    read_profile_once.cache_clear()
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
    with caplog.at_level(logging.WARNING, "vouch.profile"), app_with(profiles) as client:
        listed = client.get("/api/profiles", headers=alice)
        client.get("/api/profiles", headers=alice)
        developer = client.get("/api/developer/settings", headers=alice)

    assert listed.status_code == 200
    assert BROKEN not in [p["id"] for p in listed.json()["profiles"]]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and BROKEN in r.message]
    assert len(warnings) == 1  # read once, warned once, whatever the requests
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


def test_a_missing_profile_folder_stops_the_app_naming_the_setting(tmp_path):
    read_profile_once.cache_clear()
    with pytest.raises(SystemExit) as stop:
        create_app(Settings(profiles_dir=tmp_path / "nowhere"))

    assert PROFILES_DIR_ENV in str(stop.value.code)


def test_a_broken_default_profile_stops_the_app_naming_the_setting(tmp_path):
    make_profiles(tmp_path, broken=False)
    (tmp_path / "nordlicht" / "profile.yaml").write_text("id: [unclosed\n", encoding="utf-8")
    read_profile_once.cache_clear()
    with pytest.raises(SystemExit) as stop:
        create_app(Settings(profiles_dir=tmp_path))

    assert PROFILES_DIR_ENV in str(stop.value.code)
    assert "nordlicht" in str(stop.value.code)


def test_a_missing_evidence_folder_is_noted_without_the_servers_path(profiles, users):
    alice, _ = users
    shutil.rmtree(profiles / SECOND / "records")
    with app_with(profiles) as client:
        notes = client.get("/api/developer/settings", headers=alice).json()["broken_profiles"]

    problems = {note["id"]: note["problem"] for note in notes}
    assert set(problems) == {BROKEN, SECOND}
    assert "records" in problems[SECOND] and "does not exist" in problems[SECOND]
    assert str(profiles) not in problems[SECOND] and "/" not in problems[SECOND]


@pytest.mark.parametrize(
    "error",
    [
        ProfileError(
            Path("/srv/p/a/profile.yaml"),
            "evidence_sources.policies.folder: folder /srv/p/a/policies does not exist",
        ),
        BaseFrameworkError(Path("/srv/app/src/vouch/data/controls.yaml"), "not valid YAML"),
        ProfileError(
            Path("/srv/p/a/profile.yaml"),
            "cannot read base framework file /srv/app/src/vouch/data/controls.yaml: not valid YAML",
        ),
    ],
)
def test_every_load_problem_is_told_without_paths(error):
    problem = load_problem(error)

    assert "/srv" not in problem and "/" not in problem
    assert "policies" in problem or "controls.yaml" in problem


def test_the_default_comes_from_the_loaded_default_profile(profiles, users):
    alice, _ = users
    with app_with(profiles) as client:
        listed = client.get("/api/profiles", headers=alice).json()

    assert listed["default"] == "nordlicht"


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


def test_an_over_long_profile_name_is_404_not_500(profiles, users):
    alice, _ = users
    with app_with(profiles) as client:
        response = client.get("/api/overview", headers=under(alice, "a" * 300))

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("profile_id", "company", "language"),
    [("nordlicht", "Nordlicht Komponenten GmbH", "'en'"), (SECOND, SECOND_COMPANY, "'de'")],
)
def test_a_chat_builds_its_prompt_for_the_chosen_profile(
    profiles, users, monkeypatch, profile_id, company, language
):
    alice, _ = users
    fake = CannedChatModel()
    monkeypatch.setattr(ModelChoice, "for_chat", lambda self, chosen, callbacks=None: fake)
    with app_with(profiles) as client:
        response = client.post(
            "/api/chat",
            json={"message": "Where do I stand?", "page": "dossier"},
            headers=under(alice, profile_id),
        )

    assert response.status_code == 200, response.text
    system, _ = fake.prompts[0]
    assert company in system.content
    assert language in system.content
    other = SECOND_COMPANY if profile_id == "nordlicht" else "Nordlicht Komponenten GmbH"
    assert other not in system.content


def test_choosing_the_broken_profile_is_a_500_before_any_stream(profiles, users):
    alice, _ = users
    with app_with(profiles) as client:
        response = client.post(
            "/api/assessments", json={"control_id": "4.1.2"}, headers=under(alice, BROKEN)
        )

    assert response.status_code == 500
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert str(profiles) not in response.text


def test_each_profiles_working_paper_requirements_are_its_own_and_built_once(
    profiles, users, monkeypatch
):
    """The overview's requirements (#41) are kept per profile: switching back and forth never
    shows one profile's requirements under the other, nor builds them again."""
    second = profiles / SECOND / "profile.yaml"
    raised = second.read_text(encoding="utf-8").replace(
        "protection_level: normal", "protection_level: high"
    )
    second.write_text(raised, encoding="utf-8")
    builds = []
    real = api.requirements_by_control
    monkeypatch.setattr(
        api, "requirements_by_control", lambda p, c: builds.append(p.id) or real(p, c)
    )
    alice, _ = users
    with app_with(profiles) as client:
        shown = [
            client.get("/api/overview", headers=under(alice, profile_id)).json()["requirements"]
            for profile_id in ("nordlicht", SECOND, "nordlicht", SECOND)
        ]

    columns = [{r["column"] for rs in each.values() for r in rs} for each in shown]
    assert "high" not in columns[0] and "high" in columns[1]
    assert shown[0] == shown[2] and shown[1] == shown[3]
    assert sorted(builds) == ["nordlicht", SECOND]
