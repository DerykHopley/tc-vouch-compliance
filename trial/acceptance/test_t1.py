"""T1: Assess one control through a LangGraph graph."""
from conftest import assessment

ANSWER = "No evidence of vulnerability handling was found in the policies."


def test_t1_gap_answer_comes_back_with_the_scripts_text(assess):
    script = {"gather": [{"answer": ANSWER, "verdict": "gap", "cited": []}],
              "judge": {"covered_points": []}}
    a = assessment(assess("NK-VUL-01", script))
    assert a["control_id"] == "NK-VUL-01"
    assert a["verdict"] == "gap"
    assert ANSWER in a["answer"]


def test_t1_cli_prints_an_assessment_with_all_four_fields(assess):
    script = {"gather": [{"answer": "Laptops are encrypted.", "verdict": "found", "cited": []}],
              "judge": {"covered_points": ["ENC-1", "ENC-2", "ENC-3"]}}
    a = assessment(assess("NK-ENC-01", script))
    assert {"control_id", "verdict", "answer", "sources"} <= a.keys()
    assert a["control_id"] == "NK-ENC-01"
    assert a["verdict"] in {"found", "partial", "gap"}
    assert isinstance(a["sources"], list)


def test_t1_unknown_control_exits_2_with_one_line_naming_it(assess):
    proc = assess("NK-XXX-99")
    assert proc.returncode == 2, f"exit {proc.returncode}, stderr: {proc.stderr[-1000:]}"
    lines = proc.stderr.strip().splitlines()
    assert len(lines) == 1, proc.stderr
    assert "NK-XXX-99" in lines[0]
    assert "Traceback" not in proc.stderr
