"""The offline eval: Vouch's verdicts and citations scored against the expected answers."""

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import vouch.graph
from vouch import ScriptedChatModel, load_named_profile
from vouch.citations import Citation
from vouch.evals import (
    EXPECTED_ANSWERS,
    BadCase,
    CasesError,
    EvalCase,
    EvalReport,
    ExpectedRequirement,
    failed_case,
    load_cases,
    main,
    run_eval,
    score_case,
    spec_roll_up,
)
from vouch.graph import Assessment, RequirementVerdict, ShouldScore
from vouch.paths import FIXTURES_DIR, REPO_DIR

NORDLICHT_ANSWERS = FIXTURES_DIR / "nordlicht" / EXPECTED_ANSWERS


def cited(*doc_ids: str) -> tuple[Citation, ...]:
    return tuple(
        Citation(d, "1.0", "Section", "a quote of four words", "documented", False, None)
        for d in doc_ids
    )


def result(control_id: str, verdict: str, *requirements: RequirementVerdict) -> Assessment:
    return Assessment(
        control_id=control_id,
        profile_id="nordlicht",
        verdict=verdict,
        should_score=ShouldScore(0, 0),
        requirements=list(requirements),
        answer="",
    )


def got(req_id: str, verdict: str, *doc_ids: str, column: str = "must") -> RequirementVerdict:
    return RequirementVerdict(req_id, column, "text", verdict, "reason", cited(*doc_ids))


def expected(req_id: str, verdict: str, *cites: str) -> ExpectedRequirement:
    return ExpectedRequirement(req_id, verdict, frozenset(cites))


# 1.1.1 by hand: M1 right, M2 wrong, S1 right; expected roll-up (M1 evidenced, M2 evidenced)
# is evidenced, the result rolled up to partly-evidenced, so the control is wrong.
CASE = EvalCase(
    "1.1.1",
    (
        expected("M1", "evidenced", "information-security-policy"),
        expected("M2", "evidenced", "information-security-policy"),
        expected("S1", "not-evidenced"),
    ),
)
COLUMNS = {"M1": "must", "M2": "must", "S1": "should"}
RESULT = result(
    "1.1.1",
    "partly-evidenced",
    got("M1", "evidenced", "information-security-policy", "policy-publication-record"),
    got("M2", "not-evidenced"),
    got("S1", "not-evidenced", column="should"),
)


def test_a_case_scores_each_requirement_its_control_and_its_citations():
    score = score_case(CASE, RESULT, COLUMNS)

    assert (score.right, score.total) == (2, 3)
    assert [r.requirement_id for r in score.requirements if not r.right] == ["M2"]
    assert score.expected_verdict == "evidenced"
    assert score.verdict == "partly-evidenced"
    assert score.control_right is False
    # M1 cites two documents, one of them expected: precision 1 of 2.
    assert (score.cited_right, score.cited) == (1, 2)


@pytest.mark.parametrize(
    ("verdicts", "control"),
    [
        ([("must", "evidenced"), ("high", "evidenced"), ("should", "not-evidenced")], "evidenced"),
        ([("must", "partly-evidenced"), ("overlay", "not-evidenced")], "not-evidenced"),
        ([("must", "evidenced"), ("overlay", "partly-evidenced")], "partly-evidenced"),
        ([("must", "evidenced"), ("very high", "not-evidenced")], "partly-evidenced"),
        ([("must", "not-applicable"), ("must", "evidenced")], "evidenced"),
        ([("should", "evidenced")], "not-applicable"),
    ],
)
def test_the_expected_control_verdict_follows_the_specs_roll_up_rule(verdicts, control):
    assert spec_roll_up(verdicts) == control


def test_a_report_totals_accuracy_per_requirement_and_per_control_and_precision():
    right_control = score_case(
        EvalCase("5.2.9", (expected("M1", "not-evidenced"),)),
        result("5.2.9", "not-evidenced", got("M1", "not-evidenced")),
        {"M1": "must"},
    )
    report = EvalReport([score_case(CASE, RESULT, COLUMNS), right_control])

    assert report.requirements_right == (3, 4)
    assert report.controls_right == (1, 2)
    assert report.citations_right == (1, 2)
    assert report.verdict_accuracy == 3 / 4
    assert report.control_accuracy == 1 / 2
    assert report.citation_precision == 1 / 2


def test_a_failed_run_counts_every_requirement_and_its_control_wrong():
    failed = failed_case(CASE, "ScriptExhaustedError: script has 2 gather turns")
    report = EvalReport([score_case(CASE, RESULT, COLUMNS), failed])

    assert failed.error == "ScriptExhaustedError: script has 2 gather turns"
    assert (failed.right, failed.total, failed.control_right) == (0, 3, False)
    assert report.verdict_accuracy == 2 / 6
    assert report.control_accuracy == 0
    assert report.citation_precision == 1 / 2  # a failed run cites nothing


