import json
from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient

from vouch.api import app

DEMO_CONTROL = "NK-ENC-01"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ASSESS_FAKE_SCRIPT", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return TestClient(app, raise_server_exceptions=False)


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
    assert response.json() == {"status": "ok", "version": version("vouch")}


def test_demo_control_streams_steps_then_one_result_with_a_decided_verdict(client):
    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = read_events(response)
    names = [name for name, _ in events]
    assert names == ["step"] * (len(names) - 1) + ["result"]
    steps = [data for name, data in events if name == "step"]
    assert [s["node"] for s in steps] == ["gather", "tools", "gather", "cite", "judge"]
    assert all(set(s) == {"node", "text"} and s["text"] for s in steps)
    assert "encrypt" in steps[0]["text"]
    assessment = events[-1][1]["assessment"]
    assert assessment["control_id"] == DEMO_CONTROL
    assert assessment["verdict"] == "evidenced"
    assert assessment["answer"]
    assert "encryption-policy" in {s["doc_id"] for s in assessment["sources"]}


def test_a_gap_maps_to_not_evidenced(client, monkeypatch):
    monkeypatch.setenv("ASSESS_FAKE_SCRIPT", "tests/scripts/vul_gap.json")

    response = client.post("/api/assessments", json={"control_id": "NK-VUL-01"})

    events = read_events(response)
    assert events[-1][0] == "result"
    assert events[-1][1]["assessment"]["verdict"] == "not-evidenced"
    assert events[-2] == (
        "step",
        {"node": "escalate", "text": "Noted the gap for the Head of IT infrastructure."},
    )


def test_unknown_control_gets_404_and_no_stream(client):
    response = client.post("/api/assessments", json={"control_id": "NK-XXX-99"})

    assert response.status_code == 404
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert "NK-XXX-99" in response.json()["detail"]


@pytest.mark.parametrize("body", [{}, {"control_id": ""}, {"control_id": "   "}])
def test_empty_or_missing_control_id_gets_422(client, body):
    response = client.post("/api/assessments", json=body)

    assert response.status_code == 422


def test_a_broken_fake_script_ends_the_stream_with_one_error_event(client, monkeypatch, tmp_path):
    script = tmp_path / "broken.json"
    script.write_text('{"gather": [')
    monkeypatch.setenv("ASSESS_FAKE_SCRIPT", str(script))

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

    monkeypatch.setattr("vouch.graph.search_policies", broken_search)

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    events = read_events(response)
    names = [name for name, _ in events]
    assert names[-1] == "error" and names.count("error") == 1
    assert "result" not in names
    assert "disk on fire" not in response.text
    assert "Traceback" not in response.text
