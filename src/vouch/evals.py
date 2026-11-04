"""The offline eval: run `assess` on each case and score it against the expected answers.

A case is one control of a compliance profile, with the verdict a good assessment gives each
applicable requirement and the evidence documents a good answer cites. The cases are the
profile's expected answers (`fixtures/<profile>/expected-answers.yaml`), read when the eval
runs, so an edit there counts on the next run.

Scores: verdict accuracy per requirement (right verdicts of all requirements), per control
(the rolled-up verdict against the roll-up of the expected verdicts), and citation precision
(cited documents that are expected, of all documents cited after the quote check). A run that
errors counts as a failed case, every requirement and its control wrong; a case that doesn't
fit the profile is a bad case, reported and not run.

The model is a parameter (`model_for`, one per case), so the same runner drives the scripted
fake model in CI and a real model in M4.
"""

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from langchain_core.language_models.chat_models import BaseChatModel

from vouch.fake_model import ScriptedChatModel
from vouch.graph import Assessment, RequirementVerdict, Verdict, assess, roll_up
from vouch.isa import UnknownControlError, load_isa_control
from vouch.paths import FIXTURES_DIR, REPO_DIR
from vouch.profile import (
    DEFAULT_PROFILE,
    ComplianceProfile,
    ProfileError,
    UnknownProfileError,
    load_named_profile,
)

EXPECTED_ANSWERS = "expected-answers.yaml"  # in the profile's fixtures folder
FAKE_SCRIPTS_DIR = REPO_DIR / "evals" / "scripts"  # one folder per profile, a script per case
RESULTS_DIR = REPO_DIR / "evals" / "results"  # git-ignored
FAKE_MODEL = "the scripted fake model"


@dataclass(frozen=True)
class ExpectedRequirement:
    """One requirement's expected verdict and the documents a good answer cites."""

    id: str
    verdict: Verdict
    cites: frozenset[str]


@dataclass(frozen=True)
class EvalCase:
    """One control and the expected answer for each of its applicable requirements."""

    control_id: str
    requirements: tuple[ExpectedRequirement, ...]


@dataclass(frozen=True)
class RequirementScore:
    requirement_id: str
    expected: Verdict
    verdict: Verdict | None  # None: the run failed, or gave no verdict for it
    cited: frozenset[str] = frozenset()  # documents its checked citations name
    expected_cites: frozenset[str] = frozenset()

    @property
    def right(self) -> bool:
        return self.verdict == self.expected

    @property
    def cited_right(self) -> int:
        return len(self.cited & self.expected_cites)


@dataclass(frozen=True)
class CaseScore:
    control_id: str
    requirements: tuple[RequirementScore, ...]
    expected_verdict: Verdict | None
    verdict: Verdict | None  # None: the run failed
    error: str | None = None  # why the run failed
    assessment: Assessment | None = field(default=None, compare=False)  # the raw result

    @property
    def right(self) -> int:
        return sum(r.right for r in self.requirements)

    @property
    def total(self) -> int:
        return len(self.requirements)

    @property
    def control_right(self) -> bool:
        return self.error is None and self.verdict == self.expected_verdict

    @property
    def cited(self) -> int:
        return sum(len(r.cited) for r in self.requirements)

    @property
    def cited_right(self) -> int:
        return sum(r.cited_right for r in self.requirements)


def score_case(case: EvalCase, assessment: Assessment) -> CaseScore:
    """The case's requirements scored against the assessment's verdicts and citations."""
    given = {r.id: r for r in assessment.requirements}
    scores, as_expected = [], []
    for req in case.requirements:
        result = given.get(req.id)
        if result is None:
            scores.append(RequirementScore(req.id, req.verdict, None, expected_cites=req.cites))
            continue
        doc_ids = frozenset(c.doc_id for c in result.citations)
        scores.append(RequirementScore(req.id, req.verdict, result.verdict, doc_ids, req.cites))
        as_expected.append(RequirementVerdict(req.id, result.column, "", req.verdict, ""))
    expected_verdict = roll_up(as_expected)
    return CaseScore(
        case.control_id, tuple(scores), expected_verdict, assessment.verdict, None, assessment
    )


