"""The demo round seed (#579, the owner's option C): a lead's work played through the app's own
HTTP API on the fake model, so Company › Rounds opens on a closed "Q2 2026" and a clear
improvement since.

In process, on both backends (memory, and Postgres under `scripts/test-db.sh`); then the
command's refusals: no target, a database without a user, a preview that isn't on this machine.
"""

import os
import subprocess
import sys
import uuid
from typing import Any

import httpx
import pytest
import test_memory
from fastapi.testclient import TestClient
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture)

from vouch import demo_round
from vouch.demo_round import ROUND_NAME, AlreadySeeded, NotFresh, seed, seeding_app

database_url = test_memory.database_url
postgres = test_memory.postgres


@pytest.fixture
def user() -> str:
    """A user new to this test (the in-memory backend is one per process)."""
    return f"user_seed{uuid.uuid4().hex[:8]}"


def client_for(url: Any, user: str) -> TestClient:
    return TestClient(seeding_app(url, user, "Demo lead"))


def comparison(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    listed = client.get("/api/rounds").json()
    number = listed["default"]
    shown = client.get(f"/api/rounds/{number}/comparison")
    assert shown.status_code == 200, shown.text
    return listed, shown.json()


def test_the_seed_closes_q2_2026_and_the_comparison_opens_on_a_clear_improvement(
    database_url, user
):
    with client_for(database_url, user) as client:
        seeded = seed(client)
        listed, compared = comparison(client)

    names = [(r["name"], r["closed_at"] is not None) for r in listed["rounds"]]
    assert names == [("First round", True), (ROUND_NAME, True), ("Q3 2026", False)]
    assert listed["rounds"][listed["default"] - 1]["name"] == ROUND_NAME
    better = {
        c["control_id"]: (c["before"], c["after"])
        for c in compared["controls"]
        if c["change"] == "better"
    }
    assert sorted(better.values()) == [
        ("not-evidenced", "evidenced"),
        ("not-evidenced", "evidenced"),
        ("partly-evidenced", "evidenced"),
    ]
    assert set(better) == set(seeded.improved)
    assert compared["changes"]["worse"] == 0
    assert compared["changes"]["new"] == 0
    assert [d["control_id"] for d in compared["remediation_done"]] == [seeded.followed_up]
    assert [q["control_id"] for q in compared["questions_answered"]] == [seeded.followed_up]
    assert (
        compared["readiness"]["by_verdict"]["evidenced"]["now"]
        == compared["readiness"]["by_verdict"]["evidenced"]["then"] + 3
    )


def test_a_second_run_stops_and_writes_nothing(database_url, user):
    with client_for(database_url, user) as client:
        seed(client)
        before = client.get("/api/rounds").json()
        with pytest.raises(AlreadySeeded):
            seed(client)
        after = client.get("/api/rounds").json()

    assert after == before


def test_a_user_with_work_since_the_fresh_start_is_refused_with_nothing_written(database_url, user):
    with client_for(database_url, user) as client:
        test_memory.assess(client, {}, "4.1.2")
        with pytest.raises(NotFresh):
            seed(client)
        listed = client.get("/api/rounds").json()

    assert [r["name"] for r in listed["rounds"]] == ["First round"]


def test_after_start_afresh_the_seed_runs_again_and_its_round_is_choosable(database_url, user):
    with client_for(database_url, user) as client:
        seed(client)
        assert client.post("/api/progress/fresh-start").status_code == 200
        seed(client)
        listed, compared = comparison(client)

    chosen = listed["rounds"][listed["default"] - 1]
    assert chosen["name"] == ROUND_NAME and not chosen["before_fresh_start"]
    assert compared["changes"]["better"] == 3


def test_the_seeding_app_runs_on_the_fake_model_even_with_a_key_in_the_environment(
    monkeypatch, user
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-not-used")
    monkeypatch.setenv("ASSESS_MODEL", "some/paid-model")

    settings = seeding_app(None, user, "Demo lead").state.settings

    assert settings.openrouter_api_key is None
    assert settings.model is None
    assert settings.fake_script is None
    assert settings.langfuse is None


class FailingDone:
    """A client whose remediation-done call fails, as an interrupted run's would."""

    def __init__(self, client: TestClient, error: type[BaseException] | None = None) -> None:
        self.client = client
        self.error = error

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        if url.endswith("/done"):
            if self.error is not None:
                raise self.error()
            return httpx.Response(500, text="broken")
        return self.client.request(method, url, **kwargs)


@pytest.mark.parametrize("error", [None, KeyboardInterrupt])
def test_the_goal_is_put_back_when_marking_done_fails(user, error):
    with client_for(None, user) as client:
        with pytest.raises(demo_round.SeedError if error is None else error):
            seed(FailingDone(client, error))
        goal = client.get("/api/overview").json()["goal"]

    assert goal == "audit-readiness"


class Answers:
    """A client answering each request with the next of `responses`."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        return self.responses.pop(0)


def test_a_request_rate_429_is_waited_out(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(demo_round.time, "sleep", waits.append)
    rate = httpx.Response(429, headers={"Retry-After": "2"}, json={"detail": "Too many"})

    answered = demo_round._call(Answers(rate, httpx.Response(200, json={})), "GET", "/api/x")

    assert answered.status_code == 200
    assert waits == [2.0]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, headers={"Retry-After": "30000"}, json={"limit": "profile-pass"}),
        httpx.Response(429, headers={"Retry-After": "30000"}, json={"limit": "daily"}),
        httpx.Response(429, headers={"Retry-After": "7200"}, json={"detail": "Too many"}),
    ],
)
def test_a_limit_429_stops_at_once_without_waiting(monkeypatch, response):
    waits: list[float] = []
    monkeypatch.setattr(demo_round.time, "sleep", waits.append)

    with pytest.raises(demo_round.SeedError, match="limit"):
        demo_round._call(Answers(response), "POST", "/api/profile-pass")

    assert waits == []


# --- The command -------------------------------------------------------------------------------


def run(argv: list[str], env: dict[str, str], capsys) -> tuple[int, str]:
    code = demo_round.main(argv, env)
    return code, capsys.readouterr().err


def test_the_command_refuses_without_a_target_and_says_what_it_needs(capsys):
    code, said = run([], {}, capsys)

    assert code == 2
    assert "VOUCH_DATABASE_URL" in said and "--api" in said


def test_the_command_refuses_a_database_without_a_user(capsys):
    code, said = run([], {"VOUCH_DATABASE_URL": "postgresql://x@127.0.0.1:1/x"}, capsys)

    assert code == 2
    assert "--user" in said


def test_the_command_refuses_a_user_id_that_isnt_clerks(capsys):
    env = {"VOUCH_DATABASE_URL": "postgresql://x@127.0.0.1:1/x"}

    code, said = run(["--user", "alice"], env, capsys)

    assert code == 2
    assert "user_" in said


@pytest.mark.parametrize("api", ["https://vouch.example.com", "http://10.0.0.5:8000", "ftp://x"])
def test_the_command_refuses_a_preview_not_on_this_machine(api, capsys):
    code, said = run(["--api", api], {}, capsys)

    assert code == 2
    assert "127.0.0.1" in said


def test_the_command_refuses_both_targets_at_once(capsys):
    env = {"VOUCH_DATABASE_URL": "postgresql://x@127.0.0.1:1/x"}

    code, said = run(["--api", "http://127.0.0.1:8010"], env, capsys)

    assert code == 2
    assert "one target" in said


@needs_postgres
def test_the_command_seeds_a_database_once(postgres, user, capsys):
    env = {"VOUCH_DATABASE_URL": postgres.url.get_secret_value()}

    first, _ = run(["--user", user], env, capsys)
    second, said = run(["--user", user], env, capsys)

    assert first == 0
    assert second == 1
    assert ROUND_NAME in said
    with client_for(postgres.url, user) as client:
        names = [r["name"] for r in client.get("/api/rounds").json()["rounds"]]
    assert names.count(ROUND_NAME) == 1


def test_the_command_starts_in_a_shell_with_clerk_keys_and_no_auth_mode():
    """The owner's .envrc loads the Clerk keys without VOUCH_AUTH_MODE, with which vouch.api's
    own app refuses to start on import; the command starts, and says what it needs."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VOUCH_", "CLERK_"))}
    env |= {"CLERK_SECRET_KEY": "sk_test_unused", "OPENROUTER_API_KEY": "sk-unused"}

    done = subprocess.run(
        [sys.executable, "-m", "vouch.demo_round"],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert done.returncode == 2, done.stderr
    assert "needs one target" in done.stderr
