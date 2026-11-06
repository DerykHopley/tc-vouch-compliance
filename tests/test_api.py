import json
import shutil
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import pytest
from assess_steps import in_order
from fastapi.testclient import TestClient
from isa_editions import GERMAN_5_1_1

from vouch import ScriptedChatModel, isa, profile
from vouch.api import PROFILE_HEADER, create_app
from vouch.paths import FIXTURES_DIR
from vouch.profile import read_profile_once
from vouch.settings import Settings

DEMO_CONTROL = "4.1.2"


@pytest.fixture
def client():
    """The app in local mode on the bundled demo script, the profile read afresh."""
    read_profile_once.cache_clear()
    yield TestClient(create_app(Settings()), raise_server_exceptions=False)
    read_profile_once.cache_clear()


def client_playing(script) -> TestClient:
    """The app in local mode, its fake model playing `script`."""
    return TestClient(create_app(Settings(fake_script=Path(script))), raise_server_exceptions=False)


def test_a_broken_profile_is_a_500_before_any_stream(tmp_path):
    shutil.copytree(FIXTURES_DIR / profile.DEFAULT_PROFILE, tmp_path / profile.DEFAULT_PROFILE)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / profile.PROFILE_FILE).write_text("id: [unclosed\n")
    read_profile_once.cache_clear()
    client = TestClient(create_app(Settings(profiles_dir=tmp_path)), raise_server_exceptions=False)

    response = client.post(
        "/api/assessments", json={"control_id": DEMO_CONTROL}, headers={PROFILE_HEADER: "broken"}
    )

    assert response.status_code == 500
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert response.json()["detail"].startswith("The assessment could not be completed.")
    assert str(tmp_path) not in response.text


def test_the_profile_is_read_once_across_requests(client, monkeypatch):
    reads = []
    real = profile.load_named_profile
    monkeypatch.setattr(
        profile, "load_named_profile", lambda name, folder: reads.append(name) or real(name, folder)
    )
    read_profile_once.cache_clear()

    for _ in range(2):
        client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    # each profile in the folder once (the switcher's scan reads Alpenwerk too, #49), not per request
    assert profile.DEFAULT_PROFILE in reads and len(reads) == len(set(reads))


def read_events(response) -> list[tuple[str, dict]]:
    """The stream's events as (event name, decoded data), in order."""
    events = []
    for block in response.text.split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]
        if not lines:
            continue
        fields = dict(line.split(": ", 1) for line in lines)
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def test_health_answers_ok_with_the_version(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": version("vouch"),
        "auth": {"mode": "local", "publishable_key": None, "gate": "none"},
    }


def test_demo_control_streams_steps_then_requirement_events_then_waits_for_review(client):
    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = read_events(response)
    names = [name for name, _ in events]
    first = names.index("requirement")
    assert set(names[:first]) == {"step"}
    assert names[first : first + 5] == ["requirement"] * 5
    assert set(names[first + 5 : -1]) == {"step"} and names[-1] == "result"
    steps = [data for name, data in events if name == "step"]
    assert events[0] == ("step", steps[0]) and steps[0]["node"] == "load_control"
    assert steps[0]["text"].startswith(f"Loaded control {DEMO_CONTROL}: ")
    assert "5 requirements apply" in steps[0]["text"]
    assert in_order(
        [s["node"] for s in steps],
        "gather", "tools", "gather", "check_answer", "judge", "check_quotes", "remediate", "review",
    )  # fmt: skip
    assert all(set(s) - {"quote"} == {"node", "text"} and s["text"] for s in steps)  # quote: #244
    assert next(s["text"] for s in steps if s["node"] == "judge") == (
        "Judged 4 of 5 requirements evidenced."
    )
    assert steps[-1]["text"] == "Waiting for the compliance lead's review."
    requirements = [data["requirement"] for name, data in events if name == "requirement"]
    assert [(r["id"], r["verdict"]) for r in requirements] == [
        ("M1", "partly-evidenced"),
        ("M2", "evidenced"),
        ("S1", "evidenced"),
        ("S2", "evidenced"),
        ("S3", "evidenced"),
    ]
    assert all(
        set(r) == {"id", "column", "text", "verdict", "reason", "citations"} for r in requirements
    )
    assert requirements[3]["citations"] == [
        {
            "doc_id": "authentication-standard",
            "version": "3.1",
            "section": "Passwords",
            "quote": "Passwords have at least 12 characters.",
            "kind": "documented",
            "stale": False,
            "stale_because": None,
        }
    ]
    assessment = events[-1][1]["assessment"]
    assert assessment["control_id"] == DEMO_CONTROL
    assert assessment["profile_id"] == "nordlicht"
    assert assessment["verdict"] == "partly-evidenced"
    assert assessment["should_score"] == {"evidenced": 3, "total": 3}
    assert assessment["requirements"] == requirements
    assert assessment["steps"] == steps
    assert assessment["answer"]
    assert "sources" not in assessment  # citations sit on each requirement (#21)
    assert assessment["status"] == "waiting-for-review"
    assert assessment["thread_id"]
    # M1 is partly evidenced, so it gets a draft (#212); the evidenced ones get none.
    assert [item["requirement_id"] for item in assessment["remediation"]] == ["M1"]
    assert assessment["review"] is None


