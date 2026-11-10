"""A stale citation's reason, as the API serves an assessment, follows its answer language (#359):
German on a German profile, in the words of the stale-evidence cap's note (#354); English, as
vouch.documents writes it, on an English one. The working paper's stale flag shows it."""

from vouch.api import assessment_data
from vouch.citations import Citation
from vouch.documents import NO_REVIEW_DATE
from vouch.graph import Assessment, RequirementVerdict, ShouldScore

STALE = "approved 2024-03-01 with a yearly review, due 2025-03-01"


def cited(doc_id: str, stale_because: str | None) -> Citation:
    return Citation(
        doc_id=doc_id,
        version="1.0",
        section="Scope",
        quote="The register lists each key.",
        kind="documented",
        stale=stale_because is not None,
        stale_because=stale_because,
    )


def assessed(language: str, *citations: Citation) -> Assessment:
    return Assessment(
        control_id="4.1.2",
        profile_id="alpenwerk" if language == "de" else "nordlicht",
        verdict="partly-evidenced",
        should_score=ShouldScore(0, 0),
        requirements=[
            RequirementVerdict("M1", "must", "Text.", "partly-evidenced", "Reason.", citations)
        ],
        answer="Answer.",
        answer_language=language,
    )


def served_reasons(assessment: Assessment) -> list[str | None]:
    served = assessment_data(assessment)
    return [c["stale_because"] for c in served["requirements"][0]["citations"]]


def test_on_a_german_profile_a_stale_citations_reason_is_served_in_german():
    assessment = assessed(
        "de", cited("schluesselregister", STALE), cited("richtlinie", NO_REVIEW_DATE)
    )

    assert served_reasons(assessment) == [
        "freigegeben am 2024-03-01, jährlich zu prüfen, fällig am 2025-03-01",
        "kein Prüfdatum",
    ]


def test_the_stale_word_is_the_reports_in_the_answer_language():
    # As the report and the Documents tab flag a stale document (vouch.report.WORDS).
    assert assessment_data(assessed("de"))["stale_word"] == "Prüfung überfällig"
    assert assessment_data(assessed("en"))["stale_word"] == "Stale"


def test_a_current_citation_has_no_reason_in_any_language():
    assert served_reasons(assessed("de", cited("register", None))) == [None]


def test_on_an_english_profile_the_reason_is_served_as_written():
    assessment = assessed("en", cited("key-register", STALE), cited("policy", NO_REVIEW_DATE))

    assert served_reasons(assessment) == [STALE, NO_REVIEW_DATE]


def test_the_stored_assessment_keeps_its_english_reason():
    assessment = assessed("de", cited("schluesselregister", STALE))

    assessment_data(assessment)

    assert assessment.requirements[0].citations[0].stale_because == STALE
