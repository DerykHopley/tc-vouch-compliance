"""The security guard's layers outside the model (#36), at the HTTP API: input limits, the tool
allow-list checked again outside the graph, and the filter on answers and logs."""

import io
import json
import logging
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import SecretStr

from vouch.api import compliance_profile, create_app, profile_tools
from vouch.fake_model import ScriptedChatModel
from vouch.graph import SUBMIT_ANSWER
from vouch.limits import MAX_MESSAGE_BYTES, RateLimit
from vouch.profile import NORDLICHT_PROFILE, load_profile
from vouch.redaction import install_log_redaction
from vouch.settings import Settings

DEMO_CONTROL = "4.1.2"


def read_events(response) -> list[tuple[str, dict]]:
    events = []
    for block in response.text.split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]
        if lines:
            fields = dict(line.split(": ", 1) for line in lines)
            events.append((fields["event"], json.loads(fields["data"])))
    return events


def guard_steps(events) -> list[str]:
    return [data["text"] for name, data in events if name == "step" and data["node"] == "guard"]


@pytest.fixture
def model_calls(monkeypatch) -> list[int]:
    """Counts the fake model's calls, across every model the app builds."""
    calls: list[int] = []
    generate = ScriptedChatModel._generate

    def counting(self, *args, **kwargs):
        calls.append(1)
        return generate(self, *args, **kwargs)

    monkeypatch.setattr(ScriptedChatModel, "_generate", counting)
    return calls


def client_for(settings: Settings | None = None) -> TestClient:
    return TestClient(create_app(settings or Settings()), raise_server_exceptions=False)


def write_script(tmp_path: Path, script: dict) -> Path:
    path = tmp_path / "script.json"
    path.write_text(json.dumps(script))
    return path


# Layer 1: input limits


def test_an_oversized_message_gets_413_and_no_model_call(model_calls):
    client = client_for()
    body = {"control_id": DEMO_CONTROL, "note": "x" * (MAX_MESSAGE_BYTES + 1)}

    response = client.post("/api/assessments", json=body)

    assert response.status_code == 413
    assert "too long" in response.json()["detail"]
    assert model_calls == []


def test_an_oversized_message_sent_in_chunks_gets_413_and_no_model_call(model_calls):
    """No Content-Length to go by: the body is counted as it arrives."""
    body = json.dumps({"control_id": DEMO_CONTROL, "note": "x" * MAX_MESSAGE_BYTES}).encode()
    chunks = (body[i : i + 1024] for i in range(0, len(body), 1024))

    response = client_for().post(
        "/api/assessments", content=chunks, headers={"content-type": "application/json"}
    )

    assert response.status_code == 413
    assert model_calls == []


def test_a_message_within_the_limit_still_runs(model_calls):
    response = client_for().post("/api/assessments", json={"control_id": DEMO_CONTROL})

    assert response.status_code == 200
    assert model_calls  # the model ran


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_burst_over_the_rate_gets_429_with_retry_after(model_calls):
    client = client_for()
    clock = Clock()
    client.app.state.rate_limit = RateLimit(requests=2, per_seconds=60, clock=clock)
    request = {"control_id": DEMO_CONTROL}

    first = client.post("/api/assessments", json=request)
    clock.now += 10
    second = client.post("/api/assessments", json=request)
    calls_before = len(model_calls)
    third = client.post("/api/assessments", json=request)

    assert (first.status_code, second.status_code) == (200, 200)
    assert third.status_code == 429
    assert third.headers["retry-after"] == "50"  # when the first request leaves the window
    assert "Too many requests" in third.json()["detail"]
    assert len(model_calls) == calls_before
    clock.now += 50
    assert client.post("/api/assessments", json=request).status_code == 200


# Layer 2: the allow-list, checked again at the API for every tool call


