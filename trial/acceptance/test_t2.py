"""T2: The agent searches the company's policies, within an allow-list."""
import pytest

from conftest import MISSING_DIR, POLICY_DOCS, assessment, doc_ids, is_error, search


def test_t2_encrypt_finds_the_encryption_policy_with_a_heading():
    result = search("encrypt")
    assert isinstance(result, list) and result, result
    hits = [p for p in result if p["doc_id"] == "encryption-policy"]
    assert hits, result
    assert all(p["heading"].strip() for p in hits)
    assert all(p["text"].strip() for p in hits)


def test_t2_limit_caps_the_passages():
    result = search("the", limit=1)
    assert isinstance(result, list) and len(result) <= 1, result


BAD = [("", 5), ("   ", 5), ("x" * 201, 5), ("encrypt", 0), ("encrypt", 11)]


@pytest.mark.parametrize("policy_dir", [None, MISSING_DIR], ids=["default-dir", "missing-dir"])
@pytest.mark.parametrize("query,limit", BAD, ids=["empty", "blank", "201-chars", "limit-0", "limit-11"])
def test_t2_bad_input_is_invalid_input_before_any_search(query, limit, policy_dir):
    result = search(query, limit=limit, policy_dir=policy_dir)
    assert is_error(result, "invalid_input"), result


@pytest.mark.parametrize("query,limit", [("a" * 200, 5), ("encrypt", 1), ("encrypt", 10)],
                         ids=["200-chars", "limit-1", "limit-10"])
def test_t2_inputs_at_the_limits_are_accepted(query, limit):
    assert isinstance(search(query, limit=limit), list)


def test_t2_a_path_in_the_query_reads_nothing_outside_the_folder():
    result = search("../../pyproject.toml")
    if isinstance(result, dict):
        assert "error" in result, result
    else:
        assert {p["doc_id"] for p in result} <= POLICY_DOCS, result


def test_t2_a_cited_search_result_becomes_a_source(assess):
    script = {"gather": [{"tool": "search_policies", "args": {"query": "encrypt"}},
                         {"answer": "Laptops use full-disk encryption.", "verdict": "found",
                          "cited": ["encryption-policy"]}],
              "judge": {"covered_points": ["ENC-1", "ENC-2", "ENC-3"]}}
    a = assessment(assess("NK-ENC-01", script))
    assert "encryption-policy" in doc_ids(a["sources"]), a["sources"]
    assert all(s.get("heading") for s in a["sources"]), a["sources"]
    assert a.get("warnings") == [], a.get("warnings")


@pytest.mark.parametrize("gather", [
    [{"tool": "search_policies", "args": {"query": "backup"}}],
    [],
], ids=["searched-something-else", "no-search"])
def test_t2_a_citation_no_search_returned_is_dropped(assess, gather):
    script = {"gather": gather + [{"answer": "Encrypted.", "verdict": "found", "cited": ["encryption-policy"]}],
              "judge": {"covered_points": []}}
    a = assessment(assess("NK-ENC-01", script))
    assert "encryption-policy" not in doc_ids(a["sources"]), a["sources"]


def test_t2_missing_folder_still_finishes_as_gap_with_a_warning(assess):
    script = {"gather": [{"tool": "search_policies", "args": {"query": "encrypt"}},
                         {"answer": "Could not check.", "verdict": "gap", "cited": []}],
              "judge": {"covered_points": []}}
    a = assessment(assess("NK-ENC-01", script, ASSESS_POLICY_DIR=MISSING_DIR))
    assert a["verdict"] == "gap"
    assert "source_unavailable" in a["warnings"], a.get("warnings")
