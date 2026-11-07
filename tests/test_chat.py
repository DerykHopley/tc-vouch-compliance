"""Vouch's chat (#44): the chat stream endpoint, and the chat agent's prompt and reply."""

import logging

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import SystemMessage
from pydantic import SecretStr
from test_api import read_events

from vouch.api import create_app
from vouch.chat import CHAT_FAILED, CHAT_PROMPT_VERSION, PageContext
from vouch.chat_agent import chat_prompt
from vouch.chat_fake import CHAT_REPLY, CannedChatModel
from vouch.isa import load_isa_control
from vouch.model_choice import ModelChoice
from vouch.profile import DEFAULT_PROFILE, load_named_profile, read_profile_once
from vouch.redaction import remember_secrets
from vouch.settings import Settings


@pytest.fixture
def client():
    """The app in local mode, its chat on the canned fake (no key, no paid call)."""
    read_profile_once.cache_clear()
    yield TestClient(create_app(Settings()), raise_server_exceptions=False)
    read_profile_once.cache_clear()


def chat(client, **body):
    return client.post("/api/chat", json={"page": "dossier", **body})


def test_the_chat_streams_the_reply_whole_as_one_token_event_then_done(client):
    response = chat(client, message="What does TISAX ask of us?")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = read_events(response)
    names = [name for name, _ in events]
    # Whole, once its quotes are checked (#46), not piece by piece as the model writes it.
    assert names == ["thread", "token", "done"]
    assert "".join(data["text"] for name, data in events if name == "token") == CHAT_REPLY


@pytest.mark.parametrize("message", ["", "   "])
def test_an_empty_message_is_422_and_no_stream(client, message):
    response = chat(client, message=message)

    assert response.status_code == 422


def test_an_unknown_page_is_422(client):
    response = client.post("/api/chat", json={"message": "Hello", "page": "elsewhere"})

    assert response.status_code == 422


def test_an_unknown_control_is_404_and_no_stream(client):
    response = chat(client, message="What's missing?", page="paper", control_id="9.9.9")

    assert response.status_code == 404
    assert not response.headers["content-type"].startswith("text/event-stream")


def test_the_model_sees_the_page_and_control_the_compliance_lead_is_on(client, monkeypatch):
    fake = CannedChatModel()
    monkeypatch.setattr(ModelChoice, "for_chat", lambda self, chosen, callbacks=None, **_: fake)

    chat(client, message="What's missing here?", page="paper", control_id="4.1.2")

    system, human = fake.prompts[0]
    assert "4.1.2" in system.content
    assert load_isa_control("4.1.2").question in system.content
    assert "working paper" in system.content
    assert human.content == "What's missing here?"


def test_on_the_readiness_report_the_model_is_told_so_and_not_the_dossier_overview(
    client, monkeypatch
):
    fake = CannedChatModel()
    monkeypatch.setattr(ModelChoice, "for_chat", lambda self, chosen, callbacks=None, **_: fake)

    response = chat(client, message="What does the report say?", page="report")

    assert response.status_code == 200
    assert [name for name, _ in read_events(response)][-1] == "done"
    system, _ = fake.prompts[0]
    assert "readiness report" in system.content
    assert "Dossier overview" not in system.content


def test_a_model_failure_ends_the_stream_with_one_formal_error_event(client, monkeypatch):
    fake = CannedChatModel(fail_with=RuntimeError("socket on fire"))
    monkeypatch.setattr(ModelChoice, "for_chat", lambda self, chosen, callbacks=None, **_: fake)

    response = chat(client, message="Hello")

    events = read_events(response)
    assert events[-1] == ("error", {"message": CHAT_FAILED})
    assert [name for name, _ in events].count("error") == 1
    assert "done" not in [name for name, _ in events]
    assert "socket on fire" not in response.text


def test_a_secret_in_the_reply_is_removed_with_a_guard_step(client, monkeypatch):
    leaked = (
        "Your token is Bearer abc123def456ghi and the address is anna.schmidt@example.com today."
    )
    fake = CannedChatModel(reply=leaked)
    monkeypatch.setattr(ModelChoice, "for_chat", lambda self, chosen, callbacks=None, **_: fake)

    response = chat(client, message="Hello")

    events = read_events(response)
    text = "".join(data["text"] for name, data in events if name == "token")
    assert "abc123def456ghi" not in response.text
    assert "anna.schmidt" not in response.text
    assert text == "Your token is Bearer [removed] and the address is [removed] today."
    assert any(name == "step" and data["node"] == "guard" for name, data in events)


# The chat entry point


def nordlicht():
    return load_named_profile(DEFAULT_PROFILE)


