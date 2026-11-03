"""T3: The graph routes each control to found, partial or gap."""
import pytest

from conftest import assessment


def script(query, covered, said="found"):
    return {"gather": [{"tool": "search_policies", "args": {"query": query}},
                       {"answer": "Draft answer.", "verdict": said, "cited": []}],
            "judge": {"covered_points": covered}}


CASES = [
    ("NK-ENC-01", "encrypt", ["ENC-1", "ENC-2", "ENC-3"], "found", ["ENC-1", "ENC-2", "ENC-3"], []),
    ("NK-ACC-01", "review", ["ACC-1"], "partial", ["ACC-1"], ["ACC-2", "ACC-3"]),
    ("NK-VUL-01", "vulnerability", [], "gap", [], ["VUL-1", "VUL-2", "VUL-3"]),
]


@pytest.mark.parametrize("control,query,judged,verdict,covered,missing", CASES, ids=["found", "partial", "gap"])
def test_t3_routes_on_the_judges_coverage(assess, control, query, judged, verdict, covered, missing):
    a = assessment(assess(control, script(query, judged)))
    assert a["verdict"] == verdict
    assert a["covered_points"] == covered
    assert a["missing_points"] == missing


def test_t3_gap_note_names_control_missing_points_and_owner(assess):
    a = assessment(assess("NK-VUL-01", script("vulnerability", [])))
    note = a["gap_note"]
    assert note["control_id"] == "NK-VUL-01"
    assert note["missing_points"] == ["VUL-1", "VUL-2", "VUL-3"]
    assert note["owner_role"] == "Head of IT infrastructure"


@pytest.mark.parametrize("control,query,judged", [CASES[0][:3], CASES[1][:3]], ids=["found", "partial"])
def test_t3_no_gap_note_unless_gap(assess, control, query, judged):
    a = assessment(assess(control, script(query, judged)))
    assert a.get("gap_note") is None


def test_t3_ids_from_another_control_are_ignored(assess):
    a = assessment(assess("NK-ENC-01", script("encrypt", ["ENC-1", "ENC-9"])))
    assert a["covered_points"] == ["ENC-1"]
    assert a["verdict"] == "partial"


def test_t3_the_answer_turns_verdict_no_longer_counts(assess):
    a = assessment(assess("NK-ENC-01", script("encrypt", [], said="found")))
    assert a["verdict"] == "gap"
