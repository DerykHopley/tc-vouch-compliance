"""The security guard's middleware layers (#36), at the HTTP API: input limits, the tool
allow-list checked again outside the graph, and the filter on answers and logs."""

import io
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vouch.api import create_app
from vouch.fake_model import ScriptedChatModel
from vouch.graph import SUBMIT_ANSWER
from vouch.middleware import MAX_MESSAGE_BYTES, RateLimit, redact
from vouch.settings import POLICY_DIR_ENV, Settings

DEMO_CONTROL = "NK-ENC-01"


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


def test_the_rate_is_counted_per_user():
    clock = Clock()
    limit = RateLimit(requests=1, per_seconds=60, clock=clock)

    assert limit.wait_for("lead") is None
    assert limit.wait_for("developer") is None
    assert limit.wait_for("lead") == 60


# Layer 2: the allow-list, checked again at the API for every tool call


def test_a_tool_outside_the_allow_list_is_refused_when_the_model_asks_for_it(tmp_path):
    script = {
        "gather": [
            {"tool": "read_file", "args": {"path": "/etc/passwd"}},
            {"tool": "search_policies", "args": {"query": "encrypt"}},
            {"answer": "Laptops are encrypted.", "cited": ["encryption-policy"]},
        ],
        "judge": {"covered_points": ["ENC-1"]},
    }
    client = client_for(Settings(fake_script=write_script(tmp_path, script)))

    events = read_events(client.post("/api/assessments", json={"control_id": DEMO_CONTROL}))

    assert guard_steps(events) == [
        "Refused read_file: it isn't on the compliance profile's allow-list."
    ]
    assert events[-1][0] == "result"  # the run goes on without it


def test_the_api_refuses_a_tool_the_graph_would_run_once_the_allow_list_drops_it(monkeypatch):
    """The API's check stands on its own: here the graph would run the search, the API won't."""
    searches = []
    monkeypatch.setattr("vouch.graph.search_policies", lambda *a, **k: searches.append(a) or [])
    client = client_for()
    client.app.state.allowed_tools = frozenset({SUBMIT_ANSWER})

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
def planted_policies(tmp_path, monkeypatch) -> Path:
    """A policy folder whose one document carries a key and a stranger's e-mail address."""
    folder = tmp_path / "policies"
    folder.mkdir()
    (folder / "encryption-policy.md").write_text(
        f"# Encryption {PLANTED_EMAIL}\n\n"
        f"Laptops are encrypted. The recovery service key is {PLANTED_KEY}; "
        f"questions to {PLANTED_EMAIL}.\n"
    )
    monkeypatch.setenv(POLICY_DIR_ENV, str(folder))
    return folder


def test_a_planted_key_and_email_in_a_tool_result_never_reach_the_answer_or_the_log(
    planted_policies, tmp_path, caplog
):
    """The fake model echoes the tool result back, as a real model might."""
    script = {
        "gather": [
            {"tool": "search_policies", "args": {"query": "encrypted"}},
            {
                "answer": f"Laptops are encrypted (key {PLANTED_KEY}, ask {PLANTED_EMAIL}).",
                "cited": ["encryption-policy"],
            },
        ],
        "judge": {"covered_points": ["ENC-1"]},
    }
    client = client_for(Settings(fake_script=write_script(tmp_path, script)))

    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL})

    events = read_events(response)
    assert events[-1][0] == "result"
    assessment = events[-1][1]["assessment"]
    assert assessment["answer"] == "Laptops are encrypted (key [removed], ask [removed])."
    assert assessment["sources"] == [
        {"doc_id": "encryption-policy", "heading": "Encryption [removed]"}
    ]
    for planted in (PLANTED_KEY, PLANTED_EMAIL):
        assert planted not in response.text
        assert planted not in caplog.text
    assert "Removed a key and an e-mail address from the assessment." in guard_steps(events)


def test_a_planted_key_and_email_in_a_failure_never_reach_the_log(client_log, monkeypatch):
    def leaking_search(*args, **kwargs):
        raise RuntimeError(f"bad passage: {PLANTED_KEY} {PLANTED_EMAIL}")

    monkeypatch.setattr("vouch.graph.search_policies", leaking_search)

    response = client_for().post("/api/assessments", json={"control_id": DEMO_CONTROL})

    assert read_events(response)[-1][0] == "error"
    assert "bad passage" in client_log.getvalue()  # the failure is logged, its traceback too
    assert "Traceback" in client_log.getvalue()
    for planted in (PLANTED_KEY, PLANTED_EMAIL):
        assert planted not in client_log.getvalue()


def test_any_logger_record_is_filtered_even_a_libraries_debug_line(client_log):
    """A model client logging its request at debug (the tool results inside) is filtered too."""
    client_for()  # the app installs the filter
    logging.getLogger("openai._base_client").debug(
        "Request options: %s", {"headers": {"Authorization": f"Bearer {PLANTED_KEY}"}}
    )

    assert "Request options" in client_log.getvalue()
    assert PLANTED_KEY not in client_log.getvalue()


@pytest.fixture
def client_log():
    """Everything logged at debug and up, as a plain handler on the root logger writes it."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield stream
    root.removeHandler(handler)
    root.setLevel(level)


@pytest.mark.parametrize(
    ("text", "kinds"),
    [
        ("key sk-or-v1-abcdefghijklmnop0123", ["a key"]),
        ("key sk_live_51Habcdefghijklmnop", ["a key"]),
        ("key pk_test_Y2xlcmsuZXhhbXBsZS5jb20k", ["a key"]),
        ("Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1In0.c2ln", ["a bearer token"]),
        ("write to jane.doe@gmail.com", ["an e-mail address"]),
        ("call +49 30 12345678", ["a phone number"]),
        ("call 0049 (30) 1234-5678", ["a phone number"]),
        ("call 030 1234567", ["a phone number"]),
        ("call 0171/1234567", ["a phone number"]),
    ],
)
def test_redact_removes_secrets_and_personal_data(text, kinds):
    clean, removed = redact(text)

    assert removed == kinds
    assert "[removed]" in clean


@pytest.mark.parametrize(
    "text",
    [
        "Report to security@nordlicht.example, the profile's own address.",
        "Reviewed on 2026-11-04 for NK-ENC-01 under ISA 5.2.4, version 2.1.0.",
        "The task-force asks the security hotline on extension 4400.",
        "Disks use AES-256 and keys rotate every 90 days; risk-based, see sk-1.",
        "Bearer of the badge must show it.",
    ],
)
def test_redact_keeps_ordinary_compliance_text(text):
    assert redact(text) == (text, [])
