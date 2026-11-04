"""Documented and implemented evidence, and stale documents (spec #2, ticket #22)."""

import json
import logging
import shutil
from datetime import date

import pytest
from assess_steps import step_texts

from vouch import ScriptedChatModel, assess, load_named_profile, load_profile
from vouch.citations import Citation, CitationClaim, DropReason, QuoteChecker
from vouch.documents import review_due
from vouch.graph import GUARD
from vouch.profile import NORDLICHT_PROFILE
from vouch.search import search_records

NORDLICHT = load_named_profile("nordlicht")
TRUE_QUOTE = "Passwords have at least 12 characters."

RECORDS_DIR = NORDLICHT_PROFILE.parent / "records"
TODAY = date(2026, 11, 4)  # the expected answers' as_of
BODY = "All laptops use full disk encryption."
ENCRYPTION_STALE = "approved 2022-03-14 with a yearly review, due 2023-03-14"
LAPTOPS = "Every company laptop uses full-disk encryption with AES-256."
METHODS = "We use only encryption methods that the national cyber security authority recommends"
EXPOSURE = "We decide how people sign in to a system by looking at what could go wrong with it."
ROLLOUT = "All 171 employee accounts have an authenticator app registered."


# search_records: the policies' search, over the records folder.


def test_search_records_finds_passages_in_the_records_folder():
    passages = search_records("training attendance", folder=RECORDS_DIR)

    assert passages
    assert {p["doc_id"] for p in passages} <= {p.stem for p in RECORDS_DIR.glob("*.md")}
    assert "training-attendance-record" in {p["doc_id"] for p in passages}


def test_an_empty_records_folder_gives_no_passages_and_no_error(tmp_path):
    assert search_records("training attendance", folder=tmp_path) == []


def test_the_agent_can_search_the_records():
    model = ScriptedChatModel(
        script={
            "gather": [
                {"tool": "search_records", "args": {"query": "training attendance"}},
                {"answer": "Draft answer."},
            ]
        }
    )

    result = assess("2.1.3", NORDLICHT, model)

    [searched] = step_texts(result, "tools")
    assert searched.startswith("Searched the records for “training attendance”: ")
    assert not searched.endswith(": 0 passages.")
    assert step_texts(result, "gather")[0] == "Searching the records for “training attendance”."


# The stale rule: approval date plus review cycle, before today's date.


@pytest.mark.parametrize(
    ("approved", "cycle", "due"),
    [
        (date(2022, 3, 14), "yearly", date(2023, 3, 14)),
        (date(2026, 11, 20), "quarterly", date(2027, 2, 20)),
        (date(2026, 1, 31), "quarterly", date(2026, 4, 30)),
        (date(2026, 8, 31), "half-yearly", date(2027, 2, 28)),
        (date(2024, 2, 29), "yearly", date(2025, 2, 28)),
        (date(2026, 7, 14), "two-yearly", date(2028, 7, 14)),
    ],
    ids=["plain", "across-a-year", "31st-to-30th", "31st-to-february", "leap-day", "two-yearly"],
)
def test_review_due_clamps_to_the_months_last_day(approved, cycle, due):
    assert review_due(approved, cycle) == due


# Citations carry the document's kind and whether it is stale.


def write_doc(folder, name: str, front: str, body: str = BODY) -> None:
    (folder / f"{name}.md").write_text(f"---\n{front}---\n# Top\n\n{body}\n", encoding="utf-8")


def check(folder, doc_id: str, today: date = TODAY, source: str = "policies"):
    checker = QuoteChecker({source: str(folder)}, today=today)
    return checker.check(CitationClaim(doc_id=doc_id, quote=BODY))


FRESH = 'version: "1.0"\napproval_date: 2026-06-01\nreview_cycle: yearly\n'


@pytest.mark.parametrize("kind", ["documented", "implemented"])
def test_a_citation_carries_its_documents_kind_and_a_fresh_one_is_not_stale(tmp_path, kind):
    write_doc(tmp_path, "doc", f"kind: {kind}\n{FRESH}")

    assert check(tmp_path, "doc") == Citation(
        "doc", "1.0", "Top", BODY, kind=kind, stale=False, stale_because=None
    )


