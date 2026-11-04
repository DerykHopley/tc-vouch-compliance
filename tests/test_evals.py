"""The offline eval: Vouch's verdicts and citations scored against the expected answers."""

import json
import re
import time
from datetime import date
from pathlib import Path

import pytest

from vouch import ScriptedChatModel, load_named_profile
from vouch.citations import Citation
from vouch.evals import (
    CasesError,
    EvalCase,
    EvalReport,
    ExpectedRequirement,
    failed_case,
    load_cases,
    main,
    run_eval,
    score_case,
)
from vouch.graph import Assessment, RequirementVerdict, ShouldScore


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
RESULT = result(
    "1.1.1",
    "partly-evidenced",
    got("M1", "evidenced", "information-security-policy", "policy-publication-record"),
    got("M2", "not-evidenced"),
    got("S1", "not-evidenced", column="should"),
)


def test_a_case_scores_each_requirement_its_control_and_its_citations():
    score = score_case(CASE, RESULT)

    assert (score.right, score.total) == (2, 3)
    assert [r.requirement_id for r in score.requirements if not r.right] == ["M2"]
    assert score.expected_verdict == "evidenced"
    assert score.verdict == "partly-evidenced"
    assert score.control_right is False
    # M1 cites two documents, one of them expected: precision 1 of 2.
    assert (score.cited_right, score.cited) == (1, 2)


def test_a_report_totals_accuracy_per_requirement_and_per_control_and_precision():
    right_control = score_case(
        EvalCase("5.2.9", (expected("M1", "not-evidenced"),)),
        result("5.2.9", "not-evidenced", got("M1", "not-evidenced")),
    )
    report = EvalReport([score_case(CASE, RESULT), right_control])

    assert report.verdict_accuracy == 3 / 4
    assert report.control_accuracy == 1 / 2
    assert report.citation_precision == 1 / 2


def test_a_failed_run_counts_every_requirement_and_its_control_wrong():
    failed = failed_case(CASE, "ScriptExhaustedError: script has 2 gather turns")
    report = EvalReport([score_case(CASE, RESULT), failed])

    assert failed.error == "ScriptExhaustedError: script has 2 gather turns"
    assert (failed.right, failed.total, failed.control_right) == (0, 3, False)
    assert report.verdict_accuracy == 2 / 6
    assert report.control_accuracy == 0
    assert report.citation_precision == 1 / 2  # a failed run cites nothing


def test_with_nothing_cited_precision_is_not_a_number_but_none():
    nothing = score_case(
        EvalCase("5.2.9", (expected("M1", "not-evidenced"),)),
        result("5.2.9", "not-evidenced", got("M1", "not-evidenced")),
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


def test_a_case_naming_a_requirement_that_does_not_apply_is_a_bad_case_not_run():
    not_applicable = EvalCase("5.2.4", (expected("M4", "evidenced"),))  # the profile excludes it
    unknown_control = EvalCase("9.9.9", (expected("M1", "evidenced"),))
    asked: list[str] = []

    def model_for(control_id: str) -> ScriptedChatModel:
        asked.append(control_id)
        return scripted(BACKUP_SCRIPT)

    report = run_eval([not_applicable, unknown_control, BACKUP], NORDLICHT, model_for, today=TODAY)

    assert [c for c, _ in report.bad_cases] == ["5.2.4", "9.9.9"]
    assert "M4" in report.bad_cases[0][1] and "not applicable" in report.bad_cases[0][1]
    assert "9.9.9" in report.bad_cases[1][1]
    assert asked == ["5.2.9"]
    assert [s.control_id for s in report.scores] == ["5.2.9"]
    assert report.verdict_accuracy == 1


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


def test_the_runner_prints_accuracy_and_precision_for_nordlicht_on_the_fake_model(tmp_path, capsys):
    started = time.monotonic()
    code = main(["--results", str(tmp_path)])
    took = time.monotonic() - started

    out = capsys.readouterr().out
    assert code == 0
    assert re.search(r"Verdict accuracy: \d+ of 84 requirements \(\d+%\)", out)
    assert re.search(r"Control accuracy: \d+ of 12 controls \(\d+%\)", out)
    assert re.search(r"Citation precision: \d+ of \d+ cited documents \(\d+%\)", out)
    assert "Failed" not in out and "Bad case" not in out
    assert took < 30  # the fast lane's budget for the whole eval is a minute

    [raw] = tmp_path.glob("*.json")
    saved = json.loads(raw.read_text())
    assert [c["control_id"] for c in saved["cases"]] == [
        "1.1.1", "1.6.1", "1.6.2", "2.1.3", "4.1.2", "4.1.3",
        "4.2.1", "5.1.1", "5.1.2", "5.2.4", "5.2.5", "5.2.9",
    ]  # fmt: skip
    assert saved["today"] == "2026-11-04"
    assert all(c["assessment"]["requirements"] for c in saved["cases"])
    assert (tmp_path / raw.name.replace(".json", "-summary.txt")).read_text() in out


def test_cases_come_from_the_expected_answers_read_when_the_eval_runs(tmp_path):
    answers = tmp_path / "expected-answers.yaml"
    answers.write_text(
        "profile: nordlicht\nas_of: 2026-11-04\ncontrols:\n"
        '  - id: "5.2.9"\n    requirements:\n'
        "      - {id: M1, verdict: not-evidenced, cites: [], why: Nothing.}\n"
    )

    cases = load_cases(answers)

    assert cases.profile == "nordlicht"
    assert cases.today == date(2026, 11, 4)
    assert cases.cases == (EvalCase("5.2.9", (expected("M1", "not-evidenced"),)),)


def test_an_expected_answers_file_without_a_date_is_refused_naming_the_file(tmp_path):
    answers = tmp_path / "expected-answers.yaml"
    answers.write_text("profile: nordlicht\ncontrols: []\n")

    with pytest.raises(CasesError, match="expected-answers.yaml.*as_of"):
        load_cases(answers)
