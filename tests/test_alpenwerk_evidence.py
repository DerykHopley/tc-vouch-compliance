"""Alpenwerk's compliance profile and German evidence folder: high protection, the Austrian
overlay, every document's front matter, the planted cases, and the expected-answers draft
(#49)."""

import datetime
import re

import pytest
from evidence_folder import EvidenceFolder, table_rows
from isa_editions import GERMAN_5_1_1

from vouch.evals import EXPECTED_ANSWERS, load_cases
from vouch.graph import JUDGED_VERDICTS
from vouch.patch import RESPONSE_ID as PATCH
from vouch.paths import FIXTURES_DIR
from vouch.profile import load_named_profile

PROFILE = "alpenwerk"
OVERLAY = "AT NIS2 (illustrative)"
TWELVE = ["1.1.1", "1.6.1", "1.6.2", "2.1.3", "4.1.2", "4.1.3",
          "4.2.1", "5.1.1", "5.1.2", "5.2.4", "5.2.5", "5.2.9"]  # fmt: skip
FOLDERS = {"policies": "documented", "records": "implemented", "patch": "implemented"}
ALPENWERK = EvidenceFolder(FIXTURES_DIR / PROFILE, FOLDERS, "de")
DOCUMENTS = ALPENWERK.documents
GERMAN_WORDS = {"und", "der", "die", "das", "ist", "wird", "werden", "nicht", "mit", "für"}
ENGLISH_WORDS = {"and", "the", "is", "are", "of", "with", "for", "not", "be", "to"}
AS_OF = datetime.date(2026, 11, 4)  # the expected answers' as_of, the day staleness is judged on
DEMO_HORIZON = datetime.date(2027, 6, 30)  # the demo runs on today's date (#22)
# Any sign of a national incident report under NIS2: the team, the warning, or the law.
EARLY_WARNING = (
    r"CSIRT|\bCERT\b|CERT\.at|Computer-Notfall|Frühwarn|early warning"
    r"|\bNIS(2|-2|G)?\b|NIS-Richtlinie|Netz- und Informationssystem"
)

read, document, mentioning = ALPENWERK.read, ALPENWERK.document, ALPENWERK.mentioning
expected_answers, stale_on = ALPENWERK.expected_answers, ALPENWERK.stale_on


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def german(text: str) -> bool:
    """Whether the text reads as German: more German function words than English ones."""
    found = words(text)
    return sum(w in GERMAN_WORDS for w in found) > 2 * sum(w in ENGLISH_WORDS for w in found)


def profile():
    return load_named_profile(PROFILE)


def applicable(control_id: str):
    p = profile()
    return p.requirements_for(p.isa_control(control_id)).applicable


# The profile.


def test_the_profile_is_alpenwerk_in_austria_at_high_protection_answering_in_german():
    p = profile()

    assert p.company == "Alpenwerk Präzision GmbH"
    assert p.country == "Austria"
    assert p.protection_level == "high"
    assert p.answer_language == "de"
    assert p.base_framework == "VDA ISA 2027.1"
    assert [o.name for o in p.overlays] == [OVERLAY]


def test_the_profile_reads_the_german_isa_edition():
    assert profile().isa_control("5.1.1").question == GERMAN_5_1_1


@pytest.mark.parametrize("control_id", TWELVE)
def test_every_high_protection_requirement_applies_and_no_very_high_one(control_id):
    control = profile().isa_control(control_id)
    by_column = {r.id: r.column for r in control.requirements}
    applying = {r.id for r in applicable(control_id)}

    assert {r for r, column in by_column.items() if column == "high"} <= applying
    assert not {r for r, column in by_column.items() if column == "very high"} & applying


def test_high_and_very_high_requirements_exist_among_the_twelve():
    """So the test above can't pass on controls with no high or very high requirements."""
    columns = [r.column for c in TWELVE for r in profile().isa_control(c).requirements]
    applying = [r.column for c in TWELVE for r in applicable(c)]

    assert "high" in applying
    assert "very high" in columns


def test_1_6_2_gains_the_overlays_early_warning_as_o1():
    *base, o1 = applicable("1.6.2")

    assert " ".join(r.id for r in base) == "M1 M2 M3 S1 S2 S3 H1 H2 H3 H4 H5"
    assert (o1.id, o1.column, o1.source) == ("O1", "overlay", OVERLAY)
    assert "CSIRT" in o1.text
    assert "24 Stunden" in o1.text


def test_no_other_control_gains_an_overlay_requirement():
    gaining = [c for c in TWELVE if any(r.column == "overlay" for r in applicable(c))]

    assert gaining == ["1.6.2"]


# Front matter.


