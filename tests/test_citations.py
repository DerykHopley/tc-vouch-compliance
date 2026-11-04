"""Citations: every quote is checked against the document version it cites (#21)."""

import json
import logging

import pytest

from vouch import ScriptedChatModel, assess, load_named_profile
from vouch.citations import Citation

NORDLICHT = load_named_profile("nordlicht")
TRUE_QUOTE = "Passwords have at least 12 characters."
INVENTED = "Passwords have at least 20 characters and expire every 30 days."


def citing(*citations: dict, verdict: str = "evidenced") -> ScriptedChatModel:
    """A run on 4.1.2 where M2 is judged with these citations and the rest are evidenced."""
    judge = [
        {"requirement_id": r, "verdict": "evidenced", "reason": "Shown."}
        for r in ("M1", "S1", "S2", "S3")
    ]
    judge.insert(
        1,
        {"requirement_id": "M2", "verdict": verdict, "reason": "Shown.", "citations": citations},
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


def cite(doc_id: str, quote: str) -> dict:
    return {"doc_id": doc_id, "quote": quote}


def m2(result):
    return next(r for r in result.requirements if r.id == "M2")


def guard_steps(result) -> list[str]:
    return [s["text"] for s in result.steps if s["node"] == "guard"]


def test_a_true_quote_is_kept_with_document_id_version_and_section():
    result = assess("4.1.2", NORDLICHT, citing(cite("authentication-standard", TRUE_QUOTE)))

    assert m2(result).citations == (
        Citation(
            doc_id="authentication-standard",
            version="3.1",
            section="Passwords",
            quote=TRUE_QUOTE,
        ),
    )
    assert m2(result).verdict == "evidenced"
    assert guard_steps(result) == []


def test_an_invented_quote_is_dropped_logged_and_shown_as_a_guard_step(caplog):
    model = citing(
        cite("authentication-standard", TRUE_QUOTE), cite("authentication-standard", INVENTED)
    )

    with caplog.at_level(logging.WARNING, logger="vouch.graph"):
        result = assess("4.1.2", NORDLICHT, model)

    assert [c.quote for c in m2(result).citations] == [TRUE_QUOTE]
    assert m2(result).verdict == "evidenced"  # judged on the citation that is left
    dropped = "Dropped a citation for M2 from authentication-standard: "
    assert guard_steps(result) == [dropped + "the quote isn't in that document."]
    assert INVENTED in caplog.text
    nodes = [s["node"] for s in result.steps]
    assert nodes[-3:] == ["judge", "check_quotes", "guard"]
    assert result.steps[-2]["text"] == (
        "Checked each quote against its document: kept 1 citation, dropped 1."
    )


def test_a_requirement_whose_only_citation_was_dropped_is_not_evidenced():
    result = assess("4.1.2", NORDLICHT, citing(cite("authentication-standard", INVENTED)))

    assert m2(result).verdict == "not-evidenced"
    assert m2(result).citations == ()
    assert "dropped" in m2(result).reason
    assert result.verdict == "partly-evidenced"  # M1 evidenced, M2 no longer


def test_a_partly_evidenced_requirement_whose_only_citation_was_dropped_is_not_evidenced():
    model = citing(cite("authentication-standard", INVENTED), verdict="partly-evidenced")

    assert m2(assess("4.1.2", NORDLICHT, model)).verdict == "not-evidenced"


@pytest.mark.parametrize(
    ("claim", "problem"),
    [
        (cite("backup-policy", TRUE_QUOTE), "no such document"),
        (cite("../profile", "id: nordlicht"), "no such document"),
        (cite("authentication-standard", "   "), "the quote is empty"),
    ],
    ids=["missing-document", "path-outside-the-folders", "empty-quote"],
)
def test_a_quote_naming_a_missing_document_or_empty_is_dropped(claim, problem):
    result = assess("4.1.2", NORDLICHT, citing(claim))

    assert m2(result).citations == ()
    assert m2(result).verdict == "not-evidenced"
    assert guard_steps(result) == [f"Dropped a citation for M2 from {claim['doc_id']}: {problem}."]


def test_a_quote_matches_whatever_its_whitespace_and_a_record_can_be_cited():
    spread = "Passwords have at least\n  12   characters."
    record_quote = "1 account with 40 failed sign-ins from abroad"
    model = citing(cite("authentication-standard", spread), cite("log-review-record", record_quote))

    citations = m2(assess("4.1.2", NORDLICHT, model)).citations

    assert [(c.doc_id, c.version, c.section, c.quote) for c in citations] == [
        ("authentication-standard", "3.1", "Passwords", TRUE_QUOTE),
        ("log-review-record", "2026-09", "Monthly reviews", record_quote),
    ]


def test_a_quote_spanning_two_paragraphs_of_one_section_is_kept():
    quote = (
        "Systems reachable only from the office network may rely on a password alone. "
        "The IT operations lead makes this choice when a system is introduced"
    )

    result = assess("4.1.2", NORDLICHT, citing(cite("authentication-standard", quote)))

    assert m2(result).citations[0].section == "How we chose our sign-in methods"


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
        }
    ]
    assert "sources" not in data
