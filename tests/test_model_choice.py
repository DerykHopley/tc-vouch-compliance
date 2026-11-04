"""The real model and the developer page's API (#34), through the HTTP API.

No network and no paid call: OpenRouter is a fake on both of the app's connections to it. The
chat model is langchain-openai's real ChatOpenAI, its HTTP client (httpx2, the openai SDK's)
on a MockTransport that answers as OpenRouter's chat completions do and records each request.
The model list (GET /models, where an id is checked) is a fake behind httpx's MockTransport.
"""

import json
import logging
import time
from typing import Any

import httpx
import httpx2
import openai._base_client
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_auth import FakeClerk, bearer, clerk_app, token

from vouch import model_choice
from vouch.api import create_app
from vouch.database import Database
from vouch.graph import PROMPT_VERSION
from vouch.model_choice import (
    DEFAULT_MODEL,
    MAX_RETRIES,
    USAGE_URL,
    MemoryModelStore,
    ModelChoice,
    OpenRouter,
)
from vouch.settings import Settings

# langchain-openai probes which TCP keep-alive options the OS takes by opening (never
# connecting) a socket when it builds ChatOpenAI; pytest-socket warns. A real connection would
# still be refused, and fail the test.
pytestmark = pytest.mark.filterwarnings("ignore:A test tried to use socket.socket")

KEY = "sk-or-v1-test-key-34"
CONTROL = "NK-ENC-01"
OTHER_MODEL = "anthropic/claude-sonnet-5.5"
UNREACHABLE_DB = "postgresql://vouch:pw@/vouch?host=/nonexistent-34"


def completion(model: str, message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "gen-34",
        "object": "chat.completion",
        "created": 1_762_000_000,
        "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
    }


class FakeChat:
    """OpenRouter's POST /chat/completions: the answer turn for the gather step, and the
    judge's structured output (a `response_format` request) covering every point."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.fail: Any = None  # a function of the attempt number: a Response, or it raises

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.fail is not None:
            failed = self.fail(len(self.requests), request)
            if failed is not None:
                return failed
        body = json.loads(request.content)
        if "response_format" in body:
            judged = {"covered_points": ["ENC-1", "ENC-2", "ENC-3"]}
            message = {"role": "assistant", "content": json.dumps(judged)}
        else:
            args = {"answer": "Laptops use full-disk encryption.", "cited": []}
            call = {
                "id": "call_1",
                "type": "function",
                "function": {"name": "submit_answer", "arguments": json.dumps(args)},
            }
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
        return httpx2.Response(200, json=completion(body["model"], message))

    @property
    def models(self) -> list[str]:
        return [json.loads(r.content)["model"] for r in self.requests]


class FakeCatalogue:
    """OpenRouter's GET /models, in pages as its `links.next` says."""

    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        self.calls: list[httpx.Request] = []
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail:
            raise httpx.ConnectError("no route to OpenRouter", request=request)
        offset = int(request.url.params.get("offset", 0))
        page = self.ids[offset : offset + 2]  # two a page, so paging is exercised
        more = offset + 2 < len(self.ids)
        next_page = f"/api/v1/models?offset={offset + 2}&limit=2" if more else None
        return httpx.Response(
            200, json={"data": [{"id": i} for i in page], "links": {"next": next_page}}
        )


@pytest.fixture
def chat() -> FakeChat:
    return FakeChat()


@pytest.fixture
def catalogue() -> FakeCatalogue:
    return FakeCatalogue([DEFAULT_MODEL, "openai/gpt-5.5", OTHER_MODEL, "google/gemini-3-flash"])


def fake_openrouter(chat: FakeChat, catalogue: FakeCatalogue) -> OpenRouter:
    return OpenRouter(
        SecretStr(KEY),
        chat_client=httpx2.Client(transport=httpx2.MockTransport(chat)),
        catalogue_client=httpx.Client(transport=httpx.MockTransport(catalogue)),
    )


def app_with_key(chat: FakeChat, catalogue: FakeCatalogue, settings: Settings | None = None):
    """The app with an OpenRouter key set (local mode unless `settings` says), OpenRouter faked."""
    settings = settings or Settings(openrouter_api_key=SecretStr(KEY))
    app = create_app(settings)
    app.state.models.openrouter = fake_openrouter(chat, catalogue)
    return app


@pytest.fixture
def client(chat, catalogue) -> TestClient:
    return TestClient(app_with_key(chat, catalogue), raise_server_exceptions=False)


def events(response) -> list[str]:
    return [line.removeprefix("event: ") for line in response.text.splitlines() if "event:" in line]


def assess(client: TestClient, **kwargs: Any):
    return client.post("/api/assessments", json={"control_id": CONTROL}, **kwargs)


# --- With a key, an assessment uses the stored model -------------------------------------------