def test_the_folder_holds_policies_records_and_the_patch_response():
    assert {p.parent.name for p in DOCUMENTS} == set(FOLDERS)
    assert [p.stem for p in DOCUMENTS if p.parent.name == "patch"] == [PATCH]


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_document_has_valid_front_matter_and_is_written_in_german(path):
    meta, body = read(path)  # checks the front matter says German

    assert meta.id == path.stem
    assert meta.kind == FOLDERS[path.parent.name]
    assert not set(words(meta.title)) & ENGLISH_WORDS, f"{path.name}'s title isn't German"
    assert german(body), f"{path.name} doesn't read as German"


def test_the_german_check_tells_german_from_english():
    assert german("Die Richtlinie wird jährlich geprüft und ist für alle Mitarbeiter verbindlich.")
    assert not german("The policy is reviewed every year and is binding for all employees.")


# Planted cases.


def section(doc_id: str, heading: str) -> str:
    """The text under this `## ` heading, up to the next heading."""
    _, body = document(doc_id)
    return body.split(f"## {heading}\n", 1)[1].split("\n#", 1)[0]


def test_no_document_mentions_a_national_incident_report_or_nis2():
    assert mentioning(EARLY_WARNING) == []


@pytest.mark.parametrize(
    "text",
    ["Meldung an CERT.at", "eine Frühwarnmeldung", "nach NIS2", "das NISG", "Computer-Notfallteam"],
)
def test_the_early_warning_pattern_catches_each_way_of_naming_it(text):
    assert re.search(EARLY_WARNING, text, re.IGNORECASE)


def test_the_early_warning_pattern_leaves_nist_and_certificates_alone():
    assert not re.search(EARLY_WARNING, "NIST, Zertifikat, certificate", re.IGNORECASE)


def test_the_register_holds_a_significant_incident_that_went_to_no_authority():
    incident = section("sicherheitsereignis-register", "Vorfall 2026-009")

    assert "erheblichen Sicherheitsvorfall" in incident
    assert "Meldung an die Datenschutzbehörde war daher nicht nötig" in incident


def test_the_reporting_duties_name_data_protection_and_customers():
    duties = section("vorfallbehandlungsverfahren", "Meldepflichten")

    assert "Datenschutzbehörde" in duties
    assert "Kundenverträgen" in duties


def test_the_cryptography_concept_demands_key_sovereignty_for_confidential_cloud_data():
    sovereignty = section("kryptografiekonzept", "Schlüsselhoheit bei Cloud-Diensten")

    assert "Vertrauliche Daten" in sovereignty
    assert "nur Alpenwerk besitzt" in sovereignty


def test_one_confidential_cloud_service_leaves_its_keys_with_the_provider():
    services = table_rows(document("cloud-dienste-register")[1])
    confidential = [s for s in services if s["Höchste Klassifizierung"] == "Vertraulich"]

    assert len(services) == 3
    assert [s["Dienst"] for s in confidential if s["Schlüsselverwaltung"] == "Anbieter"] == [
        "CAD-Datenaustausch"
    ]
    assert any(s["Schlüsselverwaltung"].startswith("Alpenwerk") for s in confidential)


def test_restore_tests_skipped_two_of_the_five_services_in_the_backup_concept():
    services = {s["Dienst"] for s in table_rows(document("datensicherungskonzept")[1])}
    tested = {t["Dienst"] for t in table_rows(document("wiederherstellungstests")[1])}

    assert len(services) == 5
    assert services - tested == {"CAD-Tresor", "Dateiserver"}
    assert tested < services


def test_the_backup_concepts_table_runs_in_restore_order():
    """Each service comes after those it is restored after, and by recovery time, so the
    "in the table's order" rule agrees with the dependencies column."""
    rows = table_rows(document("datensicherungskonzept")[1])
    order = [r["Dienst"] for r in rows]
    hours = [int(r["RTO"].split()[0]) for r in rows]

    for position, row in enumerate(rows):
        after = [d.strip() for d in row["Wiederherstellung nach"].split(",") if "–" not in d]
        assert set(after) <= set(order[:position]), row["Dienst"]
    assert hours == sorted(hours)


def test_the_logging_policy_is_stale():
    meta, _ = document("protokollierungsrichtlinie")

    assert meta.approval_date == datetime.date(2023, 5, 10)
    assert meta.review_cycle == "yearly"


@pytest.mark.parametrize("day", [AS_OF, DEMO_HORIZON], ids=["as-of", "horizon"])
def test_only_the_logging_policy_is_stale(day):
    assert stale_on(day) == ["protokollierungsrichtlinie"]


def test_each_document_is_reviewed_at_least_as_often_as_the_policy_says():
    review = section("informationssicherheitsleitlinie", "Überprüfung")
    cycles = {read(p)[0].review_cycle for p in DOCUMENTS if p.parent.name == "policies"}

    assert "mindestens alle zwei Jahre" in review
    assert cycles <= {"yearly", "two-yearly"}


