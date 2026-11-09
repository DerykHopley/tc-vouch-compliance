"""The profile switcher's API (#47): the user's compliance profiles listed, a broken one left out
and noted for developers, and each request answered under the profile its X-Vouch-Profile
header names, so nothing is shared between profiles."""

import logging
import os
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
from vouch.paths import FIXTURES_DIR
from vouch.profile import (
    SAMPLE_LIBRARY,
    ProfileError,
    load_problem,
    read_profile_once,
    scan_profiles,
)
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


def test_the_default_profiles_folder_lists_both_companies_and_not_the_sample_library(users):
    """The sample library's reference profile is no company (#370)."""
    alice, _ = users
    with app_with(FIXTURES_DIR) as client:
        listed = client.get("/api/profiles", headers=alice).json()

    assert [p["id"] for p in listed["profiles"]] == ["alpenwerk", "nordlicht"]


def test_the_sample_library_is_404_before_any_stream(users):
    alice, _ = users
    with app_with(FIXTURES_DIR) as client:
        response = client.post(
            "/api/assessments", json={"control_id": "4.1.2"}, headers=under(alice, SAMPLE_LIBRARY)
        )

    assert response.status_code == 404
    assert not response.headers["content-type"].startswith("text/event-stream")


def test_the_eval_can_still_load_the_sample_library():
    assert scan_profiles(FIXTURES_DIR).find(SAMPLE_LIBRARY) is not None


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
    monkeypatch.setattr(ModelChoice, "for_chat", lambda self, chosen, callbacks=None, **_: fake)
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


# --- A profile file the loader can't read (#47's verify) --------------------------------------


def _not_utf8(path: Path) -> None:
    """The profile saved as Latin-1, as an editor on another system might: Prüfstand's ü is one
    byte that isn't UTF-8."""
    path.write_bytes(path.read_text(encoding="utf-8").encode("latin-1"))


def _unreadable(path: Path) -> None:
    path.chmod(0)


def _nested_too_deeply(path: Path) -> None:
    path.write_text("id: " + "[" * 5000 + "\n", encoding="utf-8")


def _evidence_folder_name_too_long(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("folder: records", "folder: " + "r" * 300), encoding="utf-8")


BAD_FILES = {
    "not-utf8": (_not_utf8, "UTF-8"),
    "unreadable": (_unreadable, "ermission"),
    "nested-too-deeply": (_nested_too_deeply, "nested"),
    "evidence-folder-name-too-long": (_evidence_folder_name_too_long, "evidence_sources.records"),
}


@pytest.mark.parametrize("case", BAD_FILES)
def test_a_profile_file_the_loader_cant_read_is_left_out_and_noted_path_free(
    profiles, users, caplog, case
):
    spoil, said = BAD_FILES[case]
    if case == "unreadable" and os.geteuid() == 0:
        pytest.skip("root reads any file")
    spoil(profiles / SECOND / "profile.yaml")
    alice, _ = users
    with caplog.at_level(logging.WARNING, "vouch.profile"), app_with(profiles) as client:
        listed = client.get("/api/profiles", headers=alice)
        overview = client.get("/api/overview", headers=alice)
        developer = client.get("/api/developer/settings", headers=alice)

    assert listed.status_code == 200, listed.text
    assert [p["id"] for p in listed.json()["profiles"]] == ["nordlicht"]
    assert overview.status_code == 200
    problems = {n["id"]: n["problem"] for n in developer.json()["broken_profiles"]}
    assert said in problems[SECOND]
    assert str(profiles) not in problems[SECOND] and "/" not in problems[SECOND]
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and SECOND in r.message]
    assert len(warned) == 1


def test_a_default_profile_that_isnt_utf8_stops_the_app_naming_the_setting(tmp_path):
    make_profiles(tmp_path, broken=False)
    nordlicht = tmp_path / "nordlicht" / "profile.yaml"
    text = nordlicht.read_text(encoding="utf-8").replace("GmbH", "GmbH für Bauteile")
    nordlicht.write_bytes(text.encode("latin-1"))
    read_profile_once.cache_clear()
    with pytest.raises(SystemExit) as stop:
        create_app(Settings(profiles_dir=tmp_path))

    assert PROFILES_DIR_ENV in str(stop.value.code)
    assert "UTF-8" in str(stop.value.code)


def _touched(path: Path, text: bytes, step: int) -> None:
    """Writes `text` and moves the file's time on, so each version has its own."""
    path.write_bytes(text)
    stamp = path.stat().st_mtime_ns + step * 1_000_000_000
    os.utime(path, ns=(stamp, stamp))


def test_a_broken_profile_fixed_while_the_app_runs_comes_back_warned_once_per_version(
    profiles, users, caplog
):
    alice, _ = users
    second = profiles / SECOND / "profile.yaml"
    good = second.read_bytes()
    _touched(second, b"id: [unclosed\n", 1)

    def listed(client: TestClient) -> list[str]:
        return [p["id"] for p in client.get("/api/profiles", headers=alice).json()["profiles"]]

    with caplog.at_level(logging.WARNING, "vouch.profile"), app_with(profiles) as client:
        broken = [listed(client), listed(client)]
        _touched(second, b"id: [still unclosed\n", 2)
        broken_again = [listed(client), listed(client)]
        _touched(second, good, 3)
        fixed = listed(client)

    assert broken == broken_again == [["nordlicht"], ["nordlicht"]]
    assert fixed == ["nordlicht", SECOND]
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and SECOND in r.message]
    assert len(warned) == 2  # once per broken version


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its permissions")
def test_a_profile_fixed_only_by_its_permissions_comes_back_on_the_next_scan(profiles, caplog):
    # #167: the cache was keyed on the file's time and size, which chmod changes neither of.
    read_profile_once.cache_clear()
    second = profiles / SECOND / "profile.yaml"
    mode = second.stat().st_mode

    with caplog.at_level(logging.WARNING, "vouch.profile"):
        second.chmod(0)
        try:
            unreadable = scan_profiles(profiles)
        finally:
            second.chmod(mode)
        readable = scan_profiles(profiles)
        again = scan_profiles(profiles)

    assert SECOND in [p.id for p in unreadable.broken]
    assert SECOND in [p.id for p in readable.loaded] == [p.id for p in again.loaded]
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and SECOND in r.message]
    assert len(warned) == 1  # warned once, for the unreadable version