def test_the_prompt_asks_for_the_formal_voice_in_the_profiles_language():
    profile = nordlicht().model_copy(update={"answer_language": "de"})

    system = chat_prompt(profile, PageContext("dossier"))

    assert isinstance(system, SystemMessage)
    assert "formal" in system.content
    assert "ISO 639-1 code 'de'" in system.content
    assert profile.company in system.content
    assert "Dossier" in system.content


def test_the_prompt_asks_for_every_quote_in_a_cite_naming_its_document():
    system = chat_prompt(nordlicht(), PageContext("dossier"))

    assert '<cite doc="DOC_ID">exact words</cite>' in system.content


def test_the_prompt_names_the_control_on_its_working_paper():
    control = load_isa_control("5.1.1")

    system = chat_prompt(nordlicht(), PageContext("paper", control))

    assert f"{control.id}" in system.content and control.question in system.content


def test_the_prompt_names_the_readiness_report():
    system = chat_prompt(nordlicht(), PageContext("report"))

    assert "readiness report" in system.content
    assert "Dossier overview" not in system.content


def said_in_chunks(client, monkeypatch, chunks: list[str]) -> tuple[str, list[str]]:
    """The reply the chat sends when the model writes it in `chunks`, and its guard steps."""
    fake = CannedChatModel(chunks=chunks)
    monkeypatch.setattr(ModelChoice, "for_chat", lambda self, chosen, callbacks=None, **_: fake)
    events = read_events(chat(client, message="Hello"))
    text = "".join(data["text"] for name, data in events if name == "token")
    return text, [data["text"] for name, data in events if name == "step"]


@pytest.mark.parametrize(
    "chunks",
    [
        ["Bear", "er abc1", "23def456 ", "end"],
        ["Bearer", " ", "abc123def456", " end"],
        ["Bearer abc123def456 end"],
    ],
)
def test_a_secret_split_across_chunks_is_still_removed(client, monkeypatch, chunks):
    text, steps = said_in_chunks(client, monkeypatch, chunks)

    assert text == "Bearer [removed] end"
    assert steps


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        (["Bearer\n", "abc12345 ok"], "Bearer [removed] ok"),
        (
            ["Authorization:", " Bearer", " tok", "en1234 done"],
            "Authorization: Bearer [removed] done",
        ),
        (
            ["Authorization:\n", "Bearer\n", "abcd", "efghijklmnopqrstuvwx\n", "then more words"],
            "Authorization:\nBearer [removed]\nthen more words",  # the scheme kept as written
        ),
    ],
)
def test_a_secret_split_across_whitespace_or_lines_is_never_half_sent(
    client, monkeypatch, chunks, expected
):
    assert said_in_chunks(client, monkeypatch, chunks)[0] == expected


def test_a_remembered_secret_with_spaces_is_removed_across_chunks(client, monkeypatch):
    remember_secrets(Settings(openrouter_api_key=SecretStr("open sesame key 0047 plus")))
    chunks = ["The key is open ", "sesame", " key 0047", " plus and", " that is all."]

    text, _ = said_in_chunks(client, monkeypatch, chunks)

    assert text == "The key is [removed] and that is all."


# The spending guard (M3, docs/requirements.md 2026-11-03): chat is metered, and refused at the limit


def test_a_chat_messages_tokens_are_in_the_developer_pages_totals_but_not_its_assessments(client):
    chat(client, message="Where do I stand?")

    usage = client.get("/api/developer/settings").json()["usage"]["fake"]
    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0
    assert usage["assessments"] == 0


def test_a_chat_message_takes_nothing_from_the_daily_assessment_count(client):
    client.put("/api/developer/daily-limit", json={"daily_limit": 1})

    for _ in range(3):
        assert chat(client, message="Hello").status_code == 200
    assessed = client.post("/api/assessments", json={"control_id": "4.1.2"})

    assert assessed.status_code == 200


def test_once_the_daily_limit_is_reached_a_chat_message_is_refused_with_its_429(client):
    client.put("/api/developer/daily-limit", json={"daily_limit": 1})
    client.post("/api/assessments", json={"control_id": "4.1.2"})

    response = chat(client, message="Hello")

    assert response.status_code == 429
    body = response.json()
    assert body["limit"] == "daily" and body["detail"].startswith("You have reached today's limit")
    assert int(response.headers["Retry-After"]) > 0


def test_the_chat_prompt_version_is_on_the_developer_page_and_in_the_usage_log(client, caplog):
    settings = client.get("/api/developer/settings").json()
    assert settings["chat_prompt_version"] == CHAT_PROMPT_VERSION

    with caplog.at_level(logging.INFO, logger="vouch.api"):
        chat(client, message="Hello")

    logged = [r.getMessage() for r in caplog.records]
    assert any(CHAT_PROMPT_VERSION in line and "tokens" in line for line in logged), logged
