import pytest

from vouch import ScriptedChatModel, assess, load_control


def scripted(covered_points, verdict="partial"):
    """A script that searches once, answers, and has the judge name these points."""
    return ScriptedChatModel(
        script={
            "gather": [
                {"tool": "search_policies", "args": {"query": "policy"}},
                {"answer": "Draft answer.", "verdict": verdict, "cited": []},
            ],
            "judge": {"covered_points": covered_points},
        }
    )


@pytest.mark.parametrize(
    ("control_id", "judged", "verdict", "covered", "missing"),
    [
        ("NK-ENC-01", ["ENC-1", "ENC-2", "ENC-3"], "found", ["ENC-1", "ENC-2", "ENC-3"], []),
        ("NK-ACC-01", ["ACC-1"], "partial", ["ACC-1"], ["ACC-2", "ACC-3"]),
        ("NK-VUL-01", [], "gap", [], ["VUL-1", "VUL-2", "VUL-3"]),
    ],
    ids=["found", "partial", "gap"],
)
def test_judged_coverage_routes_the_verdict(control_id, judged, verdict, covered, missing):
    result = assess(load_control(control_id), scripted(judged))

    assert result.verdict == verdict
    assert result.covered_points == covered
    assert result.missing_points == missing


def test_a_gap_is_escalated_to_the_controls_owner():
    result = assess(load_control("NK-VUL-01"), scripted([]))

    assert result.gap_note == {
        "control_id": "NK-VUL-01",
        "missing_points": ["VUL-1", "VUL-2", "VUL-3"],
        "owner_role": "Head of IT infrastructure",
    }


@pytest.mark.parametrize(
    ("control_id", "judged"),
    [("NK-ENC-01", ["ENC-1", "ENC-2", "ENC-3"]), ("NK-ACC-01", ["ACC-1"])],
    ids=["found", "partial"],
)
def test_found_and_partial_have_no_gap_note(control_id, judged):
    result = assess(load_control(control_id), scripted(judged))

    assert result.gap_note is None


def test_ids_from_another_control_are_ignored():
    result = assess(load_control("NK-ENC-01"), scripted(["ENC-1", "ENC-9"]))

    assert result.covered_points == ["ENC-1"]
    assert result.verdict == "partial"


def test_covered_points_follow_the_controls_order():
    result = assess(load_control("NK-ENC-01"), scripted(["ENC-3", "ENC-1"]))

    assert result.covered_points == ["ENC-1", "ENC-3"]
    assert result.missing_points == ["ENC-2"]


def test_the_answer_turns_verdict_is_ignored():
    result = assess(load_control("NK-ENC-01"), scripted([], verdict="found"))

    assert result.verdict == "gap"