def failed_case(case: EvalCase, error: str) -> CaseScore:
    """A case whose run errored: no verdicts, so every requirement and the control are wrong."""
    scores = tuple(
        RequirementScore(r.id, r.verdict, None, expected_cites=r.cites) for r in case.requirements
    )
    return CaseScore(case.control_id, scores, None, None, error)


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


@dataclass
class EvalReport:
    """The scored cases of one eval run, and the bad cases left out of it."""

    scores: list[CaseScore]
    bad_cases: list[tuple[str, str]] = field(default_factory=list)  # (control id, problem)

    @property
    def verdict_accuracy(self) -> float | None:
        """Right verdicts of all requirements scored; None when none were."""
        return _share(sum(s.right for s in self.scores), sum(s.total for s in self.scores))

    @property
    def control_accuracy(self) -> float | None:
        """Controls whose rolled-up verdict is the expected one, of all scored."""
        return _share(sum(s.control_right for s in self.scores), len(self.scores))

    @property
    def citation_precision(self) -> float | None:
        """Cited documents that are expected, of all cited; None when nothing was cited."""
        return _share(sum(s.cited_right for s in self.scores), sum(s.cited for s in self.scores))


def case_problem(case: EvalCase, profile: ComplianceProfile) -> str | None:
    """Why the case doesn't fit the profile (an unknown control, a requirement that doesn't
    apply), or None when it fits."""
    try:
        control = load_isa_control(case.control_id)
    except UnknownControlError as error:
        return str(error)
    requirements = profile.requirements_for(control)
    applicable = {r.id for r in requirements.applicable}
    excluded = {n.requirement.id for n in requirements.not_applicable}
    for req in case.requirements:
        if req.id in excluded:
            return f"{req.id} is not applicable in the {profile.id} profile"
        if req.id not in applicable:
            return f"{case.control_id} has no requirement {req.id} at {profile.protection_level}"
    return None


def run_eval(
    cases: Iterable[EvalCase],
    profile: ComplianceProfile,
    model_for: Callable[[str], BaseChatModel],
    *,
    today: date,
) -> EvalReport:
    """Assess each case's control on its own model (`model_for(control_id)`) and score it.

    A case that doesn't fit the profile is a bad case, not run. A run that raises, the model
    not made included, is a failed case with the error's message; the other cases still run.
    `today` is fixed, so the stale rule gives the same verdicts on every run."""
    report = EvalReport(scores=[])
    for case in cases:
        problem = case_problem(case, profile)
        if problem is not None:
            report.bad_cases.append((case.control_id, problem))
            continue
        try:
            assessment = assess(case.control_id, profile, model_for(case.control_id), today=today)
        # Any exception, as a real provider raises its own kinds; the eval goes on.
        except Exception as error:  # noqa: BLE001
            report.scores.append(failed_case(case, f"{type(error).__name__}: {error}"))
            continue
        report.scores.append(score_case(case, assessment))
    return report


@dataclass(frozen=True)
class EvalCases:
    """A profile's cases, and the date staleness is judged on when they run."""

    profile: str
    today: date
    cases: tuple[EvalCase, ...]


class CasesError(ValueError):
    """An expected-answers file that can't be read as cases."""

    def __init__(self, path: Path, problem: str):
        super().__init__(f"bad expected answers {path}: {problem}")


