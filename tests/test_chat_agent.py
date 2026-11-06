"""The chat agent (#45): its progress greeting, assessments started from the chat, and the chat
thread it keeps. Through the chat entry point (the HTTP API), on the chat's fake model and the
bundled assessment script: no key, no paid call. Each case runs in memory and on Postgres (#38's
fixtures), for a lead new to the test."""

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
import test_memory
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.store.memory import InMemoryStore
from profiles_dir import SECOND, make_profiles
from test_api import read_events, recorded_prompts
from test_database import empty_database  # noqa: F401 (a fixture: postgres's)
from test_memory import app_on, as_user, restart

from vouch import api, chat_agent
from vouch.api import PROFILE_HEADER, create_app, current_user
from vouch.chat_agent import (
    GREETING_REQUEST,
    HISTORY_WINDOW,
    START_ASSESSMENT,
    ChatTools,
    chat_config,
    chat_context,
    chat_graph,
    chat_turn,
)
from vouch.chat_fake import CannedChatModel
from vouch.database import Database
from vouch.graph import memory_checkpointer, new_thread_id, state_serializer
from vouch.isa import load_base_framework
from vouch.memory import Memory, process_checkpointer
from vouch.model_choice import MemoryModelStore, ModelChoice
from vouch.profile import DEFAULT_PROFILE, load_named_profile, read_profile_once
from vouch.runs import RUNNING_ALREADY, AssessmentRun, RunningAssessments
from vouch.settings import Settings
from vouch.spending import DailyLimitReached, MemorySpendingStore, SpendingGuard, UsageMeter

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


@contextmanager
def holding(running: RunningAssessments, scope: str) -> Iterator[None]:
    """A run of the lead's going, as far as `running` knows."""
    assert running.try_hold(scope)
    try:
        yield
    finally:
        running.release(scope)


def is_held(running: RunningAssessments, scope: str) -> bool:
    if running.try_hold(scope):
        running.release(scope)
        return False
    return True


def thread_of(events) -> str:
    """The chat thread a reply names, in its first event."""
    name, data = events[0]
    assert name == "thread", events[0]
    return data["thread_id"]


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
    with holding(app.state.running, scope_of(lead)):
        events = say(client, "Assess 5.1.1")

    assert reply(events) == RUNNING_ALREADY
    assert "assessment" not in names(events)
    assert client.get("/api/assessments").json()["assessments"] == []


def test_another_users_running_assessment_doesnt_stop_this_one(app, client, users):
    with holding(app.state.running, scope_of(users[1])):
        events = say(client, "Assess 5.1.1")

    assert "assessment" in names(events)


def test_an_assessment_from_the_working_paper_is_noted_as_running_while_it_runs(
    app, client, lead, monkeypatch
):
    seen = []
    events_of = api.assessment_events

    def watched(*args, **kwargs):
        for item in events_of(*args, **kwargs):
            seen.append(is_held(app.state.running, scope_of(lead)))
            yield item

    monkeypatch.setattr(api, "assessment_events", watched)

    assess_and_review(client, "4.1.2")

    assert seen and all(seen)
    assert not is_held(app.state.running, scope_of(lead))


def test_the_chat_releases_its_run_so_the_next_one_starts(app, client, lead):
    say(client, "Assess 5.1.1")

    events = say(client, "Assess 4.1.2")

    assert "assessment" in names(events)
    assert not is_held(app.state.running, scope_of(lead))


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
    thread_id = thread_of(first)
    say(client, "And again", thread_id=thread_id)

    contents = [message.content for message in fake.prompts[-1][1:]]
    assert contents == ["Hello", fake.reply, "And again"]


def test_a_chat_thread_outlasts_a_restart(database_url, lead, fake):
    with TestClient(app_on(database_url), headers=lead) as client:
        thread_id = thread_of(say(client, "Hello"))
    restart()

    with TestClient(app_on(database_url), headers=lead) as client:
        say(client, "And again", thread_id=thread_id)

    contents = [message.content for message in fake.prompts[-1][1:]]
    expected = ["Hello", fake.reply, "And again"] if database_url else ["And again"]
    assert contents == expected