def test_a_tool_outside_the_allow_list_is_refused_when_the_model_asks_for_it(tmp_path):
    script = {
        "gather": [
            {"tool": "read_file", "args": {"path": "/etc/passwd"}},
            {"tool": "search_policies", "args": {"query": "password second factor"}},
            {"answer": "Passwords have at least 12 characters."},
        ],
        "judge": {"requirements": []},
    }
    client = client_for(Settings(fake_script=write_script(tmp_path, script)))

    events = read_events(client.post("/api/assessments", json={"control_id": DEMO_CONTROL}))

    assert guard_steps(events) == [
        "Refused read_file: it isn't on the compliance profile's allow-list."
    ]
    assert events[-1][0] == "result"  # the run goes on without it


def test_the_api_takes_its_allow_list_from_the_compliance_profile(tmp_path):
    """A profile that allows no records: the API refuses search_records before the graph does,
    so the one guard step is the API's."""
    script = {
        "gather": [
            {"tool": "search_records", "args": {"query": "access review"}},
            {"answer": "Nothing was searched."},
        ],
    }
    client = client_for(Settings(fake_script=write_script(tmp_path, script)))
    profile = load_profile(copy_profile(tmp_path, policies="policies"))
    client.app.dependency_overrides[compliance_profile] = lambda: profile

    events = read_events(client.post("/api/assessments", json={"control_id": DEMO_CONTROL}))

    assert guard_steps(events) == [
        "Refused search_records: it isn't on the compliance profile's allow-list."
    ]
    assert events[-1][0] == "result"


def test_the_api_refuses_a_tool_the_graph_would_run_once_the_allow_list_drops_it(monkeypatch):
    """The API's check stands on its own: here the graph would run the search, the API won't."""
    searches = []
    monkeypatch.setattr("vouch.graph.search_folder", lambda *a, **k: searches.append(a) or [])
    client = client_for()
    client.app.dependency_overrides[profile_tools] = lambda: frozenset({SUBMIT_ANSWER})

    events = read_events(client.post("/api/assessments", json={"control_id": DEMO_CONTROL}))

    assert searches == []
    assert guard_steps(events) == [
        "Refused search_policies: it isn't on the compliance profile's allow-list."
    ]
    assert events[-1][0] == "result"


# Layer 4: no secrets or personal data in answers or logs

PLANTED_KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"
PLANTED_EMAIL = "jane.doe@gmail.com"


@pytest.fixture
def server_log():
    """Everything logged at debug and up, through a root handler, redacted as a served app's
    handlers are (install_log_redaction wraps the handlers present when it runs)."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    install_log_redaction()
    yield stream
    root.removeHandler(handler)
    root.setLevel(level)


def copy_profile(tmp_path: Path, **sources: str) -> Path:
    """Nordlicht's profile and evidence copied to tmp_path/nordlicht, allowing only the
    sources named (name=folder)."""
    folder = tmp_path / "nordlicht"
    shutil.copytree(NORDLICHT_PROFILE.parent, folder)
    path = folder / "profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["evidence_sources"] = {name: {"folder": where} for name, where in sources.items()}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def planted_policies(tmp_path) -> Path:
    """A compliance profile whose one policy carries a key and a stranger's e-mail address."""
    path = copy_profile(tmp_path, policies="policies")
    folder = path.parent / "policies"
    shutil.rmtree(folder)
    folder.mkdir()
    (folder / "encryption-policy.md").write_text(
        '---\nid: encryption-policy\nkind: documented\nversion: "1.0"\n---\n'
        f"# Encryption {PLANTED_EMAIL}\n\n"
        f"Laptops are encrypted. The recovery service key is {PLANTED_KEY}; "
        f"questions to {PLANTED_EMAIL}.\n"
    )
    return path


