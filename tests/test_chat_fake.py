"""The chat's canned fake (vouch.chat_fake), on its own: what it answers a tool's result with."""

import json
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast, get_args

import pytest
import test_memory
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.store.memory import InMemoryStore
from test_api import read_events
from test_memory import app_on

from vouch.allow_list import allowed_tools
from vouch.api import PROFILE_HEADER
from vouch.chat_agent import (
    ANSWER_REFUSED,
    CITE_THEM,
    EXPLAIN_CONTROL,
    SEARCH_EVIDENCE,
    START_ASSESSMENT,
    ChatTools,
    SignOff,
    assessed_controls_words,
    assessed_words,
    not_assessed_words,
    progress_words,
    sign_off_words,
    unknown_control,
    wrong_language,
)
from vouch.chat_citations import CITE
from vouch.chat_fake import CHAT_REPLY, CannedChatModel, _cited, german_answer
from vouch.citations import Citation
from vouch.dossier import NextAction, NextStep
from vouch.graph import (
    Assessment,
    RequirementVerdict,
    Status,
    Verdict,
    memory_checkpointer,
    roll_up,
    should_score,
)
from vouch.guards import as_data
from vouch.isa import IsaControl
from vouch.language import LANGUAGES, mismatch, without_quotes, written_in
from vouch.memory import Memory
from vouch.model_choice import MemoryModelStore, ModelChoice
from vouch.profile import load_named_profile
from vouch.runs import ASSESSMENT_FAILED, RUNNING_ALREADY, AssessmentRun, RunningAssessments
from vouch.settings import DEMO_SCRIPT_DE, Settings
from vouch.spending import MemorySpendingStore, SpendingGuard, UsageMeter

users = test_memory.users

PASSAGES = [
    {
        "doc_id": "encryption-standard",
        "heading": "Laptops",
        "text": "Every laptop uses full-disk encryption.",
    }
]


def test_the_evidence_reply_shows_its_citations_and_no_prompt_text():
    # #292: the tool's words tell the model how to cite; the fake used to echo that to the lead.
    tool_words = f"Found 1 passage for “encryption”. {CITE_THEM}\n{as_data(PASSAGES)}"
    call = {"name": SEARCH_EVIDENCE, "args": {"query": "encryption"}, "id": "c1"}
    messages = [
        HumanMessage("Is there evidence of encryption?"),
        AIMessage("", tool_calls=[call]),
        ToolMessage(tool_words, tool_call_id="c1", name=SEARCH_EVIDENCE),
    ]

    answer = CannedChatModel().invoke(messages).text

    assert "chat prompt" not in answer
    assert CITE_THEM not in answer
    assert (
        '<cite doc="encryption-standard">Every laptop uses full-disk encryption.</cite>' in answer
    )
    assert answer.startswith("Found 1 passage for “encryption”.")


def test_on_a_german_profile_the_fake_answers_in_german():
    # #233: a real model answers in the profile's language; the fake now does too, for `de`.
    answer = CannedChatModel(language="de").invoke([HumanMessage("Wie stehen wir da?")]).text

    assert mismatch(answer, "de") is None
    assert written_in(answer) == "de"


def test_on_any_other_profile_the_fake_answers_in_english():
    for language in ("en", "fr"):
        answer = CannedChatModel(language=language).invoke([HumanMessage("Hello")]).text
        assert answer == CHAT_REPLY


def test_through_the_api_on_alpenwerk_the_chat_answers_in_german(users):
    lead = users[0]
    with TestClient(app_on(None), headers={**lead, PROFILE_HEADER: "alpenwerk"}) as client:
        events = read_events(client.post("/api/chat", json={"page": "dossier", "message": "Hallo"}))

    answer = "".join(d["text"] for n, d in events if n == "token")
    assert mismatch(answer, "de") is None and answer


def refusal(text: str = "Your last answer was refused.") -> HumanMessage:
    return HumanMessage(text, name=ANSWER_REFUSED)


