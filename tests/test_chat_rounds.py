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
from test_chat_fake import answer_to, english_words
from test_database import empty_database  # noqa: F401 (a fixture: postgres's)
from test_dossier import seeded
from test_memory import DEMO_CONTROL, app_on, assess
from test_rounds import AT, CONTROLS, IDS, frozen_as

from vouch import rounds
from vouch.chat import PageContext
from vouch.chat_agent import (
    COMPARE_ROUNDS,
    NO_ROUND_CLOSED,
    RoundsReader,
    chat_prompt,
    compare_rounds_words,
    unknown_round,
    wrong_language,
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
        comparison = rounds.round_comparison(
            chosen.results, _controls(), _controls(), {}, added=[], closed_at=AT
        )
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
    comparison = rounds.round_comparison(then, controls, applicable, now, added=[], closed_at=AT)
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
    comparison = rounds.round_comparison(
        chosen.results, _controls(), _controls(), {}, added=[], closed_at=AT
    )
    comparison["controls"] = [{**c, "change": "unchanged"} for c in comparison["controls"]]

    said = compare_rounds_words(chosen, comparison, [], [], "en")

    assert "Changed controls: none." in said
    assert "Scope changes: none." in said
    assert "Remediation items done since: none." in said
    assert "Questions answered since: none." in said


def test_a_proposed_control_says_it_waits_for_review():
    chosen = listed_rounds()[1]
    comparison = rounds.round_comparison(
        chosen.results, _controls(), _controls(), {}, added=[], closed_at=AT
    )
    row = next(c for c in comparison["controls"] if c["control_id"] == IDS[1])
    row["proposed"] = True

    said = compare_rounds_words(chosen, comparison, [], [], "en")

    assert f"- {IDS[1]}: worse, evidenced → not accepted; proposed, waiting for review." in said


def test_its_verdicts_are_in_the_profiles_language():
    chosen = listed_rounds()[1]
    comparison = rounds.round_comparison(
        chosen.results, _controls(), _controls(), {}, added=[], closed_at=AT
    )

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


def test_a_named_round_when_there_are_none_says_no_round_is_closed() -> None:
    from vouch.chat_agent import NO_ROUND_CLOSED, unknown_round

    assert unknown_round("Q2", []) == NO_ROUND_CLOSED


def test_round_n_and_review_round_n_name_round_n() -> None:
    from datetime import UTC, datetime

    from vouch.chat_agent import chosen_round
    from vouch.rounds import ListedRound

    closed = datetime(2026, 6, 30, tzinfo=UTC)
    listed = [
        ListedRound(
            number=1, name="First round", opened_at=None, closed_at=closed, before_fresh_start=False
        )
    ]
    for asked in ("round 1", "Review round 1"):
        assert chosen_round(listed, asked) == listed[0]


@pytest.mark.parametrize(
    ("asked", "args"),
    [
        ("Whats changed between the first and this round?", {"round": "first"}),
        ("What changed between the first round and now?", {"round": "first"}),
        ("How has this round improved on the first round?", {"round": "first"}),
        ("What is better in this round?", {}),
        ("Was hat sich seit der ersten Runde verändert?", {"round": "first"}),
        ("What improved since Q2 2026?", {"round": "Q2 2026"}),
        ("Which controls got worse since the last round?", {}),
    ],
)
def test_the_fake_sends_questions_about_rounds_to_the_comparison(asked, args):
    made = CannedChatModel(tools=[COMPARE_ROUNDS])._tool_call(HumanMessage(asked), [])

    assert made is not None
    assert (made["name"], made["args"]) == (COMPARE_ROUNDS, args)


def test_first_names_the_first_closed_round_unless_a_round_has_that_name() -> None:
    from vouch.chat_agent import chosen_round
    from vouch.rounds import ListedRound

    at = datetime(2026, 6, 30, tzinfo=UTC)
    old = ListedRound(
        number=1, name="Kick-off", opened_at=None, closed_at=at, before_fresh_start=True
    )
    first = ListedRound(
        number=2, name="Q2 2026", opened_at=at, closed_at=at, before_fresh_start=False
    )
    now = ListedRound(
        number=3, name="Q3 2026", opened_at=at, closed_at=None, before_fresh_start=False
    )
    for asked in ("first", "the first round", "First round", "erste Runde"):
        assert chosen_round([old, first, now], asked) == first


# --- On a German profile, on the fake (#582) ------------------------------------------------------


def _compared_words() -> str:
    """test_its_words_carry_counts…'s comparison on `de`, one more question answered and its
    new control proposed."""
    chosen = listed_rounds()[1]
    controls = _controls()
    now = {
        IDS[0]: _assessment(IDS[0], "evidenced"),
        IDS[2]: _assessment(IDS[2], "not-evidenced"),
    }
    applicable = [c for c in controls if c.id != IDS[3]]
    then = tuple(f for f in chosen.results if f.control_id != IDS[4])
    comparison = rounds.round_comparison(then, controls, applicable, now, added=[], closed_at=AT)
    next(c for c in comparison["controls"] if c["control_id"] == IDS[2])["proposed"] = True
    done = [rounds.Done(control_id=IDS[0], requirement_id=f"{IDS[0]}-M1", at=CLOSED + HOUR)]
    answered = [
        rounds.Answered(control_id=IDS[2], question_id=3, at=CLOSED + HOUR),
        rounds.Answered(control_id=IDS[0], question_id=4, at=CLOSED + HOUR),
    ]
    return compare_rounds_words(chosen, comparison, done, answered, "de")


def _nothing_changed_words() -> str:
    chosen = listed_rounds()[1]
    comparison = rounds.round_comparison(
        chosen.results, _controls(), _controls(), {}, added=[], closed_at=AT
    )
    comparison["controls"] = [{**c, "change": "unchanged"} for c in comparison["controls"]]
    return compare_rounds_words(chosen, comparison, [], [], "de")


ROUND_WORDS = {
    "compared": _compared_words,
    "nothing changed": _nothing_changed_words,
    "no round closed": lambda: NO_ROUND_CLOSED,
    "unknown round": lambda: unknown_round("Q4 2030", listed_rounds()),
    "unknown round, one listed": lambda: unknown_round("Q4 2030", listed_rounds()[:1]),
    "still open": lambda: rounds.STILL_OPEN,
    "before the fresh start": lambda: rounds.BEFORE_FRESH_START,
}


PINNED = ("compared", "unknown round")  # their German is checked word for word below


@pytest.mark.parametrize("case", [c for c in ROUND_WORDS if c not in PINNED])
def test_on_de_the_fakes_answer_to_each_round_sentence_is_german(case):
    answer = answer_to(ROUND_WORDS[case](), name=COMPARE_ROUNDS)

    assert wrong_language(answer, "de") is None
    assert english_words(answer) == []


@pytest.mark.parametrize("case", ROUND_WORDS)
def test_on_en_and_fr_the_fakes_answer_to_each_round_sentence_is_the_words(case):
    words = ROUND_WORDS[case]()

    assert answer_to(words, "en", COMPARE_ROUNDS) == words
    assert answer_to(words, "fr", COMPARE_ROUNDS) == words


def test_on_de_the_comparison_reads_in_the_reports_words():
    answer = answer_to(_compared_words(), name=COMPARE_ROUNDS)

    assert answer.splitlines() == [
        "Jetzt verglichen mit Prüfrunde 2, “Q2 2026”, abgeschlossen am 30. Juni 2026.",
        "Besser: 1, schlechter: 1, neu: 1, unverändert: 7.",
        (
            "Bereitschaft: 2 Kontrollen damals akzeptiert, 2 jetzt. Nachgewiesen 1 → 1, "
            "Teilweise nachgewiesen 1 → 0, Nicht nachgewiesen 0 → 1."
        ),
        "Geänderte Kontrollen:",
        f"- {IDS[0]}: besser, Teilweise nachgewiesen → Nachgewiesen.",
        f"- {IDS[1]}: schlechter, Nachgewiesen → Nicht akzeptiert.",
        (
            f"- {IDS[2]}: neu, Nicht akzeptiert → Nicht nachgewiesen; vorgeschlagen und wartet "
            "auf Ihre Prüfung."
        ),
        (
            f"Änderungen im Anwendungsbereich: {IDS[3]} seitdem nicht mehr im "
            f"Anwendungsbereich; {IDS[4]} seitdem neu im Anwendungsbereich."
        ),
        f"Seitdem erledigte Maßnahmen: {IDS[0]}-M1 zu {IDS[0]}.",
        f"Seitdem beantwortete Fragen: eine zu {IDS[2]}, eine zu {IDS[0]}.",
    ]


def test_on_de_nothing_changed_says_keine():
    answer = answer_to(_nothing_changed_words(), name=COMPARE_ROUNDS)

    assert "Geänderte Kontrollen: keine." in answer
    assert "Änderungen im Anwendungsbereich: keine." in answer
    assert "Seitdem erledigte Maßnahmen: keine." in answer
    assert "Seitdem beantwortete Fragen: keine." in answer


def test_on_de_an_unknown_round_names_the_rounds_as_they_are():
    answer = answer_to(ROUND_WORDS["unknown round"](), name=COMPARE_ROUNDS)

    assert answer == (
        "Die Prüfrunde “Q4 2030” gibt es nicht. Die Prüfrunden sind 1 “First round”, "
        "2 “Q2 2026” und 3 “Q3 2026”."
    )
