"""The chat agent (#45): its progress greeting, assessments started from the chat, and the chat
thread it keeps. Through the chat entry point (the HTTP API), on the chat's fake model and the
bundled assessment script: no key, no paid call. Each case runs in memory and on Postgres (#38's
fixtures), for a lead new to the test."""

import json
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import test_memory
from chosen_documents import (
    CHOSEN,
    CHOSEN_QUOTE,
    SHARED_WORDS,
    UNCHOSEN,
    UNCHOSEN_QUOTE,
    chosen_profile,
)
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.store.memory import InMemoryStore
from profiles_dir import SECOND, make_profiles
from reviewing import reviewed
from test_api import read_events, recorded_prompts
from test_database import empty_database  # noqa: F401 (a fixture: postgres's)
from test_dossier import GERMAN_1_6_2, GERMAN_1_6_2_M1, WHOLE_REASON, WITHOUT_1_1_1, seeded
from test_memory import app_on, as_user, restart

from vouch import api, chat_agent
from vouch.api import PROFILE_HEADER, create_app, current_user
from vouch.chat import CHAT_PROMPT_VERSION, PageContext
from vouch.chat_agent import (
    ASSESSED_CONTROLS,
    GREETING_REQUEST,
    HISTORY_WINDOW,
    START_ASSESSMENT,
    ChatTools,
    assessed_controls_words,
    assessed_words,
    chat_config,
    chat_context,
    chat_graph,
    chat_prompt,
    chat_turn,
    progress_words,
    verdict_words_for,
)
from vouch.chat_fake import CannedChatModel
from vouch.citations import Citation, DropReason
from vouch.database import Database
from vouch.documents import EvidenceKind
from vouch.dossier import overview
from vouch.graph import (
    Assessment,
    RequirementVerdict,
    ShouldScore,
    memory_checkpointer,
    new_thread_id,
    state_serializer,
)
from vouch.isa import load_base_framework
from vouch.memory import Memory, process_checkpointer
from vouch.model_choice import MemoryModelStore, ModelChoice
from vouch.profile import (
    DEFAULT_PROFILE,
    SAMPLE_LIBRARY,
    ComplianceProfile,
    NotApplicable,
    load_named_profile,
    read_profile_once,
)
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
        reviewed(client, thread_id, decision)  # type: ignore[arg-type]


@pytest.fixture
def fake(monkeypatch) -> CannedChatModel:
    """The chat's fake, kept, so a test can read the prompts it was sent. Like the real one
    (ModelChoice.for_chat), it answers in the profile's language (#233), so its canned reply
    passes the chat's language check (#208)."""
    model = CannedChatModel()

    def for_chat(self, chosen, callbacks=None, answer_language="en"):
        model.language = answer_language
        return model

    monkeypatch.setattr(ModelChoice, "for_chat", for_chat)
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
    assert finished[0]["answer_language"] == "en"  # its verdict chip's words (#200)
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


def chat_tools(profile: ComplianceProfile | None = None) -> tuple[ChatTools, list[str]]:
    """The chat's tools for a lead in memory, on the fakes (under Nordlicht, or `profile`); and the
    controls whose usage they recorded."""
    memory = Memory(InMemoryStore(), "lead-left", DEFAULT_PROFILE)
    models = ModelChoice(Settings(), MemoryModelStore())
    saver = memory_checkpointer()
    run = AssessmentRun(models, None, frozenset(), saver, UsageMeter(None), memory.scope)
    recorded: list[str] = []
    spending = SpendingGuard(MemorySpendingStore())
    tools = ChatTools(
        profile or load_named_profile(DEFAULT_PROFILE),
        memory,
        run,
        spending,
        RunningAssessments(),
        lambda control_id, usage: recorded.append(control_id),
    )
    return tools, recorded


def chosen_tools(tmp_path) -> ChatTools:
    """The chat's tools under a profile whose policies are limited to CHOSEN (#384)."""
    profile = chosen_profile(tmp_path)
    memory = Memory(InMemoryStore(), "lead-chosen", profile.id)
    models = ModelChoice(Settings(), MemoryModelStore())
    run = AssessmentRun(
        models, None, frozenset(), memory_checkpointer(), UsageMeter(None), memory.scope
    )
    spending = SpendingGuard(MemorySpendingStore())
    return ChatTools(profile, memory, run, spending, RunningAssessments(), lambda c, u: None)


