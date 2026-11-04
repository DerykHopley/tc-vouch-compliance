import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent / "scripts"
ASSESS = Path(sys.executable).parent / "assess"  # the entry point `uv run assess` runs


def run_assess(*args: str, fake_script: Path | None = None) -> subprocess.CompletedProcess:
    env = {
        k: v for k, v in os.environ.items() if k not in ("OPENROUTER_API_KEY", "ASSESS_FAKE_SCRIPT")
    }
    if fake_script is not None:
        env["ASSESS_FAKE_SCRIPT"] = str(fake_script)
    return subprocess.run(
        [ASSESS, *args], check=False, env=env, capture_output=True, text=True, timeout=60
    )


def test_assess_prints_assessment_json():
    result = run_assess("NK-ENC-01", fake_script=SCRIPTS / "enc_found.json")

    assert result.returncode == 0, result.stderr
    assessment = json.loads(result.stdout)
    assert assessment["control_id"] == "NK-ENC-01"
    assert assessment["verdict"] == "found"
    assert assessment["answer"] == (
        "Laptops use full-disk encryption with centrally stored recovery keys."
    )
    assert "encryption-policy" in {s["doc_id"] for s in assessment["sources"]}
    assert assessment["covered_points"] == ["ENC-1", "ENC-2", "ENC-3"]
    assert assessment["missing_points"] == []
    assert assessment["gap_note"] is None


def test_unknown_control_exits_2_with_one_line_error():
    result = run_assess("NK-XXX-99")

    assert result.returncode == 2
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert "NK-XXX-99" in lines[0]
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_a_gap_prints_its_gap_note():
    result = run_assess("NK-VUL-01", fake_script=SCRIPTS / "vul_gap.json")

    assert result.returncode == 0, result.stderr
    assessment = json.loads(result.stdout)
    assert assessment["verdict"] == "gap"
    assert assessment["covered_points"] == []
    assert assessment["gap_note"] == {
        "control_id": "NK-VUL-01",
        "missing_points": ["VUL-1", "VUL-2", "VUL-3"],
        "owner_role": "Head of IT infrastructure",
    }


BAD_SCRIPTS = {
    "missing-file": None,
    "malformed-json": '{"gather": [',
    "no-answer-turn": '{"gather": [{"tool": "search_policies", "args": {"query": "x"}}]}',
    "bad-verdict": '{"gather": [{"answer": "A.", "verdict": "maybe", "cited": []}]}',
    "bad-judge": '{"gather": [{"answer": "A.", "verdict": "gap", "cited": []}],'
    ' "judge": {"covered_points": "VUL-1"}}',
}


@pytest.mark.parametrize("case", BAD_SCRIPTS)
def test_a_bad_fake_script_exits_2_with_one_line_naming_it(case, tmp_path):
    script = tmp_path / f"{case}.json"
    if BAD_SCRIPTS[case] is not None:
        script.write_text(BAD_SCRIPTS[case])

    result = run_assess("NK-VUL-01", fake_script=script)

    assert result.returncode == 2
    lines = result.stderr.splitlines()
    assert len(lines) == 1, result.stderr
    assert str(script) in lines[0]
    assert "Traceback" not in result.stderr
    if case == "bad-verdict":
        assert "verdict" in lines[0].split(str(script), 1)[1]  # the problem, not the file name