def test_asked_again_after_a_tool_call_the_fake_passes_the_tool_result_on_again():
    # #208: the turn keeps its content, not the canned "Thank you for your question"; on `de`,
    # the same German answer as before the refusal (#330).
    call = {"name": SEARCH_EVIDENCE, "args": {"query": "encryption"}, "id": "c1"}
    tool_words = f"Found 1 passage for “encryption”. {CITE_THEM}\n{as_data(PASSAGES)}"
    messages = [
        HumanMessage("Is there evidence of encryption?"),
        AIMessage("", tool_calls=[call]),
        ToolMessage(tool_words, tool_call_id="c1", name=SEARCH_EVIDENCE),
    ]
    fake = CannedChatModel(language="de")
    first = fake.invoke(messages).text

    answer = fake.invoke([*messages, AIMessage(first), refusal()]).text

    assert answer == first
    assert answer.startswith("1 Textstelle")
    assert '<cite doc="encryption-standard">' in answer


def test_asked_again_with_no_tool_result_the_fake_gives_its_reply_in_the_profile_language():
    messages = [HumanMessage("Hallo"), AIMessage("Hello there."), refusal()]

    answer = CannedChatModel(language="de").invoke(messages).text

    assert written_in(answer) == "de"


# The fake's German answer to a tool's words on a German profile (#330): the tools speak English
# to the model; the fake, standing in for a model that answers in German, says it in German.

ALPENWERK = load_named_profile("alpenwerk")
DEMO_DE = json.loads(DEMO_SCRIPT_DE.read_text())


def demo_assessment(status: Status = "waiting-for-review", **verdicts: Verdict) -> Assessment:
    """4.1.2's assessment as demo-de.json judges it on Alpenwerk; `verdicts` overrides a
    requirement's verdict by id, and `status` sets its review state."""
    control = ALPENWERK.isa_control("4.1.2")
    texts = {r.id: r for r in ALPENWERK.requirements_for(control).applicable}
    requirements = [
        RequirementVerdict(
            id=judged["requirement_id"],
            column=texts[judged["requirement_id"]].column,
            text=texts[judged["requirement_id"]].text,
            verdict=verdicts.get(judged["requirement_id"], judged["verdict"]),
            reason=judged["reason"],
            citations=tuple(
                Citation(c["doc_id"], "1.0", "Anmeldung", c["quote"], "documented", False, None)
                for c in judged["citations"]
            ),
        )
        for judged in DEMO_DE["judge"]["requirements"]
    ]
    return Assessment(
        control_id="4.1.2",
        profile_id="alpenwerk",
        verdict=roll_up(requirements),
        should_score=should_score(requirements),
        requirements=requirements,
        answer="",
        answer_language="de",
        status=status,
    )


def control(control_id: str) -> IsaControl:
    return ALPENWERK.isa_control(control_id)


def controls() -> list[IsaControl]:
    return list(ALPENWERK.isa_edition().controls)


def by_status(**counts: int) -> dict:
    statuses = ("accepted", "rejected", "waiting-for-review", "not-assessed")
    return {s: counts.get(s.replace("-", "_"), 0) for s in statuses}


def alpenwerk_tools(**sources: Path | None) -> ChatTools:
    """The chat's tools on Alpenwerk, on the fakes, as the API builds them: their words are the
    real tools' words. `sources` points an evidence source at another folder (a missing one is
    unreadable), or with None leaves it out of the profile."""
    allowed = dict(cast(dict[str, Any], ALPENWERK.evidence_sources))
    for source, folder in sources.items():
        if folder is None:
            allowed.pop(source)
        else:
            allowed[source] = allowed[source].model_copy(update={"folder": folder})
    profile = ALPENWERK.model_copy(update={"evidence_sources": allowed})
    memory = Memory(InMemoryStore(), f"lead-{uuid.uuid4().hex[:8]}", profile.id)
    run = AssessmentRun(
        ModelChoice(Settings(), MemoryModelStore()),
        None,
        allowed_tools(profile),
        memory_checkpointer(),
        UsageMeter(None),
        memory.scope,
    )
    spending = SpendingGuard(MemorySpendingStore())
    return ChatTools(profile, memory, run, spending, RunningAssessments(), lambda c, u: None)


