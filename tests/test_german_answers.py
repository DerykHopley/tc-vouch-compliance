"""Alpenwerk's assessments answer in German, keep quotes in the source language, and the
output check knows German audit-result words (#51)."""

import datetime

import pytest
from assess_steps import step_texts
from langchain_core.messages import ToolMessage

from vouch import ScriptedChatModel, assess, load_named_profile
from vouch.graph import claims_audit_result
from vouch.language import written_in


@pytest.mark.parametrize(
    "text, word",
    [
        ("Alpenwerk ist konform.", "konform"),
        ("Das Verfahren ist ISA-konform umgesetzt.", "konform"),
        ("Alpenwerk ist eine konforme Organisation.", "konforme"),
        ("Alpenwerk ist nach TISAX zertifiziert.", "zertifiziert"),
        ("Ein zertifiziertes Unternehmen.", "zertifiziertes"),
        ("Alpenwerk hat das Audit bestanden.", "bestanden"),
        ("Die Prüfung gilt als BESTANDEN.", "bestanden"),
    ],
)
def test_a_german_claim_of_an_audit_result_is_caught(text, word):
    assert claims_audit_result(text) == word


@pytest.mark.parametrize(
    "text",
    [
        "Alpenwerk ist in diesem Punkt nicht konform.",
        "Der Lieferant ist nicht zertifiziert.",
        "Zwei Konten waren nichtkonform eingerichtet.",
        "Die Linie bestand aus zwei Fertigungs-PCs.",
        "Die Lücken bestanden aus fehlenden Nachweisen.",
        "Die Meldepflichten sind im Notfallordner hinterlegt.",
    ],
)
def test_german_negations_and_bestehen_aus_are_not_audit_claims(text):
    assert claims_audit_result(text) is None


# 1.6.2 for Alpenwerk, at the assess seam.

ALPENWERK = load_named_profile("alpenwerk")
TODAY = datetime.date(2026, 11, 4)  # the expected answers' as_of
OVERLAY = "AT NIS2 (illustrative)"
GERMAN_ANSWER = (
    "Das Verfahren zur Behandlung von Sicherheitsvorfällen legt Zuständigkeiten, "
    "Reaktionszeiten und Meldepflichten fest, und das Register zeigt, dass jede Meldung am "
    "Tag des Eingangs erfasst wurde. Eine Frühwarnung an das nationale CSIRT ist nicht "
    "vorgesehen."
)
INCIDENTS = "vorfallbehandlungsverfahren"
REGISTER = "sicherheitsereignis-register"
# Passages copied unchanged from the German documents.
RECORDED = "Jede Meldung wurde am Tag des Eingangs erfasst"
DUTIES = (
    "Datenschutzverletzungen an die österreichische Datenschutzbehörde innerhalb von "
    "72 Stunden (Datenschutz-Grundverordnung, Artikel 33)"
)
O1_REASON = "Nichts sieht eine Frühwarnung an das nationale CSIRT vor."


def german_1_6_2_script(answers: tuple[str, ...] = (GERMAN_ANSWER,)) -> dict:
    """Searches the German policies and records, answers (once per entry), and judges every
    requirement of 1.6.2, quoting German passages; O1 is not evidenced."""
    base = ("M1", "M2", "M3", "S1", "S2", "S3", "H1", "H2", "H4", "H5")
    cite = [{"doc_id": REGISTER, "quote": RECORDED}]
    return {
        "gather": [
            {"tool": "search_policies", "args": {"query": "Sicherheitsvorfall Meldepflichten"}},
            {"tool": "search_records", "args": {"query": "Sicherheitsereignis Register"}},
            *({"answer": answer} for answer in answers),
        ],
        "judge": {
            "requirements": [
                *(
                    {"requirement_id": r, "verdict": "evidenced", "reason": "Belegt.",
                     "citations": cite}
                    for r in base
                ),
                {
                    "requirement_id": "H3",
                    "verdict": "partly-evidenced",
                    "reason": "Die Meldepflicht nach NIS2 fehlt.",
                    "citations": [{"doc_id": INCIDENTS, "quote": DUTIES}],
                },
                {"requirement_id": "O1", "verdict": "not-evidenced", "reason": O1_REASON},
            ]
        },
    }  # fmt: skip


def text_of(messages) -> str:
    return "\n".join(str(m.content) for m in messages)


