"""Alpenwerk's compliance profile and German evidence folder: high protection, the Austrian
overlay, every document's front matter, the planted cases, and the expected-answers draft
(#49)."""

import datetime
import re
from functools import cache
from pathlib import Path
from typing import Literal

import pytest
import yaml
from isa_editions import GERMAN_5_1_1
from pydantic import BaseModel, ConfigDict

from vouch.documents import stale_because
from vouch.evals import EXPECTED_ANSWERS as EXPECTED_ANSWERS_FILE
from vouch.evals import load_cases
from vouch.front_matter import split_front_matter
from vouch.graph import JUDGED_VERDICTS
from vouch.paths import FIXTURES_DIR
from vouch.profile import load_named_profile

PROFILE = "alpenwerk"
ALPENWERK = FIXTURES_DIR / PROFILE
EXPECTED_ANSWERS = ALPENWERK / EXPECTED_ANSWERS_FILE
OVERLAY = "AT NIS2 (illustrative)"
TWELVE = ["1.1.1", "1.6.1", "1.6.2", "2.1.3", "4.1.2", "4.1.3",
          "4.2.1", "5.1.1", "5.1.2", "5.2.4", "5.2.5", "5.2.9"]  # fmt: skip
FOLDERS = {"policies": "documented", "records": "implemented", "patch": "implemented"}
DOCUMENTS = sorted(p for folder in FOLDERS for p in (ALPENWERK / folder).glob("*.md"))
PATCH = "patch-vulnerability-status"  # the recorded Patch response's id (vouch.patch)
GERMAN_WORDS = {"und", "der", "die", "das", "ist", "wird", "werden", "nicht", "mit", "für"}
ENGLISH_WORDS = {"and", "the", "is", "are", "of", "with", "for", "not", "be", "to"}
AS_OF = datetime.date(2026, 11, 4)  # the expected answers' as_of, the day staleness is judged on
DEMO_HORIZON = datetime.date(2027, 6, 30)  # the demo runs on today's date (#22)


class FrontMatter(BaseModel):
    """The fields every evidence document carries (spec #2, Evidence documents), in German."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    title: str
    kind: Literal["documented", "implemented"]
    version: str
    owner: str
    approval_date: datetime.date
    review_cycle: Literal["quarterly", "half-yearly", "yearly", "two-yearly"]
    language: Literal["de"]


def read(path: Path) -> tuple[FrontMatter, str]:
    data, body = split_front_matter(path.read_text(encoding="utf-8"))
    assert data is not None, f"{path.name} has no front matter"
    return FrontMatter.model_validate(data), body


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
def test_every_high_protection_requirement_applies(control_id):
    control = profile().isa_control(control_id)
    every_high = {r.id for r in control.requirements if r.column == "high"}

    assert every_high <= {r.id for r in applicable(control_id)}


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
    meta, body = read(path)

    assert meta.id == path.stem
    assert meta.kind == FOLDERS[path.parent.name]
    assert not set(words(meta.title)) & ENGLISH_WORDS, f"{path.name}'s title isn't German"
    assert german(body), f"{path.name} doesn't read as German"


def test_the_german_check_tells_german_from_english():
    assert german("Die Richtlinie wird jährlich geprüft und ist für alle Mitarbeiter verbindlich.")
    assert not german("The policy is reviewed every year and is binding for all employees.")


# Planted cases.


def document(doc_id: str) -> tuple[FrontMatter, str]:
    [path] = [p for p in DOCUMENTS if p.stem == doc_id]
    return read(path)


def section(doc_id: str, heading: str) -> str:
    """The text under this `## ` heading, up to the next heading."""
    _, body = document(doc_id)
    return body.split(f"## {heading}\n", 1)[1].split("\n#", 1)[0]


def mentioning(pattern: str) -> list[str]:
    """The documents whose text matches this regular expression, ignoring case."""
    return [
        p.stem
        for p in DOCUMENTS
        if re.search(pattern, p.read_text(encoding="utf-8"), re.IGNORECASE)
    ]


def table_rows(body: str) -> list[dict[str, str]]:
    """The first Markdown table in the body, one {header: cell} per data row."""
    lines: list[str] = []
    for line in map(str.strip, body.splitlines()):
        if line.startswith("|"):
            lines.append(line)
        elif lines:  # the first table has ended
            break
    header, _, *rows = ([cell.strip() for cell in line.strip("|").split("|")] for line in lines)
    return [dict(zip(header, row, strict=True)) for row in rows]


def stale_on(day: datetime.date) -> list[str]:
    """The documents stale on this day, by vouch's rule (vouch.documents)."""
    return [p.stem for p in DOCUMENTS if stale_because(read(p)[0].model_dump(), day)]


def test_no_document_mentions_a_csirt_or_an_early_warning():
    assert mentioning(r"CSIRT|Frühwarnung|early warning") == []


def test_the_register_holds_a_significant_incident_that_went_to_no_authority():
    incident = section("sicherheitsereignis-register", "Vorfall 2026-009")

    assert "erheblichen Sicherheitsvorfall" in incident
    assert "Meldung an die Datenschutzbehörde war daher nicht nötig" in incident


def test_the_reporting_duties_name_data_protection_and_customers_but_not_nis2():
    duties = section("vorfallbehandlungsverfahren", "Meldepflichten")

    assert "Datenschutzbehörde" in duties
    assert "Kundenverträgen" in duties
    assert "NIS" not in duties


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


def test_the_logging_policy_is_stale():
    meta, _ = document("protokollierungsrichtlinie")

    assert meta.approval_date == datetime.date(2023, 5, 10)
    assert meta.review_cycle == "yearly"


@pytest.mark.parametrize("day", [AS_OF, DEMO_HORIZON], ids=["as-of", "horizon"])
def test_only_the_logging_policy_is_stale(day):
    assert stale_on(day) == ["protokollierungsrichtlinie"]


# The expected-answers draft.


@cache
def draft() -> dict:
    return yaml.safe_load(EXPECTED_ANSWERS.read_text(encoding="utf-8"))


def expected_answers() -> dict[str, dict[str, dict]]:
    """The draft as {control id: {requirement id: entry}}."""
    return {c["id"]: {r["id"]: r for r in c["requirements"]} for c in draft()["controls"]}


def test_the_draft_covers_exactly_alpenwerks_applicable_requirements():
    expected = {c: {r.id for r in applicable(c)} for c in TWELVE}

    assert {c: set(reqs) for c, reqs in expected_answers().items()} == expected


def test_the_eval_reads_the_draft_as_alpenwerks_cases_judged_on_its_as_of_day():
    cases = load_cases(EXPECTED_ANSWERS, PROFILE)

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


def test_the_high_protection_restore_test_is_partly_evidenced():
    h2 = expected_answers()["5.2.9"]["H2"]
    [column] = {r.column for r in applicable("5.2.9") if r.id == "H2"}

    assert column == "high"
    assert h2["verdict"] == "partly-evidenced"
    assert h2["cites"] == ["datensicherungskonzept", "wiederherstellungstests"]


def test_no_answer_resting_on_the_stale_logging_policy_is_evidenced():
    resting = [
        (control, req_id, entry["verdict"])
        for control, reqs in expected_answers().items()
        for req_id, entry in reqs.items()
        if "protokollierungsrichtlinie" in entry["cites"]
    ]

    assert resting, "5.2.4 rests on the logging policy"
    assert all(verdict == "partly-evidenced" for _, _, verdict in resting), resting