def ignore(event: tuple[str, dict]) -> None:
    """A tool's events, not needed here."""


def finished() -> str:
    """start_assessment's words for 4.1.2, assessed on demo-de.json."""
    words = alpenwerk_tools().start_assessment("4.1.2", ignore, threading.Event())
    assert "4.1.2" in words and words != ASSESSMENT_FAILED
    return words


def searched(query: str, **sources: Path | None) -> str:
    """search_evidence's words for the query on Alpenwerk's documents."""
    return alpenwerk_tools(**sources).search_evidence(query, ignore)


UNREADABLE = Path("/nonexistent-folder-330")


def next_step(action: NextAction) -> NextStep:
    return {"control_id": "1.1.1", "action": action}


def signed_off(sign_off: SignOff, assessed: bool) -> Callable[[], str]:
    return lambda: sign_off_words("4.1.2", sign_off, assessed=assessed)


LAST_WORKED = datetime(2026, 3, 3, 9, tzinfo=UTC)
STALE = Citation("protokoll", "2.0", "Log", "Das Protokoll von 2024.", "implemented", True, "old")


def _stale_and_implemented() -> str:
    return assessed_controls_words(
        controls(),
        {
            "4.1.2": demo_assessment("accepted", M1="evidenced"),
            "1.1.1": replace(
                demo_assessment(),
                control_id="1.1.1",
                requirements=[
                    RequirementVerdict("M1", "must", "Text.", "evidenced", "Weil.", (STALE,)),
                    RequirementVerdict("S1", "should", "Text.", "not-evidenced", "Weil."),
                ],
                verdict="evidenced",
            ),
            "1.6.1": replace(
                demo_assessment("rejected"),
                control_id="1.6.1",
                requirements=[RequirementVerdict("M1", "must", "Text.", "not-evidenced", "Weil.")],
                verdict="not-evidenced",
            ),
        },
        "de",
    )


TOOL_WORDS = {
    "progress none assessed": lambda: progress_words(
        by_status(not_assessed=12), None, next_step("assess")
    ),
    "progress every state, review next": lambda: progress_words(
        by_status(accepted=2, rejected=1, waiting_for_review=3, not_assessed=6),
        LAST_WORKED,
        next_step("review"),
    ),
    "progress one of each, assess again": lambda: progress_words(
        by_status(accepted=1, rejected=1, waiting_for_review=1, not_assessed=9),
        LAST_WORKED,
        next_step("assess-again"),
    ),
    "progress one waiting, assess next": lambda: progress_words(
        by_status(waiting_for_review=1, not_assessed=11), LAST_WORKED, next_step("assess")
    ),
    "progress every control accepted": lambda: progress_words(
        by_status(accepted=12), LAST_WORKED, None
    ),
    "assessed partly, waiting": lambda: assessed_words(control("4.1.2"), demo_assessment(), "de"),
    "assessed evidenced, accepted": lambda: assessed_words(
        control("4.1.2"), demo_assessment("accepted", M1="evidenced"), "de"
    ),
    "assessed not evidenced, rejected": lambda: assessed_words(
        control("4.1.2"),
        demo_assessment("rejected", M1="not-evidenced", M2="partly-evidenced", H1="not-evidenced"),
        "de",
    ),
    "not assessed": lambda: not_assessed_words(control("1.1.1"), ALPENWERK),
    **{
        f"sign off {sign_off}, {'assessed' if assessed else 'not assessed'}": (
            signed_off(sign_off, assessed)
        )
        for sign_off in get_args(SignOff)
        for assessed in (True, False)
    },
    "assessed controls, one assessed": lambda: assessed_controls_words(
        controls(), {"4.1.2": demo_assessment()}, "de"
    ),
    "assessed controls, stale and implemented": _stale_and_implemented,
    "unknown control": lambda: unknown_control("9.9.9"),
    "no evidence": lambda: searched("Raumschiff"),
    "no evidence, records unread": lambda: searched("Raumschiff", records=UNREADABLE),
    "no evidence, both unread": lambda: searched(
        "Passwort", policies=UNREADABLE, records=UNREADABLE
    ),
    "no search allowed": lambda: searched("Passwort", policies=None, records=None),
    "assessment finished": finished,
    "found one passage": lambda: searched("Bildschirmsperre"),
    "found passages": lambda: searched("Passwort"),
    "found passages, records unread": lambda: searched("Passwort", records=UNREADABLE),
}


