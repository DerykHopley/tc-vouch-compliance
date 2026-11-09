"""The sample library (#370): invented English evidence any company made in setup can use, its
hidden reference profile, the three planted gaps, and the expected-answers draft."""

import datetime
import re

import pytest
from evidence_folder import EvidenceFolder

from vouch.documents import paragraphs
from vouch.evals import EXPECTED_ANSWERS, load_cases, spec_roll_up
from vouch.front_matter import split_front_matter
from vouch.graph import JUDGED_VERDICTS
from vouch.paths import FIXTURES_DIR, SAMPLE_LIBRARY_DIR
from vouch.profile import SAMPLE_LIBRARY, load_named_profile
from vouch.search import search_folder

TWELVE = ["1.1.1", "1.6.1", "1.6.2", "2.1.3", "4.1.2", "4.1.3",
          "4.2.1", "5.1.1", "5.1.2", "5.2.4", "5.2.5", "5.2.9"]  # fmt: skip
FOLDERS = {"policies": "documented", "records": "implemented"}
LIBRARY = EvidenceFolder(SAMPLE_LIBRARY_DIR, FOLDERS, "en")
DOCUMENTS = LIBRARY.documents
AS_OF = datetime.date(2026, 11, 4)  # the expected answers' as_of
DEMO_HORIZON = datetime.date(2027, 6, 30)  # the demo runs on today's date (#22)
STALE = "backup-policy"
COMPANY = r"Nordlicht|Alpenwerk|\bGmbH\b|\bAG\b|\bLtd\b|\bInc\b"
ACCESS_REVIEW = r"access review|review(ed|s)? (of )?(the )?(access|rights)|rights review|recertif"
# Any sentence pairing a report with a security event, either way round, or telling of one.
REPORTING = (
    r"\breport\w*\b[^.\n]{0,60}\b(event|incident|observation|suspicion)s?\b"
    r"|\b(event|incident|observation)s?\b[^.\n]{0,30}\breport\w*"
    # Telling someone at once, or being told of an incident (verify round 1 of #370).
    r"|\btell\w*\b[^.\n]{0,20}\b(at once|immediately|straight away)"
    r"|\btell\w*\b[^.\n]{0,20}\bof an? (event|incident|observation)"
)
# The requirements of the twelve that ask for something to be done, not only written down.
ABOUT_DOING = {
    "1.1.1": ["M4", "M5"],
    "1.6.2": ["M1", "M2", "M3", "S1", "H2"],
    "2.1.3": ["M1", "S4", "S5", "S6"],
    "4.1.2": ["S1", "S2", "S3", "H1"],
    "4.1.3": ["M1", "M2", "M4", "M5", "M6", "M7", "S1", "S2", "S3", "S4", "S5", "S8", "S9", "S10"],
    "4.2.1": ["M1", "M2", "S2", "S3", "S4", "S5", "H1"],
    "5.1.1": ["M1", "S1", "H1"],
    "5.1.2": ["M2", "M3", "S1", "S2", "S3", "H1"],
    "5.2.4": ["M1", "M2", "M3", "M4", "M5", "S1", "S3", "H1", "H2"],
    "5.2.5": ["M1", "M2", "M3", "S1", "S2", "S3"],
    "5.2.9": ["H2"],
}
REVIEWING_ACCESS = ["M2"]  # 4.2.1's requirements about reviewing access

read, mentioning, stale_on = LIBRARY.read, LIBRARY.mentioning, LIBRARY.stale_on
expected_answers = LIBRARY.expected_answers


def profile():
    return load_named_profile(SAMPLE_LIBRARY)


def applicable(control_id: str):
    p = profile()
    return p.requirements_for(p.isa_control(control_id)).applicable


def kinds_cited(entry: dict) -> set[str]:
    return {LIBRARY.document(doc_id)[0].kind for doc_id in entry["cites"]}


# The profile.


def test_the_reference_profile_is_germany_english_high_with_nothing_left_out():
    p = profile()

    assert p.id == SAMPLE_LIBRARY
    assert p.country == "Germany"
    assert p.answer_language == "en"
    assert p.protection_level == "high"
    assert p.overlays == ()
    assert p.not_applicable == ()
    assert list(p.evidence_sources) == ["policies", "records"]


def test_the_library_lives_in_the_shipped_fixtures():
    """The two constants agree, so the eval's profile and the library are one folder."""
    assert SAMPLE_LIBRARY_DIR == FIXTURES_DIR / SAMPLE_LIBRARY


# Front matter.


def test_the_library_holds_8_to_12_policies_and_at_least_2_records():
    by_folder = [p.parent.name for p in DOCUMENTS]

    assert 8 <= by_folder.count("policies") <= 12
    assert by_folder.count("records") >= 2
    assert set(by_folder) == set(FOLDERS)  # no patch folder


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_document_has_valid_english_front_matter_of_its_folders_kind(path):
    meta, _ = read(path)  # checks the front matter and that it says English

    assert meta.id == path.stem
    assert meta.kind == FOLDERS[path.parent.name]


