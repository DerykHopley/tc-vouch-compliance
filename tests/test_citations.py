"""Citations: every quote is checked against the document version it cites (#21)."""

import datetime
import json
import logging
from typing import Any

import pytest
from assess_steps import step_texts

from vouch import ScriptedChatModel, assess, load_named_profile
from vouch.citations import Citation, CitationClaim, DropReason, QuoteChecker
from vouch.graph import GUARD

NORDLICHT = load_named_profile("nordlicht")
TODAY = datetime.date(2026, 11, 4)
TRUE_QUOTE = "Passwords have at least 12 characters."
INVENTED = "Passwords have at least 20 characters and expire every 30 days."
NOT_FOUND = "the quote isn't in that document"
ADMIN_QUOTE = "Administrator accounts always require a hardware security key."


def cite(doc_id: str, quote: str) -> dict:
    return {"doc_id": doc_id, "quote": quote}


def citing(
    *citations: dict, verdict: str = "evidenced", reason: str = "Shown."
) -> ScriptedChatModel:
    """A run on 4.1.2 where M2 is judged with these citations; the rest are evidenced with a
    true quote."""
    judge: list[dict[str, Any]] = [
        {
            "requirement_id": r,
            "verdict": "evidenced",
            "reason": "Shown.",
            "citations": [cite("authentication-standard", ADMIN_QUOTE)],
        }
        for r in ("M1", "S1", "S2", "S3")
    ]
    judge.insert(
        1,
        {"requirement_id": "M2", "verdict": verdict, "reason": reason, "citations": citations},
    )
    return ScriptedChatModel(
        script={
            "gather": [
                {"tool": "search_policies", "args": {"query": "password"}},
                {"answer": "Draft answer."},
            ],
            "judge": {"requirements": judge},
        }
    )


def m2(result):
    return next(r for r in result.requirements if r.id == "M2")


def m2_dropped(result) -> list[str]:
    prefix = "Dropped a citation for M2 from "
    return [t.removeprefix(prefix) for t in step_texts(result, GUARD) if t.startswith(prefix)]


def test_a_true_quote_is_kept_with_document_id_version_and_section():
    result = assess("4.1.2", NORDLICHT, citing(cite("authentication-standard", TRUE_QUOTE)))

    assert m2(result).citations == (
        Citation(
            doc_id="authentication-standard",
            version="3.1",
            section="Passwords",
            quote=TRUE_QUOTE,
            kind="documented",
            stale=False,
            stale_because=None,
        ),
    )
    assert m2(result).verdict == "evidenced"
    assert step_texts(result, GUARD) == []


def test_an_invented_quote_is_dropped_logged_and_shown_as_a_guard_step(caplog):
    model = citing(
        cite("authentication-standard", TRUE_QUOTE), cite("authentication-standard", INVENTED)
    )

    with caplog.at_level(logging.WARNING, logger="vouch.graph"):
        result = assess("4.1.2", NORDLICHT, model)

    assert [c.quote for c in m2(result).citations] == [TRUE_QUOTE]
    assert m2(result).verdict == "evidenced"  # judged on the citation that is left
    assert m2_dropped(result) == ["authentication-standard: the quote isn't in that document."]
    assert INVENTED in caplog.text
    nodes = [s["node"] for s in result.steps]
    assert nodes[nodes.index("judge") :][:3] == ["judge", "check_quotes", GUARD]
    assert step_texts(result, "check_quotes") == [
        "Checked each quote against its document: kept 5 citations, dropped 1."
    ]


def test_a_requirement_whose_only_citation_was_dropped_is_not_evidenced():
    result = assess("4.1.2", NORDLICHT, citing(cite("authentication-standard", INVENTED)))

    assert m2(result).verdict == "not-evidenced"
    assert m2(result).citations == ()
    assert m2(result).reason == (
        "Every citation given for it was dropped by the quote check, so nothing checked shows it."
    )
    assert result.verdict == "partly-evidenced"  # M1 evidenced, M2 no longer


def test_a_partly_evidenced_requirement_whose_only_citation_was_dropped_is_not_evidenced():
    model = citing(cite("authentication-standard", INVENTED), verdict="partly-evidenced")

    assert m2(assess("4.1.2", NORDLICHT, model)).verdict == "not-evidenced"


@pytest.mark.parametrize("verdict", ["evidenced", "partly-evidenced"])
def test_a_requirement_judged_with_no_citation_is_not_evidenced(verdict):
    result = assess("4.1.2", NORDLICHT, citing(verdict=verdict))

    assert m2(result).verdict == "not-evidenced"
    assert m2(result).reason == "No citation given, so nothing checked shows it."
    assert m2_dropped(result) == []


def test_a_not_evidenced_requirement_keeps_the_judges_reason_when_its_citation_is_dropped():
    model = citing(
        cite("authentication-standard", INVENTED),
        verdict="not-evidenced",
        reason="Nothing sets a password length.",
    )

    result = assess("4.1.2", NORDLICHT, model)

    assert (m2(result).verdict, m2(result).reason) == (
        "not-evidenced",
        "Nothing sets a password length.",
    )
    assert m2_dropped(result) == ["authentication-standard: the quote isn't in that document."]


