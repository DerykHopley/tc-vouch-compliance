"""Alpenwerk's assessments answer in German, keep quotes in the source language, and the
output check knows German audit-result words (#51)."""

import datetime
import shutil
from pathlib import Path

import pytest
from assess_steps import step_texts
from langchain_core.messages import ToolMessage

from vouch import ScriptedChatModel, assess, language, load_named_profile, load_profile
from vouch.graph import ReviewDecision, Step, claims_audit_result, memory_checkpointer, resume
from vouch.language import written_in


@pytest.mark.parametrize(
    "text, word",
    [
        ("Alpenwerk ist konform.", "konform"),
        ("Das Verfahren ist ISA-konform umgesetzt.", "isa-konform"),
        ("Alpenwerk ist eine konforme Organisation.", "konforme"),
        ("Die Ablage ist normkonform.", "normkonform"),
        ("Das Verfahren ist regelkonform.", "regelkonform"),
        ("Die Löschung erfolgt gesetzeskonform.", "gesetzeskonform"),
        ("Die Verarbeitung ist DSGVO-konform.", "dsgvo-konform"),
        ("Alpenwerk ist nach TISAX zertifiziert.", "zertifiziert"),
        ("Ein zertifiziertes Unternehmen.", "zertifiziertes"),
        ("Alpenwerk hat das Audit bestanden.", "bestanden"),
        ("Die Prüfung gilt als BESTANDEN.", "bestanden"),
        ("Die Zertifizierung ist bestanden.", "bestanden"),
        ("Ein bestandenes TISAX-Assessment liegt vor.", "bestandenes"),
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
        # bestehen in its everyday senses: no audit, test or certification near it.
        "Die Mängel bestanden fort.",
        "Bis März hat kein Notfallplan bestanden.",
        "Die Prüfung ergab, dass zwei Mängel bestanden.",
        "Das Audit wurde nicht bestanden.",
        # Negations, with a word between or more spaces.
        "Die Ablage ist nicht vollständig konform.",
        "Der Dienst ist nicht mehr zertifiziert.",
        "Die Löschung ist nicht ganz gesetzeskonform.",
        "Alpenwerk hat kein zertifiziertes ISMS.",
        "Es gibt keine konforme Lösung.",
        "Der Dienst ist nicht DSGVO-konform.",
        "Der Lieferant ist nicht   zertifiziert.",
    ],
)
def test_german_negations_and_everyday_bestehen_are_not_audit_claims(text):
    assert claims_audit_result(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Nordlicht is not fully compliant with S2.",
        "The supplier is not  certified.",
        "Nordlicht is not yet compliant.",
    ],
)
def test_english_negations_with_a_word_between_or_more_spaces_are_not_audit_claims(text):
    assert claims_audit_result(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Der Vertrag sagt: „Das Rechenzentrum ist nach ISO 27001 zertifiziert.“",
        "Laut Lieferant gilt: «der Dienst ist DSGVO-konform».",
        'The contract says "the data centre is certified to ISO 27001".',
        "The register reads “the supplier passed its audit”.",
    ],
)
def test_an_audit_word_inside_a_quote_from_a_gathered_passage_is_not_a_claim(text):
    """Quotes are copied unchanged from the documents, as the prompts ask (#51 review)."""
    assert claims_audit_result(text, GATHERED) is None


# Passages the assessment gathered, as search results, records and Patch's response give them.
GATHERED = (
    "Laut Vertrag:  Das Rechenzentrum ist nach\nISO 27001 zertifiziert. Es liegt in Wien.",
    "Der Lieferant bestätigt, der Dienst ist DSGVO-konform.",
    "Clause 4: the data centre is certified to ISO 27001.",
    "Finding 3: the supplier passed its audit in May.",
)


@pytest.mark.parametrize(
    "text, word",
    [
        ('Nordlicht is "certified".', "certified"),
        ("Nordlicht is not only compliant but exemplary.", "compliant"),
        ("Nordlicht is not just certified but audited yearly.", "certified"),
        ('Alpenwerk ist "konform".', "konform"),
        ("Alpenwerk ist nicht nur konform, sondern vorbildlich.", "konform"),
        ("Alpenwerk ist nicht bloß zertifiziert.", "zertifiziert"),
        ("Alpenwerk ist „nach TISAX zertifiziert“.", "zertifiziert"),
    ],
)
def test_a_claim_in_quote_marks_or_after_not_only_is_caught(text, word):
    """#140: neither a quote mark nor "not only" hides a claim."""
    assert claims_audit_result(text, GATHERED) == word


def test_a_quoted_claim_is_caught_when_no_passages_were_gathered():
    text = "Der Vertrag sagt: „Das Rechenzentrum ist nach ISO 27001 zertifiziert.“"

    assert claims_audit_result(text) == "zertifiziert"