def test_with_a_key_an_assessment_runs_on_openrouter_with_the_default_model(client, chat):
    response = assess(client)

    assert events(response)[-1] == "result"
    assert "full-disk encryption" in response.text
    assert chat.models and set(chat.models) == {DEFAULT_MODEL}
    assert {str(r.url) for r in chat.requests} == {"https://openrouter.ai/api/v1/chat/completions"}
    assert all(r.headers["authorization"] == f"Bearer {KEY}" for r in chat.requests)


def test_an_assessment_uses_the_model_the_store_holds(chat, catalogue):
    app = app_with_key(chat, catalogue)
    app.state.models.store.set(OTHER_MODEL)

    assess(TestClient(app))

    assert set(chat.models) == {OTHER_MODEL}


def test_assess_model_sets_the_default_until_a_developer_chooses(chat, catalogue):
    settings = Settings(openrouter_api_key=SecretStr(KEY), model="openai/gpt-5.5")

    assess(TestClient(app_with_key(chat, catalogue, settings)))

    assert set(chat.models) == {"openai/gpt-5.5"}


def test_a_fake_script_still_wins_over_the_key(chat, catalogue):
    settings = Settings(openrouter_api_key=SecretStr(KEY), fake_script=model_choice.DEMO_SCRIPT)

    response = assess(TestClient(app_with_key(chat, catalogue, settings)))

    assert events(response)[-1] == "result"
    assert chat.requests == []


def test_without_a_key_an_assessment_runs_on_the_fake_model_and_nothing_reaches_openrouter():
    client = TestClient(create_app(Settings()))

    response = assess(client)

    assert events(response)[-1] == "result"
    assert client.app.state.models.openrouter is None


# --- A developer changes the model, and the next assessment uses it ---------------------------


def test_the_developer_page_shows_the_model_the_prompt_version_and_the_usage_link(client):
    response = client.get("/api/developer/settings")

    assert response.status_code == 200
    assert response.json() == {
        "model": DEFAULT_MODEL,
        "runs_on": "openrouter",
        "prompt_version": PROMPT_VERSION,
        "usage_url": USAGE_URL,
        "kept_in": "memory",
    }


def test_a_developer_changes_the_model_and_the_next_assessment_uses_it(client, chat):
    assess(client)

    changed = client.put("/api/developer/model", json={"model": OTHER_MODEL})
    shown = client.get("/api/developer/settings").json()
    chat.requests.clear()
    assess(client)

    assert changed.status_code == 200
    assert changed.json()["model"] == OTHER_MODEL
    assert shown["model"] == OTHER_MODEL
    assert chat.models and set(chat.models) == {OTHER_MODEL}


def test_the_id_is_checked_against_every_page_of_openrouters_models(client, catalogue):
    """The last page holds it: the list is read to its end, with the key."""
    response = client.put("/api/developer/model", json={"model": "google/gemini-3-flash"})

    assert response.status_code == 200
    assert len(catalogue.calls) == 2
    assert all(c.headers["authorization"] == f"Bearer {KEY}" for c in catalogue.calls)


def test_an_unknown_model_id_is_refused_with_a_message_and_the_model_stays(client):
    response = client.put("/api/developer/model", json={"model": "anthropic/claude-haiku-9"})

    assert response.status_code == 422
    assert response.json() == {
        "detail": "OpenRouter has no model “anthropic/claude-haiku-9”. Check the id on "
        "openrouter.ai/models."
    }
    assert client.get("/api/developer/settings").json()["model"] == DEFAULT_MODEL


@pytest.mark.parametrize(
    "model", ["", "haiku", "anthropic/", "/haiku", "a/b c", "a/" + "x" * 200, "../../etc/passwd"]
)
def test_a_malformed_model_id_is_refused_before_openrouter_is_asked(client, catalogue, model):
    response = client.put("/api/developer/model", json={"model": model})

    assert response.status_code == 422
    assert catalogue.calls == []


def test_when_openrouter_cannot_be_asked_the_change_is_refused_with_a_message(client, catalogue):
    catalogue.fail = True

    response = client.put("/api/developer/model", json={"model": OTHER_MODEL})

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "OpenRouter cannot be reached to check the model just now. Please try again shortly."
    )
    assert client.get("/api/developer/settings").json()["model"] == DEFAULT_MODEL


def test_without_a_key_the_page_says_the_fake_model_runs_and_a_change_is_refused():
    client = TestClient(create_app(Settings()))

    shown = client.get("/api/developer/settings").json()
    refused = client.put("/api/developer/model", json={"model": OTHER_MODEL})

    assert shown["runs_on"] == "fake"
    assert shown["model"] == DEFAULT_MODEL
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "No OpenRouter key is set, so assessments run on the fake model and a model id cannot "
        "be checked. Set OPENROUTER_API_KEY to choose a model."
    )


# --- The model is kept in the database when there is one ----------------------------------------