def answer_to(words: str, language: str = "de", name: str = SEARCH_EVIDENCE) -> str:
    call = {"name": name, "args": {}, "id": "c1"}
    messages = [
        HumanMessage("Frage"),
        AIMessage("", tool_calls=[call]),
        ToolMessage(words, tool_call_id="c1", name=name),
    ]
    return CannedChatModel(language=language).invoke(messages).text


def english_words(answer: str) -> list[str]:
    """English function words left in the answer, its cites and quoted spans aside."""
    text = without_quotes(CITE.sub(" ", answer))
    return [w for w in re.findall(r"[^\W\d_]+", text) if w in LANGUAGES["en"].function_words]


@pytest.mark.parametrize("case", TOOL_WORDS)
def test_on_de_the_fakes_answer_to_each_tools_words_is_german(case):
    answer = answer_to(TOOL_WORDS[case]())

    assert wrong_language(answer, "de") is None
    assert english_words(answer) == []


@pytest.mark.parametrize("case", TOOL_WORDS)
def test_on_en_and_fr_the_fakes_answer_to_each_tools_words_is_the_words_cited(case):
    words = TOOL_WORDS[case]()
    assert answer_to(words, "en") == _cited(words)
    assert answer_to(words, "fr") == _cited(words)


def test_the_progress_greeting_reads_in_german():
    answer = answer_to(progress_words(by_status(not_assessed=12), None, next_step("assess")))
    assert written_in(answer) == "de"
    assert "12" in answer and "1.1.1" in answer


def test_a_last_worked_date_has_its_month_in_german():
    words = progress_words(
        by_status(waiting_for_review=1, not_assessed=11), LAST_WORKED, next_step("assess")
    )
    assert "3. März 2026" in answer_to(words)


def test_the_why_line_is_the_working_papers_german_roll_up_words():
    words = assessed_words(control("4.1.2"), demo_assessment(), "de")
    assert "Why:" in words  # the demo's partly evidenced M1 makes the roll-up explain itself

    answer = answer_to(words)

    # review.ts rollUpWords, on `de`, for the same requirements.
    assert (
        "Begründung: Teilweise nachgewiesen: 2 von 3 Muss-, Schutzbedarfs- oder "
        "Overlay-Anforderungen sind vollständig nachgewiesen." in answer
    )