def test_a_citation_from_a_document_past_its_review_is_stale(tmp_path):
    write_doc(tmp_path, "doc", f"kind: documented\n{FRESH}")

    assert check(tmp_path, "doc", today=date(2027, 6, 2)).stale is True
    assert check(tmp_path, "doc", today=date(2027, 6, 1)).stale is False  # due today: not yet


@pytest.mark.parametrize(
    "front",
    [
        'kind: documented\nversion: "1.0"\nreview_cycle: yearly\n',
        'kind: documented\nversion: "1.0"\napproval_date: 2026-06-01\n',
        'kind: documented\nversion: "1.0"\napproval_date: soon\nreview_cycle: yearly\n',
        'kind: documented\nversion: "1.0"\napproval_date: 2026-06-01\nreview_cycle: weekly\n',
        'kind: documented\nversion: "1.0"\napproval_date: 2026-06-01\nreview_cycle: [yearly]\n',
        'kind: documented\nversion: "1.0"\napproval_date: 2026-06-01\nreview_cycle: {a: 1}\n',
        'kind: documented\nversion: "1.0"\napproval_date: [2026-06-01]\nreview_cycle: yearly\n',
        'kind: documented\nversion: "1.0"\napproval_date: 20260601\nreview_cycle: yearly\n',
    ],
    ids=[
        "no-approval-date",
        "no-cycle",
        "date-not-a-date",
        "unknown-cycle",
        "cycle-a-list",
        "cycle-a-mapping",
        "date-a-list",
        "date-a-number",
    ],
)
def test_a_document_with_no_review_date_counts_as_stale(tmp_path, front):
    write_doc(tmp_path, "doc", front)

    citation = check(tmp_path, "doc")

    assert (citation.stale, citation.stale_because) == (True, "no review date")


@pytest.mark.parametrize(
    ("source", "kind"),
    [("policies", "documented"), ("records", "implemented"), ("patch", "documented")],
)
@pytest.mark.parametrize("front", ["", "kind: paper\n"], ids=["no-kind", "unknown-kind"])
def test_a_document_with_no_valid_kind_takes_its_sources_kind_and_says_so(
    tmp_path, caplog, source, kind, front
):
    write_doc(tmp_path, "doc", front + FRESH)

    with caplog.at_level(logging.WARNING, logger="vouch.documents"):
        citation = check(tmp_path, "doc", source=source)

    assert citation.kind == kind
    assert "doc" in caplog.text and kind in caplog.text


def test_the_front_matters_kind_wins_over_the_source(tmp_path):
    write_doc(tmp_path, "doc", f"kind: documented\n{FRESH}")

    assert check(tmp_path, "doc", source="records").kind == "documented"


def test_the_encryption_standard_is_stale_and_says_why():
    policies = {"policies": str(NORDLICHT_PROFILE.parent / "policies")}
    checker = QuoteChecker(policies, today=TODAY)

    stale = checker.check(CitationClaim(doc_id="encryption-standard", quote=LAPTOPS))
    fresh = checker.check(CitationClaim(doc_id="authentication-standard", quote=TRUE_QUOTE))

    assert (stale.stale, stale.stale_because) == (True, ENCRYPTION_STALE)
    assert (fresh.stale, fresh.stale_because) == (False, None)


def test_a_doc_id_in_two_sources_is_dropped(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "r").mkdir()
    write_doc(tmp_path / "p", "doc", f"kind: documented\n{FRESH}")
    write_doc(tmp_path / "r", "doc", f"kind: implemented\n{FRESH}")
    checker = QuoteChecker(
        {"policies": str(tmp_path / "p"), "records": str(tmp_path / "r")}, today=TODAY
    )

    assert checker.check(CitationClaim(doc_id="doc", quote=BODY)) == DropReason.IN_TWO_SOURCES
    assert DropReason.IN_TWO_SOURCES == "the id names a document in more than one source"


# The judge's rules, at the assess seam with today's date fixed.


def judged(judge: list[dict], query: str = "encryption") -> ScriptedChatModel:
    return ScriptedChatModel(
        script={
            "gather": [
                {"tool": "search_policies", "args": {"query": query}},
                {"answer": "Draft answer."},
            ],
            "judge": {"requirements": judge},
        }
    )


