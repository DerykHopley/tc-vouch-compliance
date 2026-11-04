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
    assert "encryption-standard" in {s["doc_id"] for s in assessment["sources"]}
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


def test_requirements_with_a_profile_mark_nordlichts_5_2_4_m4_not_applicable():
    result = run_assess("--requirements", "5.2.4", "--profile", "nordlicht")

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "Profile: Nordlicht Komponenten GmbH (nordlicht), normal protection\n" in out
    applicable, excluded = out.split("Not applicable:\n")
    listed = [line.split(" ", 1)[0] for line in applicable.splitlines() if "): " in line]
    assert listed == ["M1", "M2", "M3", "M5", "S1", "S2", "S3"]  # no M4, no high or very high
    assert excluded.startswith(
        "M4 (must): When using external IT services, information on the monitoring options "
        "is obtained and considered in the assessment.\n"
        "  Reason: Nordlicht runs its IT systems on premises and uses no external IT services"
    )
    assert "CC BY-ND 4.0" in out


def test_requirements_with_an_unknown_profile_exits_2_with_one_line_error():
    result = run_assess("--requirements", "5.2.4", "--profile", "nobody")

    assert result.returncode == 2
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert "nobody" in lines[0]
    assert result.stdout == ""


def test_a_profile_name_cannot_reach_outside_the_fixtures(capsys):
    from vouch import cli

    assert cli.main(["--requirements", "5.2.4", "--profile", "../fixtures/nordlicht"]) == 2
    captured = capsys.readouterr()
    assert "unknown profile" in captured.err
    assert captured.out == ""


@pytest.mark.parametrize(
    "args",
    [
        ["--profile", "nordlicht", "--requirements", "5.2.4"],
        ["--requirements=5.2.4", "--profile=nordlicht"],
    ],
    ids=["profile-first", "equals-forms"],
)
def test_requirements_and_profile_work_in_any_order_and_with_equals(args, capsys):
    from vouch import cli

    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert out.startswith("5.2.4 ")
    assert "Not applicable:\nM4 (must)" in out


def test_requirements_equals_form_lists_the_control(capsys):
    from vouch import cli

    assert cli.main(["--requirements=5.1.1"]) == 0
    assert capsys.readouterr().out.startswith("5.1.1 ")


def test_help_prints_the_usage_and_exits_0(capsys):
    from vouch import cli

    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "--requirements <isa-control>" in out and "--profile <name>" in out


def test_an_overlay_requirement_shows_its_source_and_the_isa_credit_covers_isa_text_only(
    tmp_path, monkeypatch, capsys
):
    from vouch import cli, profile

    folder = tmp_path / "alpen"
    for name in ("policies", "records", "patch"):
        (folder / name).mkdir(parents=True)
    (folder / "profile.yaml").write_text(
        (profile.NORDLICHT_PROFILE.read_text(encoding="utf-8"))
        .replace("id: nordlicht", "id: alpen")
        .replace(
            "overlays: []",
            "overlays:\n  - name: AT NIS2 (illustrative)\n    requirements:\n"
            "      - {control: 1.6.2, id: O1, text: An early warning goes out within a day.}",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(profile, "FIXTURES_DIR", tmp_path)

    assert cli.main(["--requirements", "1.6.2", "--profile", "alpen"]) == 0
    out = capsys.readouterr().out
    assert "O1 (overlay, AT NIS2 (illustrative)): An early warning goes out within a day.\n" in out
    assert "Source of the ISA text: VDA ISA 2027.1, (c) ENX Association, CC BY-ND 4.0" in out
    assert "O1 is AT NIS2 (illustrative)'s, not ISA text." in out


@pytest.mark.parametrize(
    "args",
    [
        ["--requirements"],
        ["--requirements", "5.1.1", "extra"],
        ["--requirements", "5.1.1", "--profile"],
        ["--requirements", "5.1.1", "--profile", "nordlicht", "extra"],
        ["--profile", "nordlicht"],
        ["NK-ENC-01", "--profile", "nordlicht"],
        [],
        ["--req", "5.1.1"],
    ],
    ids=[
        "no-id", "extra-arg", "profile-without-name", "extra-after-profile",
        "profile-alone", "profile-with-control-run", "nothing", "abbreviated-flag",
    ],
)
def test_requirements_misused_exits_2_with_the_usage_line(args, capsys):
    from vouch import cli

    assert cli.main(args) == 2  # returned, not raised as SystemExit
    capsys.readouterr()
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