def load_cases(path: Path) -> EvalCases:
    """The cases in an expected-answers file: per control, each requirement's expected verdict
    and cited documents (its `why` is for people). Its `as_of` is the eval's today."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cases = tuple(
            EvalCase(
                str(control["id"]),
                tuple(
                    ExpectedRequirement(
                        str(r["id"]), r["verdict"], frozenset(map(str, r.get("cites") or []))
                    )
                    for r in control["requirements"]
                ),
            )
            for control in data["controls"]
        )
        today = data["as_of"]
        if not isinstance(today, date):
            raise TypeError("as_of is not a date")
        return EvalCases(str(data["profile"]), today, cases)
    except OSError as error:
        raise CasesError(path, error.strerror or "can't be read") from error
    except (yaml.YAMLError, KeyError, TypeError) as error:
        raise CasesError(path, f"{type(error).__name__}: {error}") from error


def _count(part: int, whole: int, noun: str) -> str:
    share = f" ({part / whole:.0%})" if whole else ""
    return f"{part} of {whole} {noun}{share}"


def summary(report: EvalReport, *, profile: str, model: str, today: date) -> str:
    """A short summary: the three scores, a line per case, then the bad cases."""
    scores = report.scores
    right = sum(s.right for s in scores)
    lines = [
        f"Eval of {profile} on {model}, staleness judged on {today.isoformat()}",
        f"Verdict accuracy: {_count(right, sum(s.total for s in scores), 'requirements')}",
        "Control accuracy: "
        + _count(sum(s.control_right for s in scores), len(scores), "controls"),
        "Citation precision: "
        + _count(
            sum(s.cited_right for s in scores), sum(s.cited for s in scores), "cited documents"
        ),
    ]
    for s in scores:
        if s.error is not None:
            lines.append(f"  {s.control_id:<6} Failed: {s.error}")
            continue
        wrong = ", ".join(r.requirement_id for r in s.requirements if not r.right)
        lines.append(
            f"  {s.control_id:<6} {s.right}/{s.total} right"
            + (f" (wrong: {wrong})" if wrong else "")
            + f"; control {s.verdict}, expected {s.expected_verdict}"
            + f"; citations {s.cited_right}/{s.cited} expected"
        )
    lines.extend(f"  Bad case {control_id}: {problem}" for control_id, problem in report.bad_cases)
    return "\n".join(lines) + "\n"


def _jsonable(value: Any) -> Any:
    if isinstance(value, frozenset):
        return sorted(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def write_results(report: EvalReport, folder: Path, *, profile: str, today: date) -> Path:
    """The raw results (each case's score and its full Assessment) as one JSON file in
    `folder`, named by the time now; returns its path."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json"
    raw = {
        "profile": profile,
        "today": today.isoformat(),
        "verdict_accuracy": report.verdict_accuracy,
        "control_accuracy": report.control_accuracy,
        "citation_precision": report.citation_precision,
        "cases": [
            {**asdict(replace(s, assessment=None)), "assessment": _raw(s.assessment)}
            for s in report.scores
        ],
        "bad_cases": [{"control_id": c, "problem": p} for c, p in report.bad_cases],
    }
    path.write_text(json.dumps(raw, indent=2, default=_jsonable, ensure_ascii=False) + "\n")
    return path


def _raw(assessment: Assessment | None) -> dict[str, Any] | None:
    return assessment.to_dict() if assessment is not None else None


def fake_models(folder: Path) -> Callable[[str], BaseChatModel]:
    """A fresh scripted fake model per case, playing `<folder>/<control id>.json`."""
    return lambda control_id: ScriptedChatModel.from_file(folder / f"{control_id}.json")


def main(argv: list[str] | None = None) -> int:
    """`python -m vouch.evals [--profile <name>] [--results <folder>]`: run the profile's
    cases on the scripted fake model, print the summary, save the raw results and summary."""
    parser = argparse.ArgumentParser(
        prog="python -m vouch.evals",
        description="Score Vouch's verdicts and citations against a profile's expected "
        "answers, on the scripted fake model (offline, free).",
    )
    parser.add_argument("--profile", default=DEFAULT_PROFILE, metavar="<name>")
    parser.add_argument("--results", type=Path, default=RESULTS_DIR, metavar="<folder>")
    args = parser.parse_args(argv)
    try:
        profile = load_named_profile(args.profile)
        cases = load_cases(FIXTURES_DIR / profile.id / EXPECTED_ANSWERS)
    except (UnknownProfileError, ProfileError, CasesError) as error:
        print(f"eval: {error}", file=sys.stderr)
        return 2
    models = fake_models(FAKE_SCRIPTS_DIR / profile.id)
    report = run_eval(cases.cases, profile, models, today=cases.today)
    raw = write_results(report, args.results, profile=profile.id, today=cases.today)
    text = summary(report, profile=profile.id, model=FAKE_MODEL, today=cases.today)
    raw.with_name(f"{raw.stem}-summary.txt").write_text(text, encoding="utf-8")
    print(text + f"Raw results: {raw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