def test_with_nothing_cited_precision_is_not_a_number_but_none():
    nothing = score_case(
        EvalCase("5.2.9", (expected("M1", "not-evidenced"),)),
        result("5.2.9", "not-evidenced", got("M1", "not-evidenced")),
        {"M1": "must"},
    )

    assert EvalReport([nothing]).citation_precision is None
    assert EvalReport([]).verdict_accuracy is None


# Running cases: bad cases, failed runs, the real cases on the fake model.

NORDLICHT = load_named_profile("nordlicht")
TODAY = date(2026, 11, 4)
BACKUP_SCRIPT = {
    "gather": [
        {"tool": "search_policies", "args": {"query": "backup recovery"}},
        {"answer": "No backup or recovery concept was found."},
    ],
    "judge": {
        "requirements": [
            {"requirement_id": r, "verdict": "not-evidenced", "reason": "Nothing covers it."}
            for r in ("M1", "M2", "S1")
        ]
    },
}
BACKUP = EvalCase("5.2.9", tuple(expected(r, "not-evidenced") for r in ("M1", "M2", "S1")))


def scripted(script: dict) -> ScriptedChatModel:
    return ScriptedChatModel(script=script)


def backup_models(asked: list[str] | None = None):
    def model_for(control_id: str) -> ScriptedChatModel:
        if asked is not None:
            asked.append(control_id)
        return scripted(BACKUP_SCRIPT)

    return model_for


def test_a_case_naming_a_requirement_that_does_not_apply_is_a_bad_case_not_run():
    not_applicable = EvalCase("5.1.2", (expected("S3", "evidenced"),))  # the profile excludes it
    unknown_control = EvalCase("9.9.9", (expected("M1", "evidenced"),))
    asked: list[str] = []

    report = run_eval(
        [not_applicable, unknown_control, BACKUP], NORDLICHT, backup_models(asked), today=TODAY
    )

    assert [b.control_id for b in report.bad_cases] == ["5.1.2", "9.9.9"]
    assert "S3" in report.bad_cases[0].problem
    assert "not applicable" in report.bad_cases[0].problem
    assert "9.9.9" in report.bad_cases[1].problem
    assert asked == ["5.2.9"]
    assert [s.control_id for s in report.scores] == ["5.2.9"]
    assert report.verdict_accuracy == 1


def test_a_case_missing_an_applicable_requirement_or_naming_one_twice_is_a_bad_case():
    missing = EvalCase("5.2.9", BACKUP.requirements[:2])  # no S1
    twice = EvalCase("5.2.9", (*BACKUP.requirements, expected("M1", "evidenced")))

    report = run_eval([missing, twice], NORDLICHT, backup_models(), today=TODAY)

    assert report.scores == []
    assert report.bad_cases == [
        BadCase("5.2.9", "the case leaves out S1"),
        BadCase("5.2.9", "the case names M1 twice"),
    ]


def test_a_run_that_errors_is_a_failed_case_with_its_message_and_the_rest_still_run():
    twice = [{"answer": "We are certified."}, {"answer": "We are certified."}]
    claims_a_result = {**BACKUP_SCRIPT, "gather": twice}  # the output check refuses it twice
    models = {"5.2.9": [scripted(claims_a_result), scripted(BACKUP_SCRIPT)]}

    def model_for(control_id: str) -> ScriptedChatModel:
        return models[control_id].pop(0)

    report = run_eval([BACKUP, BACKUP], NORDLICHT, model_for, today=TODAY)

    failed, passed = report.scores
    assert failed.error is not None and "certified" in failed.error
    assert (failed.right, failed.total) == (0, 3)
    assert passed.error is None and passed.right == 3
    assert report.verdict_accuracy == 3 / 6


def test_a_model_that_cannot_be_made_is_a_failed_case_too():
    def model_for(control_id: str) -> ScriptedChatModel:
        return ScriptedChatModel.from_file(Path("/nonexistent") / f"{control_id}.json")

    report = run_eval([BACKUP], NORDLICHT, model_for, today=TODAY)

    assert "bad fake script" in report.scores[0].error


def test_a_broken_roll_up_in_the_graph_lowers_control_accuracy(monkeypatch):
    assert run_eval([BACKUP], NORDLICHT, backup_models(), today=TODAY).control_accuracy == 1

    monkeypatch.setattr(vouch.graph, "roll_up", lambda requirements: "evidenced")
    report = run_eval([BACKUP], NORDLICHT, backup_models(), today=TODAY)

    assert report.control_accuracy == 0
    assert report.verdict_accuracy == 1  # the requirements were still judged right