def test_another_users_thread_id_names_nothing_for_this_one(client, users, fake):
    thread_id = thread_of(say(client, "My secret question"))

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

    assert thread_of(first) != thread_of(second)
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
    monkeypatch.setattr(chat_agent, "RECURSION_LIMIT", 5)  # the chat's turn needs 4 steps

    events = say(client, "Assess 5.1.1")

    assert "assessment" in names(events)


# Review fixes (#45): one run per lead on both paths, a bounded and redacted thread, the thread
# named first, a left stream that neither waits nor ends the run early, invalid arguments


def test_the_working_paper_refuses_a_run_while_the_chats_is_going(app, client, lead):
    with holding(app.state.running, scope_of(lead)):
        response = client.post("/api/assessments", json={"control_id": "4.1.2"})

    assert response.status_code == 409
    assert response.json()["detail"] == RUNNING_ALREADY
    assert client.get("/api/assessments").json()["assessments"] == []
    assert client.get("/api/developer/settings").json()["usage"]["fake"]["assessments"] == 0


def test_a_long_thread_sends_the_model_only_its_latest_window(client, fake):
    thread_id = thread_of(say(client, "Hello 0"))
    for n in range(1, 15):
        say(client, f"Hello {n}", thread_id=thread_id)

    prompt = fake.prompts[-1]
    assert len(prompt) <= 1 + HISTORY_WINDOW
    assert isinstance(prompt[0], SystemMessage) and isinstance(prompt[1], HumanMessage)
    assert prompt[-1].content == "Hello 14"
    assert "Hello 0" not in [message.content for message in prompt]


def test_a_secret_never_reaches_the_chat_thread_or_the_model_again(
    database_url, client, lead, fake
):
    fake.reply = "Please write to anna.schmidt@example.com about it."
    thread_id = thread_of(say(client, "My token is Bearer abc123def456ghi, is that fine?"))

    say(client, "And now?", thread_id=thread_id)

    config = chat_config(thread_id, scope_of(lead))
    if database_url is None:
        kept = chat_graph(process_checkpointer()).get_state(config).values
    else:
        with Database(database_url) as db, db.checkpointer(state_serializer()) as saver:
            kept = chat_graph(saver).get_state(config).values
    assert len(kept["messages"]) == 4
    for seen in [str(kept), str(fake.prompts[-1])]:
        assert "abc123def456ghi" not in seen
        assert "anna.schmidt" not in seen


def test_the_thread_is_named_first_so_a_failed_first_turn_keeps_it(client, fake):
    fake.fail_with = RuntimeError("model down")

    events = say(client, "Hello")

    assert names(events) == ["thread", "error"]
    thread_of(events)


def chat_tools() -> tuple[ChatTools, list[str]]:
    """The chat's tools for a lead in memory, on the fakes; and the controls whose usage they
    recorded."""
    memory = Memory(InMemoryStore(), "lead-left", DEFAULT_PROFILE)
    models = ModelChoice(Settings(), MemoryModelStore())
    saver = memory_checkpointer()
    run = AssessmentRun(models, None, frozenset(), saver, UsageMeter(None), memory.scope)
    recorded: list[str] = []
    spending = SpendingGuard(MemorySpendingStore())
    tools = ChatTools(
        load_named_profile(DEFAULT_PROFILE),
        memory,
        run,
        spending,
        RunningAssessments(),
        lambda control_id, usage: recorded.append(control_id),
    )
    return tools, recorded


