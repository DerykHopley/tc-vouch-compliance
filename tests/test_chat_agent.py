"""The chat agent (#45): its progress greeting, assessments started from the chat, and the chat
thread it keeps. Through the chat entry point (the HTTP API), on the chat's fake model and the
bundled assessment script: no key, no paid call. Each case runs in memory and on Postgres (#38's
fixtures), for a lead new to the test."""

import json
from datetime import UTC, datetime

import pytest
import test_memory
from fastapi.testclient import TestClient
from test_api import read_events
from test_database import empty_database  # noqa: F401 (a fixture: postgres's)
from test_memory import app_on

from vouch import api, chat_agent
from vouch.chat import GREETING_REQUEST
from vouch.chat_fake import CannedChatModel
from vouch.isa import load_base_framework
from vouch.model_choice import ModelChoice
from vouch.profile import DEFAULT_PROFILE
from vouch.runs import RUNNING_ALREADY
from vouch.spending import DailyLimitReached, SpendingGuard

CONTROL_IDS = [control.id for control in load_base_framework().controls]

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users


@pytest.fixture
def lead(users) -> dict[str, str]:
    return users[0]


@pytest.fixture
def app(database_url):
    return app_on(database_url)


@pytest.fixture
def client(app, lead):
    with TestClient(app, raise_server_exceptions=False, headers=lead) as client:
        yield client


def scope_of(headers: dict[str, str]) -> str:
    """The lead's memory scope (vouch.memory.Memory.scope)."""
    return json.dumps([headers["X-Test-User"], DEFAULT_PROFILE])


def greet(client, **body):
    return read_events(client.post("/api/chat", json={"page": "dossier", **body}))


def say(client, message, **body):
    body = {"page": "dossier", "message": message, **body}
    return read_events(client.post("/api/chat", json=body))


def reply(events) -> str:
    return "".join(data["text"] for name, data in events if name == "token")


def names(events) -> list[str]:
    return [name for name, _ in events]


def assess_and_review(client, control_id, decision=None):
    events = read_events(client.post("/api/assessments", json={"control_id": control_id}))
    thread_id = events[-1][1]["assessment"]["thread_id"]
    if decision:
        client.post(f"/api/assessments/{thread_id}/review", json={"decision": decision})


@pytest.fixture
def fake(monkeypatch) -> CannedChatModel:
    """The chat's fake, kept, so a test can read the prompts it was sent."""
    model = CannedChatModel()
    monkeypatch.setattr(ModelChoice, "for_chat", lambda self, chosen, callbacks=None: model)
    return model


# The greeting: where the lead left off, and the next step (from the store)


def test_a_new_lead_is_greeted_with_nothing_assessed_and_the_first_control_next(client):
    events = greet(client)

    text = reply(events)
    assert "You have not assessed any of the 12 controls yet." in text
    assert f"The next step is to assess control {CONTROL_IDS[0]}." in text
    assert ("suggestion", {"control_id": CONTROL_IDS[0], "action": "assess"}) in events
    assert names(events)[-1] == "done"


def test_a_returning_lead_is_greeted_with_counts_and_the_next_control_from_the_store(client):
    assess_and_review(client, "4.1.2", "accept")
    assess_and_review(client, "1.1.1", "reject")
    assess_and_review(client, "5.1.1")  # proposed, waiting for review

    events = greet(client)

    text = reply(events)
    today = datetime.now(UTC).date()
    assert f"You last worked on {today.day} {today:%B %Y}." in text
    assert (
        "Of the 12 controls, 1 is accepted, 1 is rejected, 1 is proposed and waiting for your "
        "review, and 9 are not assessed."
    ) in text
    # A waiting review comes first (vouch.dossier's next step).
    assert "The next step is to review control 5.1.1 on its working paper." in text
    assert ("suggestion", {"control_id": "5.1.1", "action": "review"}) in events


def test_the_greeting_asks_the_agent_which_calls_its_progress_tool(client, fake):
    greet(client)

    assert fake.prompts[0][-1].content == GREETING_REQUEST
    tool_reply = fake.prompts[1][-1]
    assert tool_reply.type == "tool" and tool_reply.name == "progress"


# Assessments from the chat


def test_assess_in_the_chat_streams_the_steps_and_ends_with_the_verdict_and_its_paper(client):
    events = say(client, "Assess 5.1.1")

    steps = [data for name, data in events if name == "step"]
    assert steps[0]["node"] == "load_control" and "5.1.1" in steps[0]["text"]
    assert any(step["node"] == "judge" for step in steps)
    finished = [data for name, data in events if name == "assessment"]
    assert len(finished) == 1
    assert finished[0]["control_id"] == "5.1.1"
    assert finished[0]["status"] == "waiting-for-review"
    assert names(events).index("assessment") > names(events).index("step")
    assert "waits for your review on the control's working paper" in reply(events)
    assert names(events)[-1] == "done"
    # The run is the lead's like any other: listed, and counted as an assessment.
    listed = client.get("/api/assessments").json()["assessments"]
    assert [(a["control_id"], a["thread_id"]) for a in listed] == [
        ("5.1.1", finished[0]["thread_id"])
    ]
    assert client.get("/api/developer/settings").json()["usage"]["fake"]["assessments"] >= 1


