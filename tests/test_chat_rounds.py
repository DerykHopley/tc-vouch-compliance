"""Ask Vouch answers questions about the round comparison (#581): the chat's `compare_rounds`
tool over the round comparison (#571), its words, its refusals, and a conversation on the fake.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import test_memory
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from reviewing import accepted
from test_api import read_events
from test_chat_agent import chat_tools, reply
from test_database import empty_database  # noqa: F401 (a fixture: postgres's)
from test_dossier import seeded
from test_memory import DEMO_CONTROL, app_on, assess
from test_rounds import CONTROLS, IDS, frozen_as

from vouch import rounds
from vouch.chat import PageContext
from vouch.chat_agent import (
    COMPARE_ROUNDS,
    NO_ROUND_CLOSED,
    RoundsReader,
    chat_prompt,
    compare_rounds_words,
)
from vouch.chat_fake import CannedChatModel
from vouch.graph import Verdict
from vouch.model_choice import ModelChoice
from vouch.profile import DEFAULT_PROFILE, load_named_profile
from vouch.rounds import ListedRound

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

CLOSED = datetime(2026, 6, 30, 17, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
EVAL_CASE = Path(__file__).parent.parent / "evals" / "chat" / "round-comparison.json"


def listed_rounds(*, fresh_before_first: bool = False) -> list[ListedRound]:
    """First round closed (before the fresh start when asked), Q2 2026 closed, Q3 2026 open."""
    first = frozen_as({IDS[0]: ("accepted", "evidenced")})
    second = frozen_as(
        {IDS[0]: ("accepted", "partly-evidenced"), IDS[1]: ("accepted", "evidenced")}
    )
    return [
        ListedRound(
            1, "First round", None, CLOSED - 90 * 24 * HOUR, fresh_before_first, tuple(first)
        ),
        ListedRound(2, "Q2 2026", CLOSED - 90 * 24 * HOUR, CLOSED, False, tuple(second)),
        ListedRound(3, "Q3 2026", CLOSED, None, False),
    ]


def reader(listed: list[ListedRound], seen: list[int] | None = None) -> RoundsReader:
    """A reader over `listed` comparing each with now: nothing assessed now; one item done and
    one question answered on the first control."""

    def compared(chosen: ListedRound) -> tuple[Any, list[Any], list[Any]]:
        if seen is not None:
            seen.append(chosen.number)
        assert chosen.closed_at is not None
        comparison = rounds.round_comparison(chosen.results, _controls(), _controls(), {})
        later = chosen.closed_at + HOUR
        done = [rounds.Done(control_id=IDS[0], requirement_id=f"{IDS[0]}-M1", at=later)]
        answered = [rounds.Answered(control_id=IDS[0], question_id=7, at=later)]
        return comparison, done, answered

    return RoundsReader(listed=lambda: listed, compared=compared)


def _controls():
    return CONTROLS


def tools_on(listed: list[ListedRound], seen: list[int] | None = None):
    tools, _ = chat_tools()
    tools.rounds = reader(listed, seen)
    return tools


def call(tools, args: dict[str, Any]) -> tuple[str, list[tuple[str, Any]]]:
    written: list[tuple[str, Any]] = []
    said = tools.call(COMPARE_ROUNDS, args, written.append, None)
    return said, written


# --- Which round ------------------------------------------------------------------------------


def test_with_no_round_it_compares_with_the_default_choice():
    seen: list[int] = []

    said, _ = call(tools_on(listed_rounds(), seen), {})

    assert seen == [2]
    assert "review round 2, “Q2 2026”" in said


@pytest.mark.parametrize("asked", ["1", 1, "First round", "first ROUND "])
def test_with_a_number_or_a_name_it_compares_with_that_round(asked):
    seen: list[int] = []

    said, _ = call(tools_on(listed_rounds(), seen), {"round": asked})

    assert seen == [1]
    assert "review round 1, “First round”" in said


def test_a_name_the_lead_shortens_finds_its_one_round():
    seen: list[int] = []

    call(tools_on(listed_rounds(), seen), {"round": "Q2"})

    assert seen == [2]


# --- Refusals ----------------------------------------------------------------------------------


def test_with_no_closed_round_it_says_so_and_compares_nothing():
    seen: list[int] = []
    only_open = [ListedRound(1, "First round", None, None, False)]

    said, written = call(tools_on(only_open, seen), {})

    assert said == NO_ROUND_CLOSED
    assert seen == []
    assert not [e for e in written if e[0] == "papers"]


def test_an_unknown_round_says_so_and_names_the_rounds():
    seen: list[int] = []

    said, _ = call(tools_on(listed_rounds(), seen), {"round": "Q4 2030"})

    assert said.startswith("There is no review round “Q4 2030”.")
    assert "1 “First round”, 2 “Q2 2026” and 3 “Q3 2026”" in said
    assert seen == []


def test_the_open_round_says_close_it_first():
    said, _ = call(tools_on(listed_rounds()), {"round": "3"})

    assert said == rounds.STILL_OPEN


def test_a_round_before_the_fresh_start_says_so():
    seen: list[int] = []

    said, _ = call(tools_on(listed_rounds(fresh_before_first=True), seen), {"round": "1"})

    assert said == rounds.BEFORE_FRESH_START
    assert seen == []


def test_without_rounds_to_read_the_tool_declines():
    tools, _ = chat_tools()

    said, _ = call(tools, {})

    assert said == NO_ROUND_CLOSED


# --- Its words -----------------------------------------------------------------------------------


def test_its_words_carry_counts_readiness_changes_scope_and_whats_done_since():
    listed = listed_rounds()
    controls = _controls()
    chosen = listed[1]  # IDS[0] partly evidenced, IDS[1] evidenced, both accepted
    now = {
        IDS[0]: _assessment(IDS[0], "evidenced"),
        IDS[2]: _assessment(IDS[2], "not-evidenced"),
    }
    applicable = [c for c in controls if c.id != IDS[3]]
    then = tuple(f for f in chosen.results if f.control_id != IDS[4])  # IDS[4] new since
    comparison = rounds.round_comparison(then, controls, applicable, now)
    done = [rounds.Done(control_id=IDS[0], requirement_id=f"{IDS[0]}-M1", at=CLOSED + HOUR)]
    answered = [rounds.Answered(control_id=IDS[2], question_id=3, at=CLOSED + HOUR)]

    said = compare_rounds_words(chosen, comparison, done, answered, "en")

    assert said.splitlines() == [
        "Now compared with review round 2, “Q2 2026”, closed on 30 June 2026.",
        "Better: 1, worse: 1, new: 1, unchanged: 7.",
        (
            "Readiness: 2 controls accepted then, 2 now. Evidenced 1 → 1, partly evidenced 1 → 0, "
            "not evidenced 0 → 1."
        ),
        "Changed controls:",
        f"- {IDS[0]}: better, partly evidenced → evidenced.",
        f"- {IDS[1]}: worse, evidenced → not accepted.",
        f"- {IDS[2]}: new, not accepted → not evidenced.",
        f"Scope changes: {IDS[3]} out of scope since; {IDS[4]} new since.",
        f"Remediation items done since: {IDS[0]}-M1 on {IDS[0]}.",
        f"Questions answered since: one on {IDS[2]}.",
    ]


def test_with_nothing_changed_its_words_say_so():
    chosen = listed_rounds()[1]
    comparison = rounds.round_comparison(chosen.results, _controls(), _controls(), {})
    comparison["controls"] = [{**c, "change": "unchanged"} for c in comparison["controls"]]

    said = compare_rounds_words(chosen, comparison, [], [], "en")

    assert "Changed controls: none." in said
    assert "Scope changes: none." in said
    assert "Remediation items done since: none." in said
    assert "Questions answered since: none." in said


def test_a_proposed_control_says_it_waits_for_review():
    chosen = listed_rounds()[1]
    comparison = rounds.round_comparison(chosen.results, _controls(), _controls(), {})
    row = next(c for c in comparison["controls"] if c["control_id"] == IDS[1])
    row["proposed"] = True

    said = compare_rounds_words(chosen, comparison, [], [], "en")

    assert f"- {IDS[1]}: worse, evidenced → not accepted; proposed, waiting for review." in said


def test_its_verdicts_are_in_the_profiles_language():
    chosen = listed_rounds()[1]
    comparison = rounds.round_comparison(chosen.results, _controls(), _controls(), {})

    said = compare_rounds_words(chosen, comparison, [], [], "de")

    assert f"- {IDS[1]}: worse, Nachgewiesen → not accepted." in said


def test_the_controls_it_names_are_linked_through_a_papers_event():
    _, written = call(tools_on(listed_rounds()), {"round": "Q2 2026"})

    (papers,) = [data for name, data in written if name == "papers"]
    assert papers["control_ids"][:2] == [IDS[0], IDS[1]]
    assert next(name for name, _ in written) == "step"


def test_the_prompt_says_when_to_use_it():
    prompt = chat_prompt(load_named_profile(DEFAULT_PROFILE), PageContext(page="dossier"))

    assert f"`{COMPARE_ROUNDS}`" in str(prompt.content)
    assert "since a review round" in str(prompt.content)


def _assessment(control_id: str, verdict: Verdict):
    return seeded(control_id, verdict, "accepted")


# --- The conversation, on the fake ----------------------------------------------------------------


@pytest.fixture
def fake(monkeypatch) -> CannedChatModel:
    model = CannedChatModel()

    def for_chat(self, chosen, callbacks=None, answer_language="en"):
        model.language = answer_language
        return model

    monkeypatch.setattr(ModelChoice, "for_chat", for_chat)
    return model


def test_what_improved_since_q2_calls_the_tool_and_answers_with_the_controls_linked(
    database_url, users, fake
):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        assert client.post("/api/rounds/close", headers=alice).status_code == 200
        assert (
            client.post("/api/rounds", json={"name": "Q2 2026"}, headers=alice).status_code == 200
        )
        verdict = accepted(client, assess(client, alice), headers=alice)["verdict"]
        assert client.post("/api/rounds/close", headers=alice).status_code == 200
        assert (
            client.post("/api/rounds", json={"name": "Q3 2026"}, headers=alice).status_code == 200
        )
        body = {"page": "dossier", "message": "What improved since the First round?"}
        events = read_events(client.post("/api/chat", json=body, headers=alice))

    asked = fake.prompts[0][-1]
    assert "What improved since the First round?" in str(asked.content)
    steps = [d for n, d in events if n == "step"]
    assert any(s["node"] == COMPARE_ROUNDS for s in steps)
    text = reply(events)
    assert "review round 1, “First round”" in text
    assert f"- {DEMO_CONTROL}: new, not accepted → {verdict.replace('-', ' ')}." in text
    (papers,) = [d for n, d in events if n == "papers"]
    assert DEMO_CONTROL in papers["control_ids"]


# --- The eval case (a scripted fixture until the eval scores chat) --------------------------------


def test_the_eval_case_names_its_question_tool_and_expected_controls():
    case = json.loads(EVAL_CASE.read_text())

    assert case["question"] == "What improved since Q2 2026?"
    assert case["expects_tool"] == {"name": COMPARE_ROUNDS, "args": {"round": "Q2 2026"}}
    made = CannedChatModel(tools=[COMPARE_ROUNDS])._tool_call(HumanMessage(case["question"]), [])
    assert made is not None
    assert {"name": made["name"], "args": made["args"]} == case["expects_tool"]
    said, _ = call(tools_on(listed_rounds()), case["expects_tool"]["args"])
    for control_id in case["expects_cited_controls"]:
        assert f"- {control_id}:" in said


def test_since_the_last_round_asks_for_the_default_choice():
    made = CannedChatModel(tools=[COMPARE_ROUNDS])._tool_call(
        HumanMessage("Which controls got worse since the last round?"), []
    )

    assert made is not None
    assert (made["name"], made["args"]) == (COMPARE_ROUNDS, {})


def test_a_round_that_is_neither_text_nor_a_number_is_refused_naming_what_it_needs():
    said, written = call(tools_on(listed_rounds()), {"round": {"name": "Q2"}})

    assert said.startswith(f"Refused a call to {COMPARE_ROUNDS}: its arguments are invalid.")
    assert "number or name" in said
    assert written[0][0] == "step"