def test_the_supplier_portals_the_register_added_are_in_the_access_review():
    lessons = section("sicherheitsereignis-register", "Nachbesprechungen")
    reviewed = {r["System"] for r in table_rows(document("zugriffsrechte-ueberpruefung")[1])}

    assert "Lieferantenportale" in lessons
    assert "Lieferantenportale" in reviewed


def test_customer_portals_stay_outside_the_access_review():
    left_out = section("zugriffsrechte-ueberpruefung", "Nicht geprüft")

    assert "Portalen von drei Kunden" in left_out


def test_the_phishing_exercise_reached_the_groups_the_register_names():
    lessons = section("sicherheitsereignis-register", "Nachbesprechungen")
    [exercise] = [
        r for r in table_rows(document("schulungsnachweis")[1]) if "Phishing" in r["Schulung"]
    ]

    assert exercise["Zielgruppe"] in lessons
    assert "alle Mitarbeiter" not in lessons


# The expected-answers draft.


def test_the_draft_lists_no_control_or_requirement_twice():
    assert ALPENWERK.listed_twice() == []


def test_the_draft_covers_exactly_alpenwerks_applicable_requirements():
    expected = {c: {r.id for r in applicable(c)} for c in TWELVE}

    assert {c: set(reqs) for c, reqs in expected_answers().items()} == expected


def test_the_eval_reads_the_draft_as_alpenwerks_cases_judged_on_its_as_of_day():
    cases = load_cases(ALPENWERK.root / EXPECTED_ANSWERS, PROFILE)

    assert cases.today == AS_OF
    assert [c.control_id for c in cases.cases] == TWELVE


def test_each_answer_has_a_verdict_and_cites_only_real_documents():
    known = {p.stem for p in DOCUMENTS}
    for control, reqs in expected_answers().items():
        for req_id, entry in reqs.items():
            where = f"{control} {req_id}"
            assert entry["verdict"] in JUDGED_VERDICTS, where
            assert set(entry["cites"]) <= known, where
            assert bool(entry["cites"]) == (entry["verdict"] != "not-evidenced"), where
            assert entry["why"].strip(), where


@pytest.mark.parametrize(
    "control, req_id",
    [("4.1.3", "M2"), ("4.1.3", "S2"), ("4.1.3", "S7"), ("4.1.3", "S8"), ("5.2.9", "H1")],
)
def test_requirements_about_doing_something_are_evidenced_only_with_implemented_evidence(
    control, req_id
):
    """The draft's rule, on the cases the #49 review named: doing something needs implemented
    evidence to be evidenced; a policy alone gives partly-evidenced at best."""
    entry = expected_answers()[control][req_id]
    kinds = {read(p)[0].kind for p in DOCUMENTS if p.stem in entry["cites"]}

    assert entry["verdict"] != "evidenced" or "implemented" in kinds


def test_the_overlays_early_warning_is_not_evidenced():
    o1 = expected_answers()["1.6.2"]["O1"]

    assert o1["verdict"] == "not-evidenced"
    assert o1["cites"] == []


def test_the_known_reporting_duties_leave_out_nis2_so_1_6_2_h3_is_partly_evidenced():
    h3 = expected_answers()["1.6.2"]["H3"]

    assert h3["verdict"] == "partly-evidenced"
    assert h3["cites"] == ["vorfallbehandlungsverfahren"]


def test_key_sovereignty_is_partly_evidenced_by_the_concept_and_the_cloud_register():
    h1 = expected_answers()["5.1.1"]["H1"]

    assert h1["verdict"] == "partly-evidenced"
    assert h1["cites"] == ["kryptografiekonzept", "cloud-dienste-register"]


def test_the_high_protection_restore_test_is_the_one_gap_in_5_2_9():
    answers = expected_answers()["5.2.9"]
    [column] = {r.column for r in applicable("5.2.9") if r.id == "H2"}

    assert column == "high"
    assert answers["H2"]["verdict"] == "partly-evidenced"
    assert answers["H2"]["cites"] == ["datensicherungskonzept", "wiederherstellungstests"]
    assert [r for r, e in answers.items() if e["verdict"] != "evidenced"] == ["H2"]


def test_no_answer_resting_on_the_stale_logging_policy_is_evidenced():
    resting = [
        (control, req_id, entry["verdict"])
        for control, reqs in expected_answers().items()
        for req_id, entry in reqs.items()
        if "protokollierungsrichtlinie" in entry["cites"]
    ]

    assert resting, "5.2.4 rests on the logging policy"
    assert all(verdict == "partly-evidenced" for _, _, verdict in resting), resting


def test_5_2_5_s1_is_partly_evidenced_as_no_document_defines_patch_management():
    s1 = expected_answers()["5.2.5"]["S1"]

    assert s1["verdict"] == "partly-evidenced"
    assert mentioning(r"Patch-Management|Patchmanagement") == []