def test_the_chats_search_finds_passages_only_in_the_chosen_documents(tmp_path):
    words = chosen_tools(tmp_path).search_evidence(SHARED_WORDS, lambda event: None)

    found = set(re.findall(r'"doc_id": "([^"]+)"', words))
    assert found & set(CHOSEN)
    records = {p.stem for p in (tmp_path / SAMPLE_LIBRARY / "records").glob("*.md")}
    assert found <= set(CHOSEN) | records  # records chooses nothing: its whole folder


def test_a_chat_answer_citing_an_unchosen_document_is_dropped_and_a_chosen_one_kept(tmp_path):
    tools = chosen_tools(tmp_path)

    assert tools.check_quote(UNCHOSEN_QUOTE, UNCHOSEN) == str(DropReason.NO_DOCUMENT)
    assert tools.check_quote(CHOSEN_QUOTE, "backup-policy") is None


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


@pytest.mark.parametrize(
    ("name", "args"),
    [("delete_everything", {}), ("search_evidence", {"query": "x" * 201}), ("search_evidence", {})],
    ids=["not-a-tool", "invalid-query", "no-query"],
)
def test_a_refused_chat_tool_call_writes_its_guard_step_and_no_search_step(name, args):
    # #115: a refused call once read as followed by "Found 0 passages", as if a search ran.
    tools, _ = chat_tools()
    written = []

    said = tools.call(name, args, written.append, threading.Event())

    assert said.startswith("Refused")
    assert written == [("step", {"node": "guard", "text": said})]
    assert "Found" not in said and "Searched" not in said


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
    assert f"Kontrolle 1.6.2, “{GERMAN_1_6_2}”" in text  # the fake answers in German (#330)
    assert GERMAN_1_6_2_M1 in text


# Questions across controls (#210): every control's newest assessment at once


def _cite(kind: EvidenceKind, *, stale: bool = False) -> Citation:
    return Citation("doc", "1.0", "Scope", "Quoted words here.", kind, stale, None)


def _assessed(control_id: str, *requirements: RequirementVerdict) -> Assessment:
    return Assessment(
        control_id=control_id,
        profile_id=DEFAULT_PROFILE,
        verdict="partly-evidenced",
        should_score=ShouldScore(0, 0),
        requirements=list(requirements),
        answer="Answer.",
    )


def test_assessed_controls_words_sort_the_controls_by_their_counted_evidence():
    controls = load_base_framework().controls[:5]
    a, b, c, d, e = (control.id for control in controls)
    latest = {
        # documented only: a should's implemented evidence doesn't count towards the verdict
        a: _assessed(
            a,
            RequirementVerdict("M1", "must", "T.", "evidenced", "R.", (_cite("documented"),)),
            RequirementVerdict("S1", "should", "T.", "evidenced", "R.", (_cite("implemented"),)),
        ),
        # implemented evidence on a must, and a must not evidenced
        b: _assessed(
            b,
            RequirementVerdict("M1", "must", "T.", "evidenced", "R.", (_cite("implemented"),)),
            RequirementVerdict("M2", "must", "T.", "not-evidenced", "R."),
        ),
        # documented only, stale
        c: _assessed(
            c,
            RequirementVerdict(
                "M1", "must", "T.", "partly-evidenced", "R.", (_cite("documented", stale=True),)
            ),
        ),
        # nothing cited
        d: _assessed(d, RequirementVerdict("M1", "must", "T.", "not-evidenced", "R.")),
    }

    words = assessed_controls_words(controls, latest, "en")

    assert f"Documented evidence only: {a}, {c}." in words
    assert f"Stale evidence: {c}." in words
    assert f"Musts not evidenced: {b} (M2), {c} (M1), {d} (M1)." in words  # partly is not evidenced
    assert f"Not assessed yet: {e}." in words
    assert f"- {e}: not assessed yet." in words
    assert "M1 (must) evidenced, implemented" in words
    assert (
        f"- {a}: partly evidenced, proposed and waiting for your review; its counted "
        "requirements rest only on documented evidence." in words
    )
    assert "requirements rest on no cited evidence." in words