WORDS_CASES = json.loads((Path(__file__).parent / "roll_up_words_cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", [c for c in WORDS_CASES if c["words"]], ids=lambda c: c["name"])
def test_every_roll_up_explanation_reads_in_german(case):
    answer = german_answer(f"Why: {case['words']}")

    assert answer.startswith("Begründung: ")
    assert english_words(answer) == []


def test_on_de_cite_lines_are_the_ones_en_gives():
    words = searched("Passwort")
    cites = [line for line in answer_to(words, "en").splitlines() if "<cite" in line]

    german = answer_to(words).splitlines()

    assert cites and [line for line in german if "<cite" in line] == cites


def test_on_de_an_explain_answer_keeps_its_ids_findings_and_quotes():
    words = assessed_words(control("4.1.2"), demo_assessment(), "de")
    english = answer_to(words, "en", EXPLAIN_CONTROL)
    data = [line for line in english.splitlines() if line.startswith("- ")]

    german = answer_to(words, "de", EXPLAIN_CONTROL)

    assert data and all(line in german.splitlines() for line in data)
    for found_id in re.findall(r"\b(?:\d+\.\d+\.\d+|[MSH]\d)\b", english):
        assert found_id in german


def test_a_sentence_the_table_doesnt_know_passes_through_unchanged():
    assert answer_to(RUNNING_ALREADY, name=START_ASSESSMENT) == RUNNING_ALREADY
    assert german_answer(RUNNING_ALREADY) == RUNNING_ALREADY


# Through the API on Alpenwerk (#330, story 9): the demo's chat shows no language guard step.

LANGUAGE_GUARD = ("Asked for the answer again: it was written in", "The answer is still written in")
TURNS = [
    None,  # the greeting
    "Assess 4.1.2",
    "Explain 4.1.2",
    "Accept 4.1.2",
    "Where do I stand?",
    "Is there evidence of Passwort?",
    "Is there evidence of Raumschiff?",
    "Which controls have documented evidence only?",
    "Assess 9.9.9",
]


@pytest.fixture(scope="module")
def alpenwerk_thread() -> dict:
    """Each turn's events, the turns said one after another in one Alpenwerk thread."""
    lead = {"X-Test-User": f"user_330_{uuid.uuid4().hex[:8]}"}
    turns = {}
    with TestClient(app_on(None), headers={**lead, PROFILE_HEADER: "alpenwerk"}) as client:
        thread_id = None
        for message in TURNS:
            body = {"page": "dossier"} | ({"message": message} if message else {})
            body |= {"thread_id": thread_id} if thread_id else {}
            events = read_events(client.post("/api/chat", json=body))
            thread_id = next(d["thread_id"] for n, d in events if n == "thread")
            turns[message] = events
    return turns


def test_through_the_api_on_alpenwerk_the_greeting_is_german_with_no_guard_step(users):
    lead = users[0]
    with TestClient(app_on(None), headers={**lead, PROFILE_HEADER: "alpenwerk"}) as client:
        events = read_events(client.post("/api/chat", json={"page": "dossier"}))

    answer = "".join(d["text"] for n, d in events if n == "token")
    assert written_in(answer) == "de"
    assert [d for n, d in events if n == "step" and d["text"].startswith(LANGUAGE_GUARD)] == []
    assert [n for n, _ in events][-1] == "done"


@pytest.mark.parametrize("message", TURNS, ids=lambda m: m or "greeting")
def test_through_the_api_on_alpenwerk_each_demo_turn_shows_no_language_guard_step(
    alpenwerk_thread, message
):
    events = alpenwerk_thread[message]

    answer = "".join(d["text"] for n, d in events if n == "token")
    steps = [d["text"] for n, d in events if n == "step"]
    assert [s for s in steps if s.startswith(LANGUAGE_GUARD)] == []
    assert answer and wrong_language(answer, "de") is None
    assert [n for n, _ in events][-1] == "done"


def test_a_note_of_sources_not_read_names_them_in_german():
    answer = answer_to(searched("Passwort", policies=UNREADABLE, records=UNREADABLE))
    assert "Die Richtlinien und die Aufzeichnungen konnten nicht gelesen werden." in answer


# The rules leave document text alone (#330 review): cites and finding lines stay word for word.


def test_an_english_document_line_in_a_cite_crashes_nothing_and_stays_as_it_is():
    cited = '- <cite doc="annual-report">Of the 40 controls, most are documented.</cite>'
    assert german_answer(cited) == cited


def test_a_tool_sentence_quoted_in_a_cite_stays_as_the_document_has_it():
    cited = '- <cite doc="a">The next step is to assess control 4.1.2.</cite>'
    assert german_answer(cited) == cited


def test_an_unknown_english_sentence_inside_a_cite_passes_through():
    cited = '<cite doc="a">Laptops are encrypted. The policies could not be read.</cite>'
    assert german_answer(f"Ich habe gefunden:\n- {cited}") == f"Ich habe gefunden:\n- {cited}"


def test_a_findings_reason_and_requirement_text_stay_word_for_word():
    finding = (
        "- M1 (must): Every control is accepted. Nachgewiesen: The policies could not be read."
    )
    words = f"Not yet evidenced: M1.\n{finding}"

    assert german_answer(words) == f"Noch nicht nachgewiesen: M1.\n{finding}"


def test_a_count_sentence_without_counts_passes_through():
    assert german_answer("Of the 40 controls, most are documented.") == (
        "Of the 40 controls, most are documented."
    )