def judgement(req: str, *citations: tuple[str, str], doing: bool = False) -> dict:
    return {
        "requirement_id": req,
        "verdict": "evidenced",
        "reason": "Shown.",
        "about_doing": doing,
        "citations": [{"doc_id": d, "quote": q} for d, q in citations],
    }


def requirement(result, req_id: str):
    return next(r for r in result.requirements if r.id == req_id)


def encryption_run() -> ScriptedChatModel:
    cites = [("encryption-standard", LAPTOPS), ("encryption-standard", METHODS)]
    return judged([judgement("M1", *cites), judgement("S1", *cites)])


def test_5_1_1_m1_is_capped_at_partly_evidenced_by_the_stale_encryption_standard():
    result = assess("5.1.1", NORDLICHT, encryption_run(), today=TODAY)

    m1 = requirement(result, "M1")
    assert m1.verdict == "partly-evidenced"
    assert m1.reason == (
        f"Shown. At most partly evidenced: encryption-standard is stale ({ENCRYPTION_STALE})."
    )
    assert {(c.kind, c.stale) for c in m1.citations} == {("documented", True)}
    assert result.verdict == "not-evidenced"  # no counted requirement is evidenced


def test_the_quote_check_step_says_which_verdicts_the_checks_changed():
    result = assess("5.1.1", NORDLICHT, encryption_run(), today=TODAY)

    assert step_texts(result, "judge")[-1].startswith("Judged 2 of 2 requirements evidenced.")
    assert step_texts(result, "check_quotes")[-1].endswith(
        "Changed by the checks: M1 to partly evidenced, S1 to partly evidenced. "
        "Now 0 of 2 requirements evidenced."
    )


def test_the_quote_check_step_adds_nothing_when_no_verdict_changed():
    result = assess("5.1.1", NORDLICHT, encryption_run(), today=date(2023, 3, 14))

    assert "Changed by the checks" not in step_texts(result, "check_quotes")[-1]


def test_the_same_run_before_the_review_was_due_is_evidenced():
    result = assess("5.1.1", NORDLICHT, encryption_run(), today=date(2023, 3, 14))

    m1 = requirement(result, "M1")
    assert (m1.verdict, m1.reason) == ("evidenced", "Shown.")
    assert {c.stale for c in m1.citations} == {False}


def test_a_partly_evidenced_requirement_on_stale_evidence_still_says_why():
    judge = judgement("M1", ("encryption-standard", LAPTOPS)) | {"verdict": "partly-evidenced"}
    result = assess("5.1.1", NORDLICHT, judged([judge]), today=TODAY)

    m1 = requirement(result, "M1")
    assert m1.verdict == "partly-evidenced"
    assert m1.reason.endswith(f"encryption-standard is stale ({ENCRYPTION_STALE}).")


def test_4_1_2_risk_assessment_with_documented_evidence_only_is_partly_evidenced():
    model = judged([judgement("M1", ("authentication-standard", EXPOSURE), doing=True)], "sign in")

    m1 = requirement(assess("4.1.2", NORDLICHT, model, today=TODAY), "M1")

    assert m1.verdict == "partly-evidenced"
    assert m1.reason == (
        "Shown. At most partly evidenced: it asks for something to be done, and only "
        "documented evidence shows it."
    )
    assert [(c.doc_id, c.kind, c.stale) for c in m1.citations] == [
        ("authentication-standard", "documented", False)
    ]


def test_a_doing_requirement_with_implemented_evidence_can_be_evidenced():
    cites = [("authentication-standard", EXPOSURE), ("mfa-rollout-record", ROLLOUT)]
    model = judged([judgement("M1", *cites, doing=True)], "sign in")

    m1 = requirement(assess("4.1.2", NORDLICHT, model, today=TODAY), "M1")

    assert (m1.verdict, m1.reason) == ("evidenced", "Shown.")
    assert [c.kind for c in m1.citations] == ["documented", "implemented"]


def test_a_requirement_not_about_doing_can_be_evidenced_by_documented_evidence():
    model = judged([judgement("M1", ("authentication-standard", EXPOSURE))], "sign in")

    m1 = requirement(assess("4.1.2", NORDLICHT, model, today=TODAY), "M1")

    assert m1.verdict == "evidenced"


# Two sources, one unreadable; the step and reason name what happened.