def test_with_the_database_down_an_assessment_runs_on_the_default_and_the_page_answers_503(
    chat, catalogue
):
    settings = Settings(openrouter_api_key=SecretStr(KEY), database_url=SecretStr(UNREACHABLE_DB))
    app = app_with_key(chat, catalogue, settings)
    app.state.database = Database(settings.database_url, timeout=0.3)
    app.state.models.store = model_choice.DatabaseModelStore(app.state.database)
    client = TestClient(app, raise_server_exceptions=False)

    response = assess(client)
    page = client.get("/api/developer/settings")
    change = client.put("/api/developer/model", json={"model": OTHER_MODEL})
    app.state.database.close()

    assert events(response)[-1] == "result"
    assert set(chat.models) == {DEFAULT_MODEL}
    assert page.status_code == 503
    assert change.status_code == 503


def test_with_a_database_url_the_model_is_kept_in_the_database():
    app = create_app(Settings(database_url=SecretStr(UNREACHABLE_DB)))

    assert isinstance(app.state.models.store, model_choice.DatabaseModelStore)
    assert isinstance(create_app(Settings()).state.models.store, MemoryModelStore)


# --- An OpenRouter failure is retried with backoff, then becomes an `error` event ---------------


@pytest.fixture
def quick_backoff(monkeypatch) -> list[float]:
    """The openai SDK's own backoff, its sleeps recorded instead of slept."""
    slept: list[float] = []

    class Clock:
        sleep = staticmethod(slept.append)

        def __getattr__(self, name: str) -> Any:
            return getattr(time, name)

    monkeypatch.setattr(openai._base_client, "time", Clock())
    return slept


def test_an_openrouter_error_is_retried_with_backoff_then_one_error_event(
    client, chat, quick_backoff, caplog
):
    error = {"error": {"code": 502, "message": "Provider returned error"}}
    chat.fail = lambda attempt, request: httpx2.Response(502, json=error)

    response = assess(client)

    assert events(response) == ["error"]
    assert "could not be completed" in response.text
    assert len(chat.requests) == 1 + MAX_RETRIES
    assert len(quick_backoff) == MAX_RETRIES
    assert quick_backoff[0] < quick_backoff[1]  # exponential: 0.5 s, then 1 s (less jitter)
    assert KEY not in response.text + caplog.text


def test_an_openrouter_timeout_is_retried_then_one_error_event(client, chat, quick_backoff):
    def time_out(attempt, request):
        raise httpx2.ReadTimeout("timed out", request=request)

    chat.fail = time_out

    response = assess(client)

    assert events(response) == ["error"]
    assert len(chat.requests) == 1 + MAX_RETRIES


def test_an_openrouter_error_that_clears_on_retry_gives_the_result(client, chat, quick_backoff):
    busy = {"error": {"code": 429, "message": "Rate limited"}}
    chat.fail = lambda attempt, request: httpx2.Response(429, json=busy) if attempt == 1 else None

    response = assess(client)

    assert events(response)[-1] == "result"
    assert len(quick_backoff) == 1


def test_a_request_to_openrouter_has_a_timeout():
    model = OpenRouter(SecretStr(KEY)).chat_model(DEFAULT_MODEL)

    assert model.request_timeout == model_choice.TIMEOUT_SECONDS
    assert model.max_retries == MAX_RETRIES
    assert KEY not in repr(model)


# --- A compliance lead gets 403 from the developer API ------------------------------------------


@pytest.fixture
def clerk() -> FakeClerk:
    return FakeClerk({"user_alice": {}, "user_dev": {"role": "developer"}})


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/developer/settings", None),
        ("PUT", "/api/developer/model", {"model": OTHER_MODEL}),
    ],
)
def test_a_compliance_lead_gets_403_from_the_developer_api(clerk, method, path, body):
    client = TestClient(clerk_app(clerk), raise_server_exceptions=False)

    response = client.request(method, path, json=body, headers=bearer(token("user_alice")))

    assert response.status_code == 403
    assert response.json() == {"detail": "This is available to developers only."}
    assert "model" not in response.text


def test_a_developer_signed_in_with_clerk_reaches_the_developer_api(clerk):
    client = TestClient(clerk_app(clerk), raise_server_exceptions=False)

    response = client.get("/api/developer/settings", headers=bearer(token("user_dev")))

    assert response.status_code == 200
    assert "role" not in response.json()  # the role itself never reaches the browser


def test_the_developer_api_logs_a_change_without_the_key(client, caplog):
    caplog.set_level(logging.INFO)

    client.put("/api/developer/model", json={"model": OTHER_MODEL})

    assert OTHER_MODEL in caplog.text
    assert KEY not in caplog.text


def test_the_model_choice_never_shows_the_key():
    choice = ModelChoice(Settings(openrouter_api_key=SecretStr(KEY)), MemoryModelStore())

    assert KEY not in repr(choice) + repr(choice.openrouter)