def test_a_chat_left_mid_assessment_neither_waits_for_it_nor_ends_its_run_early(monkeypatch):
    gate = threading.Event()
    made: list[int] = []

    def slow_run(control, profile, run, memory, *, thread_id, note=""):
        for n in range(50):
            made.append(n)
            yield "step", {"node": "gather", "text": f"Step {n}."}
            if n == 0:
                assert gate.wait(5)  # the run is busy until the test lets it go on

    monkeypatch.setattr(chat_agent, "assessment_events", slow_run)
    tools, recorded = chat_tools()
    scope = tools.memory.scope
    chat = chat_context(CannedChatModel(), tools, SystemMessage("Test."))
    turn = chat_turn(chat_graph(tools.run.saver), chat, "Assess 5.1.1", new_thread_id(), scope)
    assert ("step", {"node": "gather", "text": "Step 0."}) in iter(turn)  # read up to it

    started = time.monotonic()
    turn.close()  # the lead closed the tab

    assert time.monotonic() - started < 1  # never waits for the assessment
    assert is_held(tools.running, scope) and recorded == []  # its worker is still going
    gate.set()
    deadline = time.monotonic() + 5
    while is_held(tools.running, scope) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not is_held(tools.running, scope)
    assert made == [0, 1]  # it stopped at its next step
    assert recorded == ["5.1.1"]  # kept once, as it ended


def test_a_start_assessment_call_without_a_control_id_says_its_arguments_are_invalid():
    tools, _ = chat_tools()
    written = []

    said = tools.call(START_ASSESSMENT, {"control": 511}, written.append, threading.Event())

    assert "its arguments are invalid" in said and "isn't one of the chat's tools" not in said
    assert written == [("step", {"node": "guard", "text": said})]


def test_a_chat_thread_under_one_profile_names_nothing_under_another(tmp_path, lead, fake):
    read_profile_once.cache_clear()
    restart()
    app = create_app(Settings(profiles_dir=make_profiles(tmp_path / "profiles")))
    app.dependency_overrides[current_user] = as_user

    def ask(client, message, profile_id, thread_id=None):
        body = {"page": "dossier", "message": message, "thread_id": thread_id}
        headers = {PROFILE_HEADER: profile_id}
        return read_events(client.post("/api/chat", json=body, headers=headers))

    with TestClient(app, headers=lead) as client:
        thread_id = thread_of(ask(client, "About Nordlicht", DEFAULT_PROFILE))
        ask(client, "Hello", SECOND, thread_id)
        under_second = [message.content for message in fake.prompts[-1][1:]]
        ask(client, "Back again", DEFAULT_PROFILE, thread_id)
        under_first = [message.content for message in fake.prompts[-1][1:]]

    assert under_second == ["Hello"]  # nothing of Nordlicht's thread
    assert under_first == ["About Nordlicht", fake.reply, "Back again"]
    read_profile_once.cache_clear()


def test_assess_again_from_the_chat_carries_the_rejection_note(client, monkeypatch):
    note = "The key register is in records/, dated 2026."
    events = read_events(client.post("/api/assessments", json={"control_id": "4.1.2"}))
    thread_id = events[-1][1]["assessment"]["thread_id"]
    review = {"decision": "reject", "note": note}
    assert client.post(f"/api/assessments/{thread_id}/review", json=review).status_code == 200
    prompts = recorded_prompts(monkeypatch)

    events = say(client, "Assess 4.1.2")

    assert "assessment" in names(events)
    assert note in prompts[0][-1].content  # the same path as the working paper's (#42)


# The profile's ISA edition (#199)

GERMAN_1_6_2 = "Inwieweit werden gemeldete Sicherheitsereignisse verwaltet?"
ALPENWERK = {PROFILE_HEADER: "alpenwerk"}


def test_an_assessment_from_the_chat_on_a_german_profile_loads_the_german_control(app, lead):
    with TestClient(app, raise_server_exceptions=False, headers=lead | ALPENWERK) as client:
        events = say(client, "Assess 1.6.2")

    steps = [data for name, data in events if name == "step"]
    assert steps[0]["text"].startswith(f"Loaded control 1.6.2: {GERMAN_1_6_2}")


def test_explaining_a_control_on_a_german_profile_reads_the_german_control(app, lead):
    with TestClient(app, raise_server_exceptions=False, headers=lead | ALPENWERK) as client:
        events = say(client, "Explain 1.6.2")

    text = reply(events)
    assert f"Control 1.6.2, “{GERMAN_1_6_2}”" in text
    assert "Gemeldete Ereignisse werden unverzüglich bearbeitet." in text