# Written for any company, and new.


def test_no_document_names_a_company_or_a_company_form():
    assert mentioning(COMPANY) == []


def library_paragraphs(root) -> set[str]:
    return {
        text
        for path in root.glob("*/*.md")
        for _, text in paragraphs(split_front_matter(path.read_text(encoding="utf-8"))[1])
    }


def test_no_paragraph_is_copied_from_nordlicht_or_alpenwerk():
    others = library_paragraphs(FIXTURES_DIR / "nordlicht") | library_paragraphs(
        FIXTURES_DIR / "alpenwerk"
    )

    assert library_paragraphs(SAMPLE_LIBRARY_DIR) & others == set()


# The planted gaps.


@pytest.mark.parametrize("day", [AS_OF, DEMO_HORIZON], ids=["as-of", "horizon"])
def test_only_the_backup_policy_is_stale(day):
    assert stale_on(day) == [STALE]


def test_the_access_rights_policy_sets_an_access_review_and_no_record_shows_one():
    records = [p.stem for p in DOCUMENTS if p.parent.name == "records"]

    assert "access-rights-policy" in mentioning(ACCESS_REVIEW)  # so the pattern finds one
    assert set(mentioning(ACCESS_REVIEW)) & set(records) == set()


def test_4_2_1_rolls_up_below_evidenced_and_its_review_rests_on_policies_alone():
    answers = expected_answers()["4.2.1"]
    columns = {r.id: r.column for r in applicable("4.2.1")}

    assert spec_roll_up((columns[r], e["verdict"]) for r, e in answers.items()) != "evidenced"
    for req_id in REVIEWING_ACCESS:
        assert answers[req_id]["cites"], req_id
        assert kinds_cited(answers[req_id]) == {"documented"}, req_id


@pytest.mark.parametrize(
    "text",
    [
        "Report any security event to the service desk.",
        "Staff report suspicious incidents by phone.",
        "Incidents are reported through the portal.",
        "Tell IT at once if you lose your token.",
        "When a supplier tells us of an incident, we act.",
    ],
)
def test_the_reporting_pattern_catches_each_way_of_saying_it(text):
    assert re.search(REPORTING, text, re.IGNORECASE)


def test_no_document_says_how_to_report_a_security_event():
    assert mentioning(REPORTING) == []


def test_every_1_6_1_requirement_is_not_evidenced():
    answers = expected_answers()["1.6.1"]

    assert answers
    assert {e["verdict"] for e in answers.values()} == {"not-evidenced"}


def test_no_answer_resting_on_the_stale_backup_policy_is_evidenced():
    resting = [
        (control, req_id, entry["verdict"])
        for control, reqs in expected_answers().items()
        for req_id, entry in reqs.items()
        if STALE in entry["cites"]
    ]

    assert resting, "5.2.9 rests on the backup policy"
    assert all(verdict != "evidenced" for _, _, verdict in resting), resting


# The expected-answers draft.


def test_the_draft_is_a_draft_as_of_the_other_profiles_day():
    draft = LIBRARY.draft()

    assert draft["status"] == "draft"
    assert draft["as_of"] == AS_OF
    assert draft["profile"] == SAMPLE_LIBRARY


def test_the_draft_lists_no_control_or_requirement_twice():
    assert LIBRARY.listed_twice() == []


def test_the_draft_covers_exactly_the_reference_profiles_applicable_requirements():
    expected = {c: {r.id for r in applicable(c)} for c in TWELVE}

    assert {c: set(reqs) for c, reqs in expected_answers().items()} == expected


def test_the_eval_reads_the_draft_as_twelve_cases():
    cases = load_cases(SAMPLE_LIBRARY_DIR / EXPECTED_ANSWERS, SAMPLE_LIBRARY)

    assert cases.today == AS_OF
    assert [c.control_id for c in cases.cases] == TWELVE


def test_each_answer_has_a_verdict_and_cites_only_library_documents():
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
    [(c, r) for c, reqs in ABOUT_DOING.items() for r in reqs],
)
def test_requirements_about_doing_something_are_evidenced_only_with_implemented_evidence(
    control, req_id
):
    entry = expected_answers()[control][req_id]

    assert entry["verdict"] != "evidenced" or "implemented" in kinds_cited(entry)


# Search.


def test_a_search_on_backups_and_restores_finds_the_backup_policy():
    found = search_folder("backup restore", folder=SAMPLE_LIBRARY_DIR / "policies")

    assert isinstance(found, list)
    assert STALE in {p["doc_id"] for p in found}