@pytest.mark.parametrize(
    ("claim", "problem"),
    [
        (cite("backup-policy", TRUE_QUOTE), "no such document"),
        (cite("../profile", "id: nordlicht is here"), "no such document"),
        (cite("authentication-standard", "   "), "the quote is empty"),
        (cite("authentication-standard", "at least 12"), "the quote is shorter than 4 words"),
        (cite("authentication-standard", "asswords have at least 12"), NOT_FOUND),
        (cite("log-review-record", "2026-028 | | July 2026 | 2026-08-04"), NOT_FOUND),
    ],
    ids=[
        "missing-document",
        "path-outside-the-folders",
        "empty-quote",
        "too-short",
        "starts-mid-word",
        "stitched-across-table-rows",
    ],
)  # fmt: skip
def test_a_quote_that_fails_the_check_is_dropped_with_its_reason(claim, problem):
    result = assess("4.1.2", NORDLICHT, citing(claim))

    assert m2(result).citations == ()
    assert m2(result).verdict == "not-evidenced"
    assert m2_dropped(result) == [f"{claim['doc_id']}: {problem}."]


def test_a_quote_matches_whatever_its_whitespace_and_a_record_can_be_cited():
    spread = "Passwords have at least\n  12   characters."
    record_quote = "1 account with 40 failed sign-ins from abroad"
    model = citing(cite("authentication-standard", spread), cite("log-review-record", record_quote))

    citations = m2(assess("4.1.2", NORDLICHT, model)).citations

    assert [(c.doc_id, c.version, c.section, c.quote) for c in citations] == [
        ("authentication-standard", "3.1", "Passwords", TRUE_QUOTE),
        ("log-review-record", "2026-09", "Monthly reviews", record_quote),
    ]


def test_a_quote_across_a_heading_is_dropped():
    quote = "second factor. Passwords have at least 12 characters."
    result = assess("4.1.2", NORDLICHT, citing(cite("authentication-standard", quote)))

    assert m2(result).citations == ()


def test_the_citations_reach_the_assessment_as_plain_json():
    result = assess("4.1.2", NORDLICHT, citing(cite("authentication-standard", TRUE_QUOTE)))

    data = json.loads(json.dumps(result.to_dict()))
    assert data["requirements"][1]["citations"] == [
        {
            "doc_id": "authentication-standard",
            "version": "3.1",
            "section": "Passwords",
            "quote": TRUE_QUOTE,
            "kind": "documented",
            "stale": False,
            "stale_because": None,
            "lead_statement": False,
        }
    ]
    assert "sources" not in data


# The quote check itself, on documents written for the test.


def write_doc(folder, name: str, body: str, version: str | None = "1.0") -> None:
    front = f'version: "{version}"\n' if version is not None else "title: No version\n"
    (folder / f"{name}.md").write_text(f"---\n{front}---\n# Policy\n\n{body}\n", encoding="utf-8")


def check(folder, doc_id: str, quote: str):
    return QuoteChecker({"policies": str(folder)}, today=TODAY).check(
        CitationClaim(doc_id=doc_id, quote=quote)
    )


@pytest.mark.parametrize("doc_id", ["ISMS_Policy_v2.1", "ISMS_Policy_v2.1.md"])
def test_any_id_search_can_return_can_be_cited(tmp_path, doc_id):
    write_doc(tmp_path, "ISMS_Policy_v2.1", "All laptops use full disk encryption.")

    checked = check(tmp_path, doc_id, "All laptops use full disk encryption.")

    quote = "All laptops use full disk encryption."
    assert checked == Citation(
        "ISMS_Policy_v2.1",
        "1.0",
        "Policy",
        quote,
        kind="documented",
        stale=True,
        stale_because="no review date",
    )


@pytest.mark.parametrize("doc_id", ["sub/policy", "sub\\policy", "..", "../policy"])
def test_an_id_with_a_path_in_it_names_no_document(tmp_path, doc_id):
    (tmp_path / "sub").mkdir()
    write_doc(tmp_path / "sub", "policy", "All laptops use full disk encryption.")
    write_doc(tmp_path, "policy", "All laptops use full disk encryption.")

    assert check(tmp_path / "sub", doc_id, "All laptops use full disk encryption.") == (
        DropReason.NO_DOCUMENT
    )


def test_a_document_with_no_version_is_dropped(tmp_path):
    write_doc(tmp_path, "draft", "All laptops use full disk encryption.", version=None)

    assert check(tmp_path, "draft", "All laptops use full disk encryption.") == (
        DropReason.NO_VERSION
    )
    assert DropReason.NO_VERSION == "the document has no version"


def test_a_quote_matches_whatever_its_unicode_form_and_zero_width_characters(tmp_path):
    write_doc(tmp_path, "richtlinie", "Die Ma\u00dfnahme gilt f\u00fcr alle Mitarbeiter im Werk.")
    # "u" plus a combining diaeresis (NFD), and a zero-width space after "alle"
    decomposed = "Die Ma\u00dfnahme gilt fu\u0308r alle\u200b Mitarbeiter"

    checked = check(tmp_path, "richtlinie", decomposed)

    assert isinstance(checked, Citation)
    assert checked.quote == "Die Ma\u00dfnahme gilt f\u00fcr alle Mitarbeiter"


def test_each_document_is_read_once_per_check(tmp_path):
    write_doc(tmp_path, "policy", "All laptops use full disk encryption. USB sticks are blocked.")
    checker = QuoteChecker({"policies": str(tmp_path)}, today=TODAY)

    first = checker.check(CitationClaim(doc_id="policy", quote="All laptops use full disk"))
    (tmp_path / "policy.md").unlink()
    second = checker.check(CitationClaim(doc_id="policy", quote="USB sticks are blocked."))

    assert isinstance(first, Citation)
    assert isinstance(second, Citation)  # served from the copy read the first time