def nordlicht_copy(tmp_path, *sources: str):
    """Nordlicht's profile and evidence copied to tmp_path, allowing only these sources."""
    folder = tmp_path / "nordlicht"
    shutil.copytree(NORDLICHT_PROFILE.parent, folder)
    path = folder / "profile.yaml"
    text = path.read_text(encoding="utf-8")
    start = text.index("evidence_sources:")
    end = text.index("# Room for risk")
    allowed = "".join(f"  {s}:\n    folder: {s}\n" for s in sources) or "  {}\n"
    path.write_text(text[:start] + "evidence_sources:\n" + allowed + text[end:], encoding="utf-8")
    return load_profile(path)


def two_searches(*, records_query: str = "password") -> ScriptedChatModel:
    judge = [
        {
            "requirement_id": "M2",
            "verdict": "evidenced",
            "reason": "Shown.",
            "citations": [{"doc_id": "authentication-standard", "quote": TRUE_QUOTE}],
        }
    ]
    return judged_with(
        [
            {"tool": "search_policies", "args": {"query": "password"}},
            {"tool": "search_records", "args": {"query": records_query}},
        ],
        judge,
    )


def judged_with(searches: list[dict], judge: list[dict]) -> ScriptedChatModel:
    return ScriptedChatModel(
        script={
            "gather": [*searches, {"answer": "Draft answer."}],
            "judge": {"requirements": judge},
        }
    )


def test_one_unreadable_source_keeps_what_the_other_source_found(tmp_path):
    profile = nordlicht_copy(tmp_path, "policies", "records")
    shutil.rmtree(profile.evidence_sources["records"].folder)

    result = assess("4.1.2", profile, two_searches(), today=TODAY)

    m2 = requirement(result, "M2")
    assert (m2.verdict, m2.reason) == ("evidenced", "Shown.")
    assert "source_unavailable" in result.warnings


def test_an_unreadable_source_beside_a_refused_one_says_it_could_not_be_read(tmp_path):
    profile = nordlicht_copy(tmp_path, "records")
    shutil.rmtree(profile.evidence_sources["records"].folder)

    result = assess("4.1.2", profile, two_searches(), today=TODAY)

    assert {(r.verdict, r.reason) for r in result.requirements} == {
        ("not-evidenced", "no evidence source could be read")
    }


def test_the_step_names_the_source_that_could_not_be_read(tmp_path):
    profile = nordlicht_copy(tmp_path, "policies", "records")
    shutil.rmtree(profile.evidence_sources["records"].folder)
    model = judged_with([{"tool": "search_records", "args": {"query": "password"}}], [])

    result = assess("4.1.2", profile, model, today=TODAY)

    assert step_texts(result, "tools") == ["Could not read the records."]


def test_a_citation_naming_a_document_in_two_sources_is_dropped_as_a_guard_step(tmp_path):
    profile = nordlicht_copy(tmp_path, "policies", "records")
    policies = profile.evidence_sources["policies"].folder
    records = profile.evidence_sources["records"].folder
    shutil.copy(policies / "authentication-standard.md", records)

    result = assess("4.1.2", profile, two_searches(), today=TODAY)

    assert requirement(result, "M2").verdict == "not-evidenced"
    assert (
        "Dropped a citation for M2 from authentication-standard: the id names a document in "
        "more than one source."
    ) in step_texts(result, GUARD)


# The judge sees each passage's source, kind and staleness, as data.


def judge_evidence(model: ScriptedChatModel) -> list[dict]:
    """The passages inside the <evidence> tags of the judge's prompt."""
    [prompt] = [p for p in model.prompts if "Judge each" in str(p[0].content)]
    text = str(prompt[-1].content)
    body = text.split("<evidence>\n", 1)[1].split("\n</evidence>", 1)[0]
    return json.loads(body)


def test_each_passage_the_judge_sees_carries_its_source_kind_and_staleness():
    model = judged_with(
        [
            {"tool": "search_policies", "args": {"query": "laptop encryption"}},
            {"tool": "search_records", "args": {"query": "authenticator app"}},
        ],
        [],
    )

    assess("5.1.1", NORDLICHT, model, today=TODAY)

    seen = {(p["doc_id"], p["source"], p["kind"], p["stale"]) for p in judge_evidence(model)}
    assert ("encryption-standard", "policies", "documented", True) in seen
    assert ("mfa-rollout-record", "records", "implemented", False) in seen