def test_a_short_quote_is_caught_even_when_a_gathered_passage_has_its_word():
    """A quote is evidence only with at least four words, as the quote check asks: one
    word ("konform") would otherwise pass wherever any passage uses it."""
    assert claims_audit_result('Alpenwerk ist "DSGVO-konform".', GATHERED) == "dsgvo-konform"


def test_an_audit_word_outside_a_quote_is_still_caught():
    assert claims_audit_result("„Der Plan gilt.“ Alpenwerk ist zertifiziert.") == "zertifiziert"


# 1.6.2 for Alpenwerk, at the assess seam.

ALPENWERK = load_named_profile("alpenwerk")
ALPENWERK_DIR = Path(__file__).parent.parent / "fixtures" / "alpenwerk"
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


# A quote from a gathered passage may hold an audit word (#140).

PROVIDER = "Das Rechenzentrum des Dienstleisters ist nach ISO 27001 zertifiziert."


def alpenwerk_with_a_certified_provider(tmp_path: Path):
    """Alpenwerk's profile and evidence copied to tmp_path, its incident register naming a
    provider certified to ISO 27001."""
    folder = tmp_path / "alpenwerk"
    shutil.copytree(ALPENWERK_DIR, folder)
    register = folder / "records" / f"{REGISTER}.md"
    with register.open("a", encoding="utf-8") as text:
        text.write(f"\n## Dienstleister\n\nLaut Vertrag: {PROVIDER} Es liegt in Wien.\n")
    return load_profile(folder / "profile.yaml")


def test_an_answer_quoting_a_gathered_alpenwerk_passage_with_zertifiziert_is_not_refused(
    tmp_path,
):
    answer = f"{GERMAN_ANSWER} Der Vertrag hält fest: „{PROVIDER}“"
    script = german_1_6_2_script((answer,))
    search = {"tool": "search_records", "args": {"query": "Dienstleister Rechenzentrum"}}
    script["gather"].insert(0, search)
    model = ScriptedChatModel(script=script)

    result = assess("1.6.2", alpenwerk_with_a_certified_provider(tmp_path), model, today=TODAY)

    assert result.answer == answer
    assert step_texts(result, "check_answer") == ["Checked the answer: it claims no audit result."]


def test_an_answer_quoting_zertifiziert_from_no_gathered_passage_is_refused(tmp_path):
    invented = "Alpenwerk ist nach ISO 27001 und TISAX zertifiziert."
    answer = f"{GERMAN_ANSWER} Der Vertrag hält fest: „{invented}“"
    model = ScriptedChatModel(script=german_1_6_2_script((answer, GERMAN_ANSWER)))

    result = assess("1.6.2", alpenwerk_with_a_certified_provider(tmp_path), model, today=TODAY)

    assert result.answer == GERMAN_ANSWER
    refusal = next(r for r in tool_replies(model) if r.startswith("Answer refused"))
    assert "zertifiziert" in refusal


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


@pytest.mark.parametrize(
    "text, language",
    [
        (
            (
                "Die Richtlinie ist kurz. Der Lieferant schreibt: „The policy is reviewed and the "
                "keys are stored with the provider, not with the customer, for all of the data.“"
            ),
            "de",
        ),
        (
            (
                'The policy is short. It quotes the contract: "Die Schlüssel werden beim Anbieter '
                'und nicht beim Kunden gespeichert, das ist für die Daten der Fall."'
            ),
            "en",
        ),
    ],
)
def test_the_language_of_a_text_leaves_out_what_it_quotes(text, language):
    assert written_in(text) == language


def test_ids_such_as_is_01_are_not_read_as_english_words():
    text = "IS-01, IS-02 und IS-03: Die Richtlinie wird geprüft und ist für alle gültig."

    assert written_in(text) == "de"


def test_the_language_is_told_with_only_one_language_known(monkeypatch):
    monkeypatch.setattr(language, "LANGUAGES", {"de": language.LANGUAGES["de"]})

    assert written_in(GERMAN_ANSWER) == "de"
    assert written_in(ENGLISH_ANSWER) is None


def test_a_wrong_language_step_without_a_profile_still_reads_as_a_sentence():
    text = Step("check_answer", {"wrong_language": "en"}).describe()

    assert text == (
        "The answer was written in English, not the profile's language, so it was asked for "
        "again in the profile's language."
    )


def test_a_thread_saved_before_51_reads_its_profiles_answer_language_on_resume():
    """Its state has no answer_language: the reviewed Assessment takes the profile's."""
    saver = memory_checkpointer()
    result = assess("1.6.2", ALPENWERK, ScriptedChatModel(script=german_1_6_2_script()), saver)
    # What a thread saved before #51 lacks: the answer_language channel.
    for key in [k for k in saver.blobs if k[2] == "answer_language"]:
        del saver.blobs[key]

    reviewed = resume(result.thread_id, ReviewDecision(decision="accept"), saver)

    assert reviewed.answer_language == "de"