def test_1_6_2_for_alpenwerk_answers_in_german_judges_o1_and_quotes_german_passages_unchanged():
    model = ScriptedChatModel(script=german_1_6_2_script())

    result = assess("1.6.2", ALPENWERK, model, today=TODAY)

    assert result.answer == GERMAN_ANSWER
    assert result.answer_language == "de"
    gather_prompt, judge_prompt = text_of(model.prompts[0]), text_of(model.prompts[-2])
    assert "written in German" in gather_prompt
    assert "reason in German" in judge_prompt
    assert "unchanged, in its document's language" in judge_prompt
    assert "in German" in text_of(model.prompts[-1])  # the remediation drafts
    assert step_texts(result, "overlay_branch") == [
        f"Overlay branch: gathering evidence for O1 ({OVERLAY})."
    ]
    o1 = result.requirements[-1]
    assert (o1.id, o1.column, o1.verdict, o1.reason) == (
        "O1",
        "overlay",
        "not-evidenced",
        O1_REASON,
    )
    assert not any("Not judged" in t for t in step_texts(result, "judge"))
    h3 = next(r for r in result.requirements if r.id == "H3")
    assert [(c.doc_id, c.quote) for c in h3.citations] == [(INCIDENTS, DUTIES)]
    quotes = {c.quote for r in result.requirements for c in r.citations}
    assert quotes == {RECORDED, DUTIES}
    assert step_texts(result, "check_answer") == ["Checked the answer: it claims no audit result."]
    assert [i.requirement_id for i in result.remediation] == ["O1"]


# An answer in the wrong language: asked for again once, then kept and noted.

ENGLISH_ANSWER = (
    "The incident procedure sets the responsibilities and the reporting duties, and the "
    "register shows that every report was recorded on the day it came in."
)
ASKED_AGAIN = "The answer was written in English, not German, so it was asked for again in German."


def tool_replies(model: ScriptedChatModel) -> list[str]:
    """The replies to tool calls the model was sent while gathering, once each."""
    sent = (m.content for prompt in model.prompts for m in prompt if isinstance(m, ToolMessage))
    return list(dict.fromkeys(str(c) for c in sent))


def test_an_english_answer_is_asked_for_again_once_in_german_and_the_german_one_is_kept():
    model = ScriptedChatModel(script=german_1_6_2_script((ENGLISH_ANSWER, GERMAN_ANSWER)))

    result = assess("1.6.2", ALPENWERK, model, today=TODAY)

    assert result.answer == GERMAN_ANSWER
    refusal = next(r for r in tool_replies(model) if r.startswith("Answer refused"))
    assert "written in English" in refusal and "in German" in refusal
    assert step_texts(result, "check_answer") == [
        ASKED_AGAIN,
        "Checked the answer: it claims no audit result.",
    ]


def test_an_answer_still_in_english_after_the_retry_is_kept_and_a_step_notes_the_mismatch():
    model = ScriptedChatModel(script=german_1_6_2_script((ENGLISH_ANSWER, ENGLISH_ANSWER)))

    result = assess("1.6.2", ALPENWERK, model, today=TODAY)

    assert result.answer == ENGLISH_ANSWER
    assert step_texts(result, "check_answer") == [
        ASKED_AGAIN,
        (
            "Checked the answer: it claims no audit result. It is still written in English, "
            "not German; it was kept as it is."
        ),
    ]
    assert model.turn == 4  # two searches, two answers: no third ask


def test_an_english_answer_for_nordlicht_is_not_asked_for_again():
    model = ScriptedChatModel(
        script={
            "gather": [
                {"tool": "search_policies", "args": {"query": "authentication"}},
                {"answer": ENGLISH_ANSWER},
            ]
        }
    )

    result = assess("4.1.2", load_named_profile("nordlicht"), model)

    assert result.answer == ENGLISH_ANSWER
    assert step_texts(result, "check_answer") == ["Checked the answer: it claims no audit result."]


@pytest.mark.parametrize(
    "text, language",
    [
        (GERMAN_ANSWER, "de"),
        (ENGLISH_ANSWER, "en"),
        ("Belegt.", None),  # too short to tell
        ("CSIRT, NIS2, ISA 1.6.2", None),
    ],
)
def test_the_language_of_a_text_is_told_by_its_function_words(text, language):
    assert written_in(text) == language