def test_a_question_across_controls_calls_assessed_controls_and_names_the_controls(client, fake):
    assess_and_review(client, "5.1.1")
    assess_and_review(client, "5.1.2")

    events = say(client, "Which controls have documented evidence only?")

    called = [m for m in fake.prompts[-1] if getattr(m, "tool_calls", None)]
    assert [call["name"] for call in called[-1].tool_calls] == [ASSESSED_CONTROLS]
    answer = reply(events)
    assert "Documented evidence only:" in answer
    assert "5.1.1:" in answer and "5.1.2:" in answer
    not_assessed = [i for i in CONTROL_IDS if i not in ("5.1.1", "5.1.2")]
    assert f"Not assessed yet: {', '.join(not_assessed)}." in answer
    assert names(events)[-1] == "done"
    # #217: the controls the answer may name, as data for the chat to link: never from the model.
    [papers] = [data for name, data in events if name == "papers"]
    assert papers == {"control_ids": CONTROL_IDS}


def test_assessed_controls_reads_only_the_signed_in_users_own_assessments(client, users, fake):
    # The other user assesses 5.1.1 on the same app; the lead assesses 5.1.2.
    read_events(client.post("/api/assessments", json={"control_id": "5.1.1"}, headers=users[1]))
    assess_and_review(client, "5.1.2")

    answer = reply(say(client, "Which controls have documented evidence only?"))

    assert "- 5.1.1: not assessed yet." in answer
    assert "- 5.1.2: " in answer and "- 5.1.2: not assessed yet." not in answer


def test_assessed_controls_reads_only_the_requests_profile(app, lead, client, fake):
    """The tool reads the request's profile (spec 5): Nordlicht's assessment isn't Alpenwerk's."""
    assess_and_review(client, "5.1.2")

    with TestClient(app, raise_server_exceptions=False, headers=lead | ALPENWERK) as alpenwerk:
        answer = reply(say(alpenwerk, "Which controls have documented evidence only?"))

    # The fake answers Alpenwerk in German (#330).
    assert "- 5.1.2: noch nicht bewertet." in answer
    assert "Nur dokumentierte Nachweise: keine." in answer


def test_explain_control_says_why_partly_evidenced_rows_roll_up_to_not_evidenced():
    # #212: the chat carries the working paper's roll-up explanation, not a disagreement.
    control = load_base_framework().controls[0]
    partly = (_cite("documented", stale=True),)
    assessment = _assessed(
        control.id,
        RequirementVerdict("M1", "must", "T.", "partly-evidenced", "R.", partly),
        RequirementVerdict("S1", "should", "T.", "partly-evidenced", "R.", partly),
    )

    words = assessed_words(control, replace(assessment, verdict="not-evidenced"), "en")

    why = "Why: Not evidenced: no must requirement is fully evidenced (1 of 1 partly evidenced)."
    assert why in words


# The conversation kept (#251): one per user and profile, its lines as the chat showed them


def current(client, **headers):
    response = client.get("/api/chat/current", headers=headers or None)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_turn_is_kept_as_the_chat_showed_it_and_its_thread_becomes_current(client):
    events = say(client, "Where do I stand?")

    kept = current(client)
    assert kept["thread_id"] == thread_of(events)
    assert kept["lines"] == [
        {"from": "lead", "text": "Where do I stand?"},
        {"from": "vouch", "text": reply(events)},
    ]


def test_a_greeting_alone_never_becomes_the_conversation_the_chat_opens_on(client, fake):
    # A greeting's counts go stale by the next visit, and a failed one would stick (#251's review).
    greet(client)
    assert current(client) == {"thread_id": None, "lines": []}
    fake.fail_with = RuntimeError("the model is down")
    greet(client)
    assert current(client) == {"thread_id": None, "lines": []}


def test_the_greeting_is_kept_without_a_lead_line_and_a_next_message_adds_to_it(client):
    thread_id = thread_of(greet(client))
    events = say(client, "Where do I stand?", thread_id=thread_id)

    lines = current(client)["lines"]
    assert [line["from"] for line in lines] == ["vouch", "lead", "vouch"]
    assert lines[-1]["text"] == reply(events)
    assert current(client)["thread_id"] == thread_id


def test_an_assessment_s_steps_and_verdict_are_kept_in_order(client):
    events = say(client, "Assess 5.1.1")

    lines = current(client)["lines"]
    froms = [line["from"] for line in lines]
    assert froms[0] == "lead" and froms[-1] == "verdict"
    assert "step" in froms and "vouch" in froms
    assert froms.index("vouch") > froms.index("step")
    [finished] = [data for name, data in events if name == "assessment"]
    assert lines[-1] == {
        "from": "verdict",
        "control_id": "5.1.1",
        "verdict": finished["verdict"],
        "status": "waiting-for-review",
        "answer_language": finished.get("answer_language"),
    }