def test_the_fake_model_scores_every_nordlicht_case_right_and_prints_it(tmp_path, capsys):
    """The fake scripts are drafted from the expected answers, so anything short of all right
    is a harness regression (or expected answers changed without regenerating the scripts)."""
    cases = load_cases(NORDLICHT_ANSWERS, "nordlicht").cases
    requirements = sum(len(c.requirements) for c in cases)
    cites = sum(len(r.cites) for c in cases for r in c.requirements)

    code = main(["--results", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert f"Verdict accuracy: {requirements} of {requirements} requirements (100%)" in out
    assert f"Control accuracy: {len(cases)} of {len(cases)} controls (100%)" in out
    assert f"Citation precision: {cites} of {cites} cited documents (100%)" in out
    assert "Failed" not in out and "Bad case" not in out

    [raw] = tmp_path.glob("*.json")
    saved = json.loads(raw.read_text(encoding="utf-8"))
    assert [c["control_id"] for c in saved["cases"]] == [c.control_id for c in cases]
    assert saved["today"] == "2026-11-04"
    assert all(c["assessment"]["requirements"] for c in saved["cases"])
    summary = tmp_path / raw.name.replace(".json", "-summary.txt")
    assert summary.read_text(encoding="utf-8") in out


def test_the_runner_exits_1_when_a_case_failed_or_was_bad(tmp_path, monkeypatch, capsys):
    def no_model(control_id: str):
        raise RuntimeError("no model")

    monkeypatch.setattr("vouch.evals.fake_models", lambda folder: no_model)

    assert main(["--results", str(tmp_path)]) == 1
    assert "Failed: RuntimeError: no model" in capsys.readouterr().out


# The expected-answers file.


def answers_file(tmp_path: Path, requirement: str, profile: str = "nordlicht") -> Path:
    path = tmp_path / "expected-answers.yaml"
    path.write_text(
        f"profile: {profile}\nas_of: 2026-11-04\ncontrols:\n"
        f'  - id: "5.2.9"\n    requirements:\n      - {requirement}\n',
        encoding="utf-8",
    )
    return path


def test_cases_come_from_the_expected_answers_read_when_the_eval_runs(tmp_path):
    path = answers_file(tmp_path, "{id: M1, verdict: not-evidenced, cites: [], why: Nothing.}")

    cases = load_cases(path, "nordlicht")

    assert cases.today == date(2026, 11, 4)
    assert cases.cases == (EvalCase("5.2.9", (expected("M1", "not-evidenced"),)),)
    assert cases.cases[0].requirements[0].why == "Nothing."


@pytest.mark.parametrize(
    ("requirement", "problem"),
    [
        ("{id: M1, verdict: evidenced, cites: backup-concept, why: A.}", "5.2.9 M1.*cites"),
        ("{id: M1, verdict: compliant, cites: [], why: A.}", "5.2.9 M1.*verdict"),
        ("{id: M1, verdict: not-applicable, cites: [], why: A.}", "5.2.9 M1.*verdict"),
    ],
)
def test_a_requirement_with_bare_cites_or_an_unknown_verdict_is_refused_naming_it(
    tmp_path, requirement, problem
):
    with pytest.raises(CasesError, match=problem):
        load_cases(answers_file(tmp_path, requirement), "nordlicht")


def test_expected_answers_for_another_profile_are_refused(tmp_path):
    path = answers_file(tmp_path, "{id: M1, verdict: evidenced, cites: [], why: A.}", "acme")

    with pytest.raises(CasesError, match="acme.*nordlicht"):
        load_cases(path, "nordlicht")


def test_an_expected_answers_file_without_a_date_is_refused_naming_the_file(tmp_path):
    answers = tmp_path / "expected-answers.yaml"
    answers.write_text("profile: nordlicht\ncontrols: []\n", encoding="utf-8")

    with pytest.raises(CasesError, match="expected-answers.yaml.*as_of"):
        load_cases(answers, "nordlicht")


def test_no_why_in_nordlichts_expected_answers_is_cut_short_by_a_yaml_comment():
    """An unquoted ` #` starts a YAML comment, so the rest of the line is lost."""
    cut = [
        line.strip()
        for line in NORDLICHT_ANSWERS.read_text(encoding="utf-8").splitlines()
        if re.match(r"\s*why: [^\"'].* #", line)
    ]
    assert cut == []


# The script generator.


def test_the_script_generator_gives_the_same_scripts_whatever_the_hash_seed(tmp_path):
    def generate(seed: str) -> dict[str, str]:
        out = tmp_path / seed
        subprocess.run(
            [sys.executable, str(REPO_DIR / "evals" / "scripts" / "generate.py"), "--out", out],
            check=True,
            capture_output=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(out.glob("*.json"))}

    first = generate("1")

    assert len(first) == 12
    assert generate("2") == first
