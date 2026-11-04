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


def test_requirements_lists_each_requirement_of_a_control_with_its_text():
    result = run_assess("--requirements", "5.1.1")

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.startswith("5.1.1 To what extent is the use of cryptographic procedures managed?\n")
    assert (
        "M1 (must): All cryptographic procedures used (e.g., encryption, signature, and hash "
        "algorithms, protocols) provide the security required by the respective application "
        "field according to the recognized industry standard to the extent legally feasible.\n"
    ) in out
    assert "S1 (should): A concept for the application of cryptography is defined and implemented." in out
    assert "    - Key strengths,\n" in out  # a sub-item stays under its requirement
    assert (
        "H1 (high): Key sovereignty requirements (particularly in case of external processing) "
        "are determined and fulfilled. (C, I)\n"
    ) in out
    assert [line.split(" ", 1)[0] for line in out.splitlines() if "): " in line] == ["M1", "S1", "H1"]
    assert "VDA ISA 2027.1" in out and "CC BY-ND 4.0" in out  # the credit travels with the text


def test_requirements_of_an_unknown_control_exits_2_with_one_line_error():
    result = run_assess("--requirements", "9.9.9")

    assert result.returncode == 2
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert "9.9.9" in lines[0]
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "content",
    [
        "controls: [\n",
        (
            "source: VDA ISA 2027.1\ncontrols:\n- id: 5.1.1\n  question: Q?\n"
            "  iso_27001: ISO 27001\n  requirements:\n  - {id: M1, column: must, text: null}\n"
        ),
    ],
    ids=["malformed-yaml", "null-text"],
)
def test_requirements_with_a_broken_base_framework_exits_2_naming_the_file(
    content, tmp_path, monkeypatch, capsys
):
    from vouch import cli, isa

    broken = tmp_path / "controls.yaml"
    broken.write_text(content)
    monkeypatch.setattr(isa, "CONTROLS_FILE", broken)

    assert cli.main(["--requirements", "5.1.1"]) == 2
    captured = capsys.readouterr()
    assert len(captured.err.splitlines()) == 1
    assert str(broken) in captured.err
    assert captured.out == ""


@pytest.mark.parametrize(
    "args",
    [["--requirements"], ["--requirements", "5.1.1", "extra"], ["--requirements=5.1.1"]],
    ids=["no-id", "extra-arg", "equals-form"],
)
def test_requirements_misused_exits_2_with_the_usage_line(args):
    result = run_assess(*args)

    assert result.returncode == 2
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("usage: ")
    assert "--requirements <isa-control>" in lines[0]
    assert result.stdout == ""


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