def test_a_planted_key_and_email_in_a_tool_result_never_reach_the_answer_or_the_log(
    planted_policies, tmp_path, server_log
):
    """The fake model echoes the tool result back, as a real model might."""
    script = {
        "gather": [
            {"tool": "search_policies", "args": {"query": "encrypted"}},
            {"answer": f"Laptops are encrypted (key {PLANTED_KEY}, ask {PLANTED_EMAIL})."},
        ],
        "judge": {
            "requirements": [
                {
                    "requirement_id": "M1",
                    "verdict": "partly-evidenced",
                    "reason": f"Questions go to {PLANTED_EMAIL}.",
                    "citations": [
                        {
                            "doc_id": "encryption-policy",
                            "quote": "Laptops are encrypted. The recovery",
                        }
                    ],
                }
            ]
        },
    }
    client = client_for(Settings(fake_script=write_script(tmp_path, script)))
    profile = load_profile(planted_policies)
    client.app.dependency_overrides[compliance_profile] = lambda: profile

    response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    events = read_events(response)
    assert events[-1][0] == "result"
    assessment = events[-1][1]["assessment"]
    assert assessment["answer"] == "Laptops are encrypted (key [removed], ask [removed])."
    (m1,) = [r for r in assessment["requirements"] if r["id"] == "M1"]
    assert m1["reason"].startswith("Questions go to [removed].")
    assert m1["citations"][0]["section"] == "Encryption [removed]"
    requirement = next(data for name, data in events if name == "requirement")["requirement"]
    assert requirement == m1  # the requirement event is redacted as the result is
    for planted in (PLANTED_KEY, PLANTED_EMAIL):
        assert planted not in response.text
        assert planted not in server_log.getvalue()
    assert "Removed an e-mail address from the requirement." in guard_steps(events)
    # The requirements come before the answer in the Assessment, so the address is found first.
    assert "Removed an e-mail address and a key from the assessment." in guard_steps(events)


def test_a_planted_key_and_email_in_a_failure_never_reach_the_log(server_log, monkeypatch):
    def leaking_search(*args, **kwargs):
        raise RuntimeError(f"bad passage: {PLANTED_KEY} {PLANTED_EMAIL}")

    monkeypatch.setattr("vouch.graph.search_folder", leaking_search)

    response = client_for().post("/api/assessments", json={"control_id": DEMO_CONTROL})

    assert read_events(response)[-1][0] == "error"
    assert "bad passage" in server_log.getvalue()  # the failure is logged, its traceback too
    assert "Traceback" in server_log.getvalue()
    for planted in (PLANTED_KEY, PLANTED_EMAIL):
        assert planted not in server_log.getvalue()


def test_the_apps_own_secret_values_are_removed_whatever_their_shape(tmp_path):
    """A key with no known prefix is still removed: the app knows its own secrets."""
    odd_key = "Zq8-nothing-like-a-known-prefix-3141"
    script = {"gather": [{"answer": f"The key is {odd_key}."}]}
    settings = Settings(
        fake_script=write_script(tmp_path, script), openrouter_api_key=SecretStr(odd_key)
    )

    response = client_for(settings).post("/api/assessments", json={"control_id": DEMO_CONTROL})

    assert odd_key not in response.text
    assert read_events(response)[-1][1]["assessment"]["answer"] == "The key is [removed]."


def test_the_reviewed_assessment_is_redacted_as_the_stream_is(tmp_path):
    """The paused thread keeps the model's words as written; the review's answer removes them."""
    script = {"gather": [{"answer": f"Ask {PLANTED_EMAIL} about the key {PLANTED_KEY}."}]}
    client = client_for(Settings(fake_script=write_script(tmp_path, script)))
    events = read_events(client.post("/api/assessments", json={"control_id": DEMO_CONTROL}))
    thread_id = events[-1][1]["assessment"]["thread_id"]

    response = client.post(f"/api/assessments/{thread_id}/review", json={"decision": "accept"})

    assert response.status_code == 200
    assert response.json()["assessment"]["answer"] == "Ask [removed] about the key [removed]."
    for planted in (PLANTED_KEY, PLANTED_EMAIL):
        assert planted not in response.text


def test_with_no_rate_setting_the_61st_request_in_a_minute_gets_429():
    """The default rate (#138): unset, the limit is still 60 a minute."""
    client = client_for()
    answers = [client.get("/api/no-such-route").status_code for _ in range(61)]

    assert 429 not in answers[:60]
    assert answers[60] == 429


def test_the_rate_setting_sets_the_limit():
    client = client_for(Settings(rate_requests=3))
    answers = [client.get("/api/no-such-route").status_code for _ in range(4)]

    assert 429 not in answers[:3]
    assert answers[3] == 429