def test_a_cross_control_answer_keeps_its_working_papers(client):
    say(client, "Which controls have documented evidence only?")

    [answer] = [line for line in current(client)["lines"] if line["from"] == "vouch"]
    assert answer["papers"] == CONTROL_IDS


def test_new_conversation_leaves_none_current_and_the_next_message_starts_one(client):
    first = thread_of(say(client, "Where do I stand?"))

    assert client.delete("/api/chat/current").status_code == 200
    assert current(client) == {"thread_id": None, "lines": []}

    second = thread_of(say(client, "What is missing for 5.1.1?"))
    assert second != first
    assert current(client)["thread_id"] == second
    assert current(client)["lines"][0] == {"from": "lead", "text": "What is missing for 5.1.1?"}


def test_another_user_sees_none_of_this_users_conversation(client, users):
    say(client, "My secret question")

    assert current(client, **users[1]) == {"thread_id": None, "lines": []}


def test_an_unreadable_conversation_opens_empty_with_a_note(client, monkeypatch):
    say(client, "Where do I stand?")

    def unreadable(self, thread_id):
        raise RuntimeError("the store can't be read")

    monkeypatch.setattr(Memory, "chat_lines", unreadable)
    kept = current(client)
    assert kept["lines"] == [] and kept["thread_id"] is None
    assert kept["note"] == api.CHAT_HISTORY_UNAVAILABLE


def test_the_chat_still_answers_when_its_conversation_cannot_be_kept(client, monkeypatch):
    def broken(self, *args):
        raise RuntimeError("the database is unreachable")

    monkeypatch.setattr(Memory, "add_chat_turn", broken)
    monkeypatch.setattr(Memory, "set_current_chat", broken)
    events = say(client, "Where do I stand?")

    assert names(events)[-1] == "done"
    assert reply(events)


# German verdict words in the chat's tools (#208): design.md's words, from the report's table

GERMAN_VERDICTS = [
    "Nachgewiesen",
    "Teilweise nachgewiesen",
    "Nicht nachgewiesen",
    "Nicht anwendbar",
]
ENGLISH_VERDICT = re.compile(r"\b(?:partly |not )?evidenced\b|\bnot applicable\b", re.IGNORECASE)


def test_verdict_words_follow_the_profile_language_where_the_report_has_words():
    assert verdict_words_for("partly-evidenced", "de") == "Teilweise nachgewiesen"
    assert verdict_words_for("not-applicable", "de") == "Nicht anwendbar"
    assert verdict_words_for("partly-evidenced", "en") == "partly evidenced"
    assert verdict_words_for("partly-evidenced", "fr") == "partly evidenced"  # no table


def test_alpenwerks_chat_prompt_names_the_german_verdict_words_and_nordlichts_doesnt():
    page = PageContext("dossier")
    german = str(chat_prompt(load_named_profile("alpenwerk"), page).content)
    english = str(chat_prompt(load_named_profile(DEFAULT_PROFILE), page).content)

    assert all(word in german for word in GERMAN_VERDICTS)
    assert not any(word in english for word in GERMAN_VERDICTS)
    assert CHAT_PROMPT_VERSION == "chat-8"


def tool_words(fake: CannedChatModel, name: str) -> str:
    """What the named tool last said to the model this test (its ToolMessage)."""
    said = [m for prompt in fake.prompts for m in prompt if m.type == "tool" and m.name == name]
    assert said, f"{name} was never called"
    return str(said[-1].content)


def test_explain_control_on_alpenwerk_writes_the_german_verdict_words(app, lead, fake):
    with TestClient(app, raise_server_exceptions=False, headers=lead | ALPENWERK) as client:
        assess_and_review(client, "1.6.2")
        say(client, "Explain 1.6.2")

    words = tool_words(fake, "explain_control")
    summary = words.splitlines()[0]
    assert any(f"its newest assessment is {word}," in summary for word in GERMAN_VERDICTS)
    assert not ENGLISH_VERDICT.search(summary)
    findings = json.loads(re.search(r"<findings>\n(.*?)\n</findings>", words, re.DOTALL)[1])
    assert findings and all(f["verdict"] in GERMAN_VERDICTS for f in findings)