def test_an_unknown_control_gets_a_formal_reply_listing_the_valid_ones(client):
    events = say(client, "Assess 9.9.9")

    assert reply(events) == (
        f"There is no VDA ISA control “9.9.9”. The controls are {', '.join(CONTROL_IDS)}."
    )
    assert "assessment" not in names(events)
    assert client.get("/api/assessments").json()["assessments"] == []


def test_a_second_assessment_while_one_runs_is_declined(app, client, lead):
    with app.state.running.running(scope_of(lead)):
        events = say(client, "Assess 5.1.1")

    assert reply(events) == RUNNING_ALREADY
    assert "assessment" not in names(events)
    assert client.get("/api/assessments").json()["assessments"] == []


def test_another_users_running_assessment_doesnt_stop_this_one(app, client, users):
    with app.state.running.running(scope_of(users[1])):
        events = say(client, "Assess 5.1.1")

    assert "assessment" in names(events)


def test_an_assessment_from_the_working_paper_is_noted_as_running_while_it_runs(
    app, client, lead, monkeypatch
):
    seen = []
    events_of = api.assessment_events

    def watched(*args, **kwargs):
        for item in events_of(*args, **kwargs):
            seen.append(app.state.running.busy(scope_of(lead)))
            yield item

    monkeypatch.setattr(api, "assessment_events", watched)

    assess_and_review(client, "4.1.2")

    assert seen and all(seen)
    assert not app.state.running.busy(scope_of(lead))


def test_the_chat_releases_its_run_so_the_next_one_starts(app, client, lead):
    say(client, "Assess 5.1.1")

    events = say(client, "Assess 4.1.2")

    assert "assessment" in names(events)
    assert not app.state.running.busy(scope_of(lead))


# The daily limit


def test_at_a_limit_of_0_the_chat_and_its_greeting_get_the_limit_message(app, client):
    app.state.spending.store.set_daily_limit(0)

    for body in [{}, {"message": "Assess 5.1.1"}]:
        response = client.post("/api/chat", json={"page": "dossier", **body})

        assert response.status_code == 429
        answer = response.json()
        assert answer["limit"] == "daily"
        assert answer["detail"].startswith("You have reached today's limit of 0 assessments.")


def test_the_limit_reached_as_the_chat_starts_an_assessment_is_said_in_the_chat(
    client, monkeypatch
):
    resets_at = datetime(2026, 11, 6, tzinfo=UTC)

    def refuse(self, user_id, paid):
        raise DailyLimitReached(0, resets_at, datetime.now(UTC))

    monkeypatch.setattr(SpendingGuard, "start", refuse)

    events = say(client, "Assess 5.1.1")

    assert reply(events) == DailyLimitReached(0, resets_at, resets_at).message()
    assert "assessment" not in names(events)
    assert client.get("/api/assessments").json()["assessments"] == []


# The chat thread


def test_a_chat_thread_keeps_its_history_for_the_next_message(client, fake):
    first = say(client, "Hello")
    thread_id = first[-1][1]["thread_id"]
    say(client, "And again", thread_id=thread_id)

    contents = [message.content for message in fake.prompts[-1][1:]]
    assert contents == ["Hello", fake.reply, "And again"]


def test_a_chat_thread_outlasts_a_restart(database_url, lead, fake):
    with TestClient(app_on(database_url), headers=lead) as client:
        thread_id = say(client, "Hello")[-1][1]["thread_id"]
    test_memory.restart()

    with TestClient(app_on(database_url), headers=lead) as client:
        say(client, "And again", thread_id=thread_id)

    contents = [message.content for message in fake.prompts[-1][1:]]
    expected = ["Hello", fake.reply, "And again"] if database_url else ["And again"]
    assert contents == expected


def test_another_users_thread_id_names_nothing_for_this_one(client, users, fake):
    thread_id = say(client, "My secret question")[-1][1]["thread_id"]

    other = client.post(
        "/api/chat",
        json={"page": "dossier", "message": "Hello", "thread_id": thread_id},
        headers=users[1],
    )

    assert other.status_code == 200
    assert [message.content for message in fake.prompts[-1][1:]] == ["Hello"]


def test_without_a_thread_each_message_starts_a_new_one(client, fake):
    first = say(client, "Hello")
    second = say(client, "And again")

    assert first[-1][1]["thread_id"] != second[-1][1]["thread_id"]
    assert [message.content for message in fake.prompts[-1][1:]] == ["And again"]


def test_a_malformed_thread_id_is_422(client):
    response = client.post(
        "/api/chat", json={"page": "dossier", "message": "Hi", "thread_id": "../other"}
    )

    assert response.status_code == 422


def test_the_greetings_model_calls_are_metered_as_chat_not_as_assessments(client):
    greet(client)

    usage = client.get("/api/developer/settings").json()["usage"]["fake"]
    assert usage["input_tokens"] > 0 and usage["assessments"] == 0


def test_an_assessment_from_the_chat_runs_apart_from_the_chats_own_step_limit(client, monkeypatch):
    """Started inside the chat's graph, the assessment graph would take the chat's run config,
    its recursion limit included, and stop part way."""
    monkeypatch.setattr(chat_agent, "RECURSION_LIMIT", 4)  # the chat's turn needs 3 steps

    events = say(client, "Assess 5.1.1")

    assert "assessment" in names(events)
