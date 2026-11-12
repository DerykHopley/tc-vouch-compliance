"""The local app's fake model assesses 1.1.1, 1.6.2 and 5.1.1 with scripts of their own (#272),
as well as 4.1.2, under both bundled profiles: no key, no ASSESS_FAKE_SCRIPT, no paid call.

Through the HTTP API, as the Assess button and the chat start a run."""

import datetime
import json

import pytest
import test_memory
import yaml
from fastapi.testclient import TestClient
from test_api import read_events
from test_memory import as_user, restart

from vouch import graph
from vouch.api import PROFILE_HEADER, create_app, current_user
from vouch.fake_model import AnswerTurn, ScriptedChatModel
from vouch.graph import Judgement, RemediationPlan
from vouch.language import mismatch
from vouch.paths import FIXTURES_DIR
from vouch.profile import load_named_profile, read_profile_once
from vouch.settings import Settings, demo_script

users = test_memory.users

COVERED = ("1.1.1", "1.6.2", "5.1.1")
PROFILES = ("nordlicht", "alpenwerk")
CASES = [(profile, control) for profile in PROFILES for control in COVERED]
DROPPED = "Dropped a citation"  # a guard step's text (vouch.graph's Step.describe)
NOT_JUDGED = "Not judged"  # in the judge step's text when a requirement went unjudged


def expected_answers(profile: str) -> dict:
    return yaml.safe_load((FIXTURES_DIR / profile / "expected-answers.yaml").read_text())


def expected_verdicts(profile: str, control_id: str) -> dict[str, str]:
    """The profile's expected-answers.yaml verdict for each requirement of the control."""
    (control,) = [c for c in expected_answers(profile)["controls"] if c["id"] == control_id]
    return {r["id"]: r["verdict"] for r in control["requirements"]}


@pytest.fixture(autouse=True)
def as_of(monkeypatch) -> datetime.date:
    """Judges staleness on the expected answers' `as_of`, as they do, not on the real date: a
    document falling due later would otherwise change a verdict. The API passes assess no
    `today`, so vouch.graph's date is pinned (test_kind_and_stale passes `today` instead)."""
    (day,) = {expected_answers(profile)["as_of"] for profile in PROFILES}

    class AsOf(datetime.date):
        @classmethod
        def today(cls) -> "AsOf":
            return cls(day.year, day.month, day.day)

    monkeypatch.setattr(graph, "date", AsOf)
    return day


@pytest.fixture
def client(users):
    read_profile_once.cache_clear()
    restart()
    app = create_app(Settings())  # no key, no fake script: the bundled demo scripts
    app.dependency_overrides[current_user] = as_user
    with TestClient(app) as client:
        yield client


def assess(client: TestClient, headers: dict[str, str], profile: str, control_id: str):
    response = client.post(
        "/api/assessments",
        json={"control_id": control_id},
        headers=headers | {PROFILE_HEADER: profile},
    )
    assert response.status_code == 200, response.text
    return read_events(response)


@pytest.mark.parametrize("profile, control_id", CASES)
def test_a_covered_control_is_judged_in_full_by_its_own_script(client, users, profile, control_id):
    alice, _ = users

    events = assess(client, alice, profile, control_id)

    name, data = events[-1]
    assert name == "result", data
    assessment = data["assessment"]
    assert assessment["status"] == "waiting-for-review"
    steps = [data for name, data in events if name == "step"]
    (judge,) = [step for step in steps if step["node"] == "judge"]
    assert NOT_JUDGED not in judge["text"]
    assert not [step for step in steps if step["text"].startswith(DROPPED)]
    # The output guard keeps the sectioned answer as scripted, headings and bullets (#670).
    language = load_named_profile(profile).answer_language
    script = ScriptedChatModel.from_file(demo_script(language, control_id)).script
    (scripted,) = [t.answer for t in script.gather if isinstance(t, AnswerTurn)]
    assert assessment["answer"] == scripted
    verdicts = {r["id"]: r["verdict"] for r in assessment["requirements"]}
    assert verdicts == expected_verdicts(profile, control_id)
    judged = {data["requirement"]["id"] for name, data in events if name == "requirement"}
    assert judged == set(verdicts)


def test_alpenwerks_1_6_2_takes_the_overlay_branch_and_judges_o1(client, users):
    alice, _ = users

    events = assess(client, alice, "alpenwerk", "1.6.2")

    assert any(name == "step" and data["node"] == "overlay_branch" for name, data in events)
    assert any(name == "requirement" and data["requirement"]["id"] == "O1" for name, data in events)


def test_nordlichts_5_1_1_is_partly_evidenced_on_its_stale_encryption_standard(client, users):
    alice, _ = users

    events = assess(client, alice, "nordlicht", "5.1.1")

    requirements = events[-1][1]["assessment"]["requirements"]
    citing = [
        r for r in requirements if any(c["doc_id"] == "encryption-standard" for c in r["citations"])
    ]
    assert citing
    for requirement in citing:
        assert requirement["verdict"] == "partly-evidenced"
        stale = [c for c in requirement["citations"] if c["doc_id"] == "encryption-standard"]
        assert all(c["stale"] for c in stale)


def test_the_chats_assess_1_1_1_runs_its_own_script(client, users):
    alice, _ = users

    response = client.post(
        "/api/chat", json={"page": "dossier", "message": "Assess 1.1.1"}, headers=alice
    )
    events = read_events(response)

    steps = [data for name, data in events if name == "step"]
    (judge,) = [step for step in steps if step["node"] == "judge"]
    assert NOT_JUDGED not in judge["text"]
    (finished,) = [data for name, data in events if name == "assessment"]
    assert finished["control_id"] == "1.1.1"
    assert finished["status"] == "waiting-for-review"
    opened = client.get(f"/api/assessments/{finished['thread_id']}", headers=alice).json()
    verdicts = {r["id"]: r["verdict"] for r in opened["assessment"]["requirements"]}
    assert verdicts == expected_verdicts("nordlicht", "1.1.1")


@pytest.mark.parametrize("profile, control_id", CASES)
def test_a_covered_scripts_answer_and_reasons_are_in_the_profiles_language(profile, control_id):
    language = load_named_profile(profile).answer_language
    path = demo_script(language, control_id)
    script = ScriptedChatModel.from_file(path).script

    texts = [turn.answer for turn in script.gather if isinstance(turn, AnswerTurn)]
    texts += [
        r.reason for judgement in script.replies_for(Judgement) for r in judgement.requirements
    ]
    texts += [i.missing for plan in script.replies_for(RemediationPlan) for i in plan.items]
    for text in texts:
        assert mismatch(text, language) is None, (path.name, text)
    assert json.loads(path.read_text())  # plain JSON, as the CLI's ASSESS_FAKE_SCRIPT reads it