def proposed_thread(client) -> str:
    events = read_events(client.post("/api/assessments", json={"control_id": DEMO_CONTROL}))
    return events[-1][1]["assessment"]["thread_id"]


def test_the_review_endpoint_resumes_with_an_override_and_returns_the_reviewed_assessment(client):
    thread_id = proposed_thread(client)

    response = client.post(
        f"/api/assessments/{thread_id}/review",
        json={
            "decision": "accept",
            "note": "Checked against the records.",
            "overrides": [
                {
                    "requirement_id": "M1",
                    "verdict": "evidenced",
                    "reason": "The exposure assessment is in the records.",
                }
            ],
        },
    )

    assert response.status_code == 200
    reviewed = response.json()["assessment"]
    assert reviewed["thread_id"] == thread_id
    assert reviewed["status"] == "accepted"
    assert reviewed["verdict"] == "evidenced"  # partly-evidenced before the override
    assert reviewed["review"]["note"] == "Checked against the records."
    assert reviewed["review"]["overrides"][0]["agent_verdict"] == "partly-evidenced"


def test_reviewing_a_thread_that_is_not_waiting_is_409(client):
    thread_id = proposed_thread(client)
    first = client.post(f"/api/assessments/{thread_id}/review", json={"decision": "reject"})

    again = client.post(f"/api/assessments/{thread_id}/review", json={"decision": "accept"})

    assert first.status_code == 200
    assert first.json()["assessment"]["status"] == "rejected"
    assert again.status_code == 409
    assert "not waiting for review" in again.json()["detail"]


def test_reviewing_an_unknown_thread_is_404(client):
    response = client.post("/api/assessments/no-such-thread/review", json={"decision": "accept"})

    assert response.status_code == 404
    assert "no-such-thread" in response.json()["detail"]


def test_review_needs_no_model_script(client, tmp_path):
    thread_id = proposed_thread(client)
    broken = tmp_path / "broken.json"
    broken.write_text('{"gather": [')
    # Another app on a broken script: the paused thread waits in the process's checkpointer.
    reviewer = client_playing(broken)

    response = reviewer.post(f"/api/assessments/{thread_id}/review", json={"decision": "accept"})

    assert response.status_code == 200
    assert response.json()["assessment"]["model"] == "scripted-fake"


def test_assessing_again_after_reject_carries_the_note_to_the_model(client, monkeypatch):
    prompts = []
    real = ScriptedChatModel._generate

    def recording(self, messages, *args, **kwargs):
        prompts.append(messages)
        return real(self, messages, *args, **kwargs)

    monkeypatch.setattr(ScriptedChatModel, "_generate", recording)
    note = "The exposure assessment is in the records folder."
    thread_id = proposed_thread(client)
    client.post(f"/api/assessments/{thread_id}/review", json={"decision": "reject", "note": note})
    prompts.clear()

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL, "note": note})

    events = read_events(response)
    assert events[-1][0] == "result"
    assert events[-1][1]["assessment"]["thread_id"] != thread_id
    assert note in prompts[0][-1].content


def recorded_prompts(monkeypatch) -> list:
    """Every list of messages the fake model is sent from now on."""
    prompts: list = []
    real = ScriptedChatModel._generate

    def recording(self, messages, *args, **kwargs):
        prompts.append(messages)
        return real(self, messages, *args, **kwargs)

    monkeypatch.setattr(ScriptedChatModel, "_generate", recording)
    return prompts


def test_assessing_a_rejected_control_again_without_a_note_carries_the_rejection_note(
    client, monkeypatch
):
    # Every way to start the run (the Dossier, the paper, the chat) behaves the same: the note
    # comes from the control's newest assessment, not from the caller (#42).
    prompts = recorded_prompts(monkeypatch)
    note = "The MFA rollout record is in records/, dated 2025."
    thread_id = proposed_thread(client)
    client.post(f"/api/assessments/{thread_id}/review", json={"decision": "reject", "note": note})
    prompts.clear()

    events = read_events(client.post("/api/assessments", json={"control_id": DEMO_CONTROL}))

    assert events[-1][0] == "result"
    assert note in prompts[0][-1].content


