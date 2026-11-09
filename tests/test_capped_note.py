"""The stale-evidence cap's note follows the profile's answer language (#354): German on a German
profile, English, word for word as before, on an English one."""

from vouch.citations import Citation
from vouch.documents import NO_REVIEW_DATE, EvidenceKind
from vouch.graph import CAPPED, DOCUMENTED_ONLY, RequirementVerdict, _apply_rules
from vouch.language import mismatch


def cited(doc_id: str, stale_because: str | None, kind: EvidenceKind = "documented") -> Citation:
    return Citation(
        doc_id=doc_id,
        version="1.0",
        section="Scope",
        quote="The register lists each key.",
        kind=kind,
        stale=stale_because is not None,
        stale_because=stale_because,
    )


def requirement(reason: str, *citations: Citation) -> RequirementVerdict:
    return RequirementVerdict("M1", "must", "Text.", "evidenced", reason, citations)


STALE = "approved 2024-03-01 with a yearly review, due 2025-03-01"


def test_on_a_german_profile_the_cap_note_is_german():
    reason = "Das Register der Schlüssel ist vorhanden und wird gepflegt."
    capped = _apply_rules(
        requirement(reason, cited("schluesselregister", STALE)), about_doing=False, language="de"
    )

    assert capped.verdict == "partly-evidenced"
    assert capped.reason == (
        f"{reason} Höchstens teilweise nachgewiesen: schluesselregister ist veraltet "
        "(freigegeben am 2024-03-01, jährlich zu prüfen, fällig am 2025-03-01)."
    )
    assert CAPPED not in capped.reason
    assert mismatch(capped.reason, "de") is None


def test_a_german_note_for_no_review_date_and_documented_only():
    reason = "Die Richtlinie beschreibt das Verfahren für alle Mitarbeiter."
    capped = _apply_rules(
        requirement(reason, cited("richtlinie", NO_REVIEW_DATE)), about_doing=True, language="de"
    )

    assert capped.reason == (
        f"{reason} Höchstens teilweise nachgewiesen: richtlinie ist veraltet (kein Prüfdatum); "
        "es verlangt, dass etwas getan wird, und nur dokumentierte Nachweise zeigen es."
    )


def test_on_an_english_profile_the_note_is_unchanged():
    capped = _apply_rules(requirement("Shown.", cited("key-register", STALE)), about_doing=True)

    assert capped.reason == (f"Shown. {CAPPED} key-register is stale ({STALE}); {DOCUMENTED_ONLY}.")
