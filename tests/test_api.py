import json
from importlib.metadata import version
from pathlib import Path

import pytest
from assess_steps import in_order
from fastapi.testclient import TestClient

from vouch import ScriptedChatModel, profile
from vouch.api import cached_profile, create_app
from vouch.settings import Settings

DEMO_CONTROL = "4.1.2"


@pytest.fixture
def client():
    """The app in local mode on the bundled demo script, the profile read afresh."""
    cached_profile.cache_clear()
    yield TestClient(create_app(Settings()), raise_server_exceptions=False)
    cached_profile.cache_clear()


def client_playing(script) -> TestClient:
    """The app in local mode, its fake model playing `script`."""
    return TestClient(create_app(Settings(fake_script=Path(script))), raise_server_exceptions=False)


def test_a_broken_profile_is_a_500_before_any_stream(client, monkeypatch, tmp_path):
    (tmp_path / profile.DEFAULT_PROFILE).mkdir()
    (tmp_path / profile.DEFAULT_PROFILE / profile.PROFILE_FILE).write_text("id: [unclosed\n")
    monkeypatch.setattr(profile, "FIXTURES_DIR", tmp_path)

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    assert response.status_code == 500
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert response.json()["detail"].startswith("The assessment could not be completed.")
    assert str(tmp_path) not in response.text


def test_the_profile_is_read_once_across_requests(client, monkeypatch):
    reads = []
    real = profile.load_named_profile
    monkeypatch.setattr(
        "vouch.api.load_named_profile",
        lambda name, folder: reads.append(name) or real(name, folder),
    )

    for _ in range(2):
        client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    assert reads == [profile.DEFAULT_PROFILE]


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
        "auth": {"mode": "local", "publishable_key": None},
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
    assert all(set(s) == {"node", "text"} and s["text"] for s in steps)
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
    assert assessment["remediation"] == []  # nothing in the demo is not evidenced
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