def test_a_new_run_carries_no_note_when_the_newest_assessment_was_not_rejected(client, monkeypatch):
    prompts = recorded_prompts(monkeypatch)
    rejected = proposed_thread(client)
    old = "An old rejection, since accepted."
    client.post(f"/api/assessments/{rejected}/review", json={"decision": "reject", "note": old})
    accepted = proposed_thread(client)
    client.post(f"/api/assessments/{accepted}/review", json={"decision": "accept", "note": "Ok."})
    prompts.clear()

    read_events(client.post("/api/assessments", json={"control_id": DEMO_CONTROL}))

    assert all(old not in str(m.content) for messages in prompts for m in messages)


def test_a_stored_review_keeps_who_reviewed_it_and_when(client):
    thread_id = proposed_thread(client)
    reviewed = client.post(f"/api/assessments/{thread_id}/review", json={"decision": "reject"})

    stored = client.get(f"/api/assessments/{thread_id}").json()["assessment"]["review"]

    assert stored["reviewer_id"] == "local-user"
    assert stored["reviewer_name"] == "Local user"
    assert datetime.fromisoformat(stored["reviewed_at"]).tzinfo is not None
    assert reviewed.json()["assessment"]["review"] == stored


@pytest.mark.parametrize(
    "override",
    [
        {"requirement_id": "M1", "verdict": "evidenced"},
        {"requirement_id": "M1", "verdict": "evidenced", "reason": " "},
        {"requirement_id": "M9", "verdict": "evidenced", "reason": "Shown."},
        {"requirement_id": "M1", "verdict": "compliant", "reason": "Shown."},
    ],
    ids=["no-reason", "blank-reason", "unknown-requirement", "invalid-verdict"],
)
def test_a_bad_override_is_422_and_the_thread_still_waits(client, override):
    thread_id = proposed_thread(client)
    url = f"/api/assessments/{thread_id}/review"

    response = client.post(url, json={"decision": "accept", "overrides": [override]})

    assert response.status_code == 422
    assert client.post(url, json={"decision": "accept"}).status_code == 200


def test_an_answer_claiming_an_audit_result_twice_ends_with_one_error_event(tmp_path):
    script = tmp_path / "compliant.json"
    script.write_text('{"gather": [{"answer": "We are compliant."}, {"answer": "Compliant."}]}')
    client = client_playing(script)

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    names = [name for name, _ in read_events(response)]
    assert names[-1] == "error"
    assert names.count("error") == 1
    assert "result" not in names
    assert "requirement" not in names


def test_unknown_control_gets_404_and_no_stream(client):
    response = client.post("/api/assessments", json={"control_id": "9.9.9"})

    assert response.status_code == 404
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert "9.9.9" in response.json()["detail"]


@pytest.mark.parametrize("body", [{}, {"control_id": ""}, {"control_id": "   "}])
def test_empty_or_missing_control_id_gets_422(client, body):
    response = client.post("/api/assessments", json=body)

    assert response.status_code == 422


def test_a_broken_fake_script_ends_the_stream_with_one_error_event(tmp_path):
    script = tmp_path / "broken.json"
    script.write_text('{"gather": [')
    client = client_playing(script)

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    assert response.status_code == 200
    events = read_events(response)
    assert [name for name, _ in events] == ["error"]
    message = events[0][1]["message"]
    assert message.startswith("The assessment could not be completed.")
    assert "Traceback" not in response.text
    assert str(script) not in response.text  # no server paths leak to the browser


def test_a_tool_failure_mid_run_ends_with_one_error_event(client, monkeypatch):
    def broken_search(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("vouch.graph.search_folder", broken_search)

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    events = read_events(response)
    names = [name for name, _ in events]
    assert names[-1] == "error" and names.count("error") == 1
    assert "result" not in names
    assert "disk on fire" not in response.text
    assert "Traceback" not in response.text


# ISA edition -----------------------------------------------------------------------------------


@pytest.fixture
def german_default_profile(client):
    from vouch.api import compliance_profile

    default = profile.load_named_profile(profile.DEFAULT_PROFILE)
    german = default.model_copy(update={"answer_language": "de"})
    client.app.dependency_overrides[compliance_profile] = lambda: german
    yield german
    client.app.dependency_overrides.pop(compliance_profile)


def test_a_german_profile_is_assessed_against_the_german_isa_text(client, german_default_profile):
    response = client.post("/api/assessments", json={"control_id": "5.1.1"})

    steps = [data for name, data in read_events(response) if name == "step"]
    assert steps[0]["text"].startswith(f"Loaded control 5.1.1: {GERMAN_5_1_1}")


def test_a_broken_german_edition_is_a_500_before_any_stream(
    client, german_default_profile, monkeypatch, tmp_path
):
    broken = tmp_path / "controls-de.yaml"
    broken.write_text("controls: [\n", encoding="utf-8")
    monkeypatch.setattr(isa, "CONTROLS_DE_FILE", broken)

    response = client.post("/api/assessments", json={"control_id": "5.1.1"})

    assert response.status_code == 500
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert response.json()["detail"].startswith("The assessment could not be completed.")
    assert str(tmp_path) not in response.text