def test_assessed_controls_and_start_assessment_on_alpenwerk_write_the_german_words(
    app, lead, fake
):
    with TestClient(app, raise_server_exceptions=False, headers=lead | ALPENWERK) as client:
        say(client, "Assess 1.6.2")
        say(client, "Which controls have documented evidence only?")

    finished = tool_words(fake, START_ASSESSMENT)
    assert any(f"has finished: {word}." in finished for word in GERMAN_VERDICTS)
    assert not ENGLISH_VERDICT.search(finished.split(". The verdict")[0])
    [line] = [
        x for x in tool_words(fake, ASSESSED_CONTROLS).splitlines() if x.startswith("- 1.6.2")
    ]
    head, requirements = line.split("; its counted requirements rest ")
    assert any(
        head == f"- 1.6.2: {word}, proposed and waiting for your review" for word in GERMAN_VERDICTS
    )
    assert not ENGLISH_VERDICT.search(requirements.split(" evidence. ", 1)[1])
    assert any(word in requirements for word in GERMAN_VERDICTS)


def test_progress_leaves_a_control_not_applicable_as_a_whole_out_of_its_counts():
    # #454: 1.1.1 is not applicable under the profile, so 11 controls are counted.
    controls = WITHOUT_1_1_1.isa_edition().controls
    nothing = overview(WITHOUT_1_1_1, controls, {})
    some = overview(WITHOUT_1_1_1, controls, {"4.1.2": seeded("4.1.2", "evidenced", "accepted")})

    assert progress_words(
        nothing["readiness"]["controls"], nothing["by_status"], None, nothing["next_step"]
    ).startswith("You have not assessed any of the 11 controls yet.")
    said = progress_words(some["readiness"]["controls"], some["by_status"], None, some["next_step"])
    assert "Of the 11 controls, 1 is accepted, and 10 are not assessed." in said
    assert "not applicable" not in said


# A control not applicable as a whole (#454 review): nothing in the chat counts, lists, suggests
# or assesses it.
EVERY_CONTROL_OUT = load_named_profile(DEFAULT_PROFILE).model_copy(
    update={
        "not_applicable": tuple(NotApplicable(control=c, reason="Not ours.") for c in CONTROL_IDS)
    }
)


def test_the_assessed_controls_leave_a_control_not_applicable_as_a_whole_out(monkeypatch):
    tools, _ = chat_tools(WITHOUT_1_1_1)
    newest = {"1.1.1": seeded("1.1.1", "evidenced", "accepted")}
    monkeypatch.setattr(chat_agent, "newest_by_control", lambda *_: newest)
    written: list = []

    said = tools.assessed_controls(written.append)

    [papers] = [data for name, data in written if name == "papers"]
    assert papers == {"control_ids": [c for c in CONTROL_IDS if c != "1.1.1"]}
    assert "- 1.1.1" not in said
    assert said.splitlines()[0] == "Each control's newest assessment under this profile:"
    assert any(x.startswith("- 1.6.1: not assessed yet.") for x in said.splitlines())
    [not_assessed] = [x for x in said.splitlines() if x.startswith("Not assessed yet:")]
    assert "1.1.1" not in not_assessed


def test_explaining_a_control_not_applicable_as_a_whole_says_so_and_suggests_no_assessment():
    tools, _ = chat_tools(WITHOUT_1_1_1)
    written: list = []

    said = tools.explain_control("1.1.1", None, written.append)

    assert said.startswith("1.1.1 is not applicable in this profile.")
    assert WHOLE_REASON in said
    assert ("suggestion", {"control_id": "1.1.1", "action": "assess"}) not in written


def test_the_chat_refuses_to_assess_a_control_not_applicable_as_a_whole_and_counts_nothing(
    monkeypatch,
):
    tools, recorded = chat_tools(WITHOUT_1_1_1)
    counted: list = []
    monkeypatch.setattr(tools.spending, "start", lambda *a, **k: counted.append(a))
    written: list = []

    said = tools.start_assessment("1.1.1", written.append, threading.Event())

    assert said == "1.1.1 is not applicable in this profile."
    assert counted == [] and recorded == [] and written == []
    assert not is_held(tools.running, tools.memory.scope)


def test_progress_with_every_control_not_applicable_says_no_control_applies():
    controls = EVERY_CONTROL_OUT.isa_edition().controls
    view = overview(EVERY_CONTROL_OUT, controls, {})

    said = progress_words(view["readiness"]["controls"], view["by_status"], None, view["next_step"])

    assert said == "No control applies under this profile, so there is nothing to assess."
