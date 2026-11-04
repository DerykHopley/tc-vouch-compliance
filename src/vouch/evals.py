"""The offline eval: run `assess` on each eval case and score it against the expected answers.

An eval case is one control of a compliance profile, with the verdict a good assessment gives
each applicable requirement and the evidence documents a good answer cites. The cases are the
profile's expected answers (`fixtures/<profile>/expected-answers.yaml`), read when the eval
runs, so an edit there counts on the next run.

Scores: verdict accuracy per requirement (right verdicts of all requirements), per control
(the rolled-up verdict against the expected verdicts rolled up by the spec's rule, written
again here so a roll-up bug in the graph shows), and citation precision (cited documents that
are expected, of all documents cited after the quote check). A run that errors is a failed
case, every requirement and its control counted wrong; a case that doesn't fit the profile is
a bad case, reported and not run.

The model is a parameter (`model_for`, one per case), so the same runner drives the scripted
fake model in CI and a real model in M4.
"""

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from langchain_core.language_models.chat_models import BaseChatModel

from vouch.fake_model import ScriptedChatModel
from vouch.graph import JUDGED_VERDICTS, Assessment, Verdict, assess
from vouch.isa import Column, UnknownControlError, load_isa_control
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
EXIT_CASES_FAILED = 1  # a case failed or was bad
EXIT_BAD_INPUT = 2

# The spec's roll-up (spec #2): over these columns, not applicable left out.
SPEC_ROLL_UP_COLUMNS: frozenset[Column] = frozenset({"must", "high", "very high", "overlay"})


def spec_roll_up(verdicts: Iterable[tuple[Column, Verdict]]) -> Verdict:
    """The control's verdict by the spec's rule, from (column, verdict) pairs: over must, high,
    very high and overlay requirements, not applicable ones left out, all evidenced gives
    evidenced, none evidenced gives not-evidenced, anything else partly-evidenced. With none
    counted, the control is not applicable."""
    counted = [
        v for column, v in verdicts if column in SPEC_ROLL_UP_COLUMNS and v != "not-applicable"
    ]
    if not counted:
        return "not-applicable"
    evidenced = counted.count("evidenced")
    if evidenced == len(counted):
        return "evidenced"
    return "partly-evidenced" if evidenced else "not-evidenced"


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
class ExpectedRequirement:
    """One requirement's expected verdict, the documents a good answer cites, and why (for
    people, and the fake scripts' reason)."""

    id: str
    verdict: Verdict
    cites: frozenset[str]
    why: str = field(default="", compare=False)

    def unanswered(self) -> RequirementScore:
        """Its score when the run gave it no verdict."""
        return RequirementScore(self.id, self.verdict, None, expected_cites=self.cites)


@dataclass(frozen=True)
class EvalCase:
    """One control and the expected answer for each of its applicable requirements."""

    control_id: str
    requirements: tuple[ExpectedRequirement, ...]


@dataclass(frozen=True)
class BadCase:
    """A case that doesn't fit the profile, so it isn't run."""

    control_id: str
    problem: str


@dataclass(frozen=True)
class CaseScore:
    control_id: str
    requirements: tuple[RequirementScore, ...]
    expected_verdict: Verdict | None  # None: the run failed
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


def score_case(case: EvalCase, assessment: Assessment, columns: Mapping[str, Column]) -> CaseScore:
    """The case's requirements scored against the assessment's verdicts and citations; the
    expected control verdict rolls up the expected verdicts by the spec's rule, each in its
    column (requirement id to column)."""
    given = {r.id: r for r in assessment.requirements}
    scores = []
    for req in case.requirements:
        given_verdict = given.get(req.id)
        if given_verdict is None:
            scores.append(req.unanswered())
            continue
        doc_ids = frozenset(c.doc_id for c in given_verdict.citations)
        scores.append(
            RequirementScore(req.id, req.verdict, given_verdict.verdict, doc_ids, req.cites)
        )
    expected_verdict = spec_roll_up((columns[r.id], r.verdict) for r in case.requirements)
    return CaseScore(
        case.control_id, tuple(scores), expected_verdict, assessment.verdict, None, assessment
    )


def failed_case(case: EvalCase, error: str) -> CaseScore:
    """A case whose run errored: no verdicts, so every requirement and the control are wrong."""
    scores = tuple(r.unanswered() for r in case.requirements)
    return CaseScore(case.control_id, scores, None, None, error)


def _share(count: tuple[int, int]) -> float | None:
    part, whole = count
    return part / whole if whole else None


@dataclass
class EvalReport:
    """The scored cases of one eval run, and the bad cases left out of it."""

    scores: list[CaseScore]
    bad_cases: list[BadCase] = field(default_factory=list)

    @property
    def requirements_right(self) -> tuple[int, int]:
        """(right verdicts, requirements scored)."""
        return sum(s.right for s in self.scores), sum(s.total for s in self.scores)

    @property
    def controls_right(self) -> tuple[int, int]:
        """(controls whose rolled-up verdict is the expected one, controls scored)."""
        return sum(s.control_right for s in self.scores), len(self.scores)

    @property
    def citations_right(self) -> tuple[int, int]:
        """(cited documents that are expected, documents cited)."""
        return sum(s.cited_right for s in self.scores), sum(s.cited for s in self.scores)

    @property
    def verdict_accuracy(self) -> float | None:
        """None when no requirement was scored."""
        return _share(self.requirements_right)

    @property
    def control_accuracy(self) -> float | None:
        return _share(self.controls_right)

    @property
    def citation_precision(self) -> float | None:
        """None when nothing was cited."""
        return _share(self.citations_right)

    @property
    def all_ran(self) -> bool:
        """No case was bad and none failed."""
        return not self.bad_cases and all(s.error is None for s in self.scores)


def _columns(case: EvalCase, profile: ComplianceProfile) -> dict[str, Column] | str:
    """Each applicable requirement's column, or why the case doesn't fit the profile: an
    unknown control, a requirement that doesn't apply, one named twice or one left out."""
    try:
        control = load_isa_control(case.control_id)
    except UnknownControlError as error:
        return str(error)
    requirements = profile.requirements_for(control)
    columns = {r.id: r.column for r in requirements.applicable}
    excluded = {n.requirement.id for n in requirements.not_applicable}
    named: set[str] = set()
    for req in case.requirements:
        if req.id in excluded:
            return f"{req.id} is not applicable in the {profile.id} profile"
        if req.id not in columns:
            return f"{case.control_id} has no requirement {req.id} at {profile.protection_level}"
        if req.id in named:
            return f"the case names {req.id} twice"
        named.add(req.id)
    left_out = [r for r in columns if r not in named]
    if left_out:
        return f"the case leaves out {', '.join(left_out)}"
    return columns


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
        columns = _columns(case, profile)
        if isinstance(columns, str):
            report.bad_cases.append(BadCase(case.control_id, columns))
            continue
        try:
            assessment = assess(case.control_id, profile, model_for(case.control_id), today=today)
        # Any exception, as a real provider raises its own kinds; the eval goes on.
        except Exception as error:  # noqa: BLE001
            report.scores.append(failed_case(case, f"{type(error).__name__}: {error}"))
            continue
        report.scores.append(score_case(case, assessment, columns))
    return report


@dataclass(frozen=True)
class EvalCases:
    """A profile's cases, and the date staleness is judged on when they run."""

    today: date
    cases: tuple[EvalCase, ...]


class CasesError(ValueError):
    """An expected-answers file that can't be read as cases."""

    def __init__(self, path: Path, problem: str):
        super().__init__(f"bad expected answers {path}: {problem}")


def _expected(control_id: str, raw: dict[str, Any]) -> ExpectedRequirement:
    where = f"{control_id} {raw['id']}"
    if raw["verdict"] not in JUDGED_VERDICTS:
        raise ValueError(f"{where}: verdict {raw['verdict']!r} is not one of {JUDGED_VERDICTS}")
    cites = raw.get("cites", [])
    if not isinstance(cites, list):
        raise TypeError(f"{where}: cites must be a list of document ids")
    return ExpectedRequirement(
        str(raw["id"]), raw["verdict"], frozenset(map(str, cites)), str(raw.get("why", ""))
    )


def load_cases(path: Path, profile_id: str) -> EvalCases:
    """The cases in a profile's expected-answers file: per control, each requirement's
    expected verdict, cited documents and why. Its `as_of` is the eval's today. A file for
    another profile, a verdict a judge can't give, or cites that aren't a list is refused."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data["profile"] != profile_id:
            raise ValueError(f"it is for {data['profile']}, not {profile_id}")
        today = data["as_of"]
        if not isinstance(today, date):
            raise TypeError("as_of is not a date")
        cases = tuple(
            EvalCase(
                str(control["id"]),
                tuple(_expected(str(control["id"]), r) for r in control["requirements"]),
            )
            for control in data["controls"]
        )
        return EvalCases(today, cases)
    except OSError as error:
        raise CasesError(path, error.strerror or "can't be read") from error
    except (yaml.YAMLError, KeyError, TypeError, ValueError) as error:
        raise CasesError(path, f"{type(error).__name__}: {error}") from error


def _count(count: tuple[int, int], noun: str) -> str:
    part, whole = count
    share = f" ({part / whole:.0%})" if whole else ""
    return f"{part} of {whole} {noun}{share}"


def summary(report: EvalReport, *, profile: str, model: str, today: date) -> str:
    """A short summary: the three scores, a line per case, then the bad cases."""
    lines = [
        f"Eval of {profile} on {model}, staleness judged on {today.isoformat()}",
        f"Verdict accuracy: {_count(report.requirements_right, 'requirements')}",
        f"Control accuracy: {_count(report.controls_right, 'controls')}",
        f"Citation precision: {_count(report.citations_right, 'cited documents')}",
    ]
    for s in report.scores:
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
    lines.extend(f"  Bad case {b.control_id}: {b.problem}" for b in report.bad_cases)
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
        "bad_cases": [asdict(b) for b in report.bad_cases],
    }
    text = json.dumps(raw, indent=2, default=_jsonable, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _raw(assessment: Assessment | None) -> dict[str, Any] | None:
    return assessment.to_dict() if assessment is not None else None


def fake_models(folder: Path) -> Callable[[str], BaseChatModel]:
    """A fresh scripted fake model per case, playing `<folder>/<control id>.json`."""
    return lambda control_id: ScriptedChatModel.from_file(folder / f"{control_id}.json")


def main(argv: list[str] | None = None) -> int:
    """`python -m vouch.evals [--profile <name>] [--results <folder>]`: run the profile's
    cases on the scripted fake model, print the summary, save the raw results and summary.
    Exits 1 when a case failed or was bad, 2 when the profile or its cases can't be read."""
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
        cases = load_cases(FIXTURES_DIR / profile.id / EXPECTED_ANSWERS, profile.id)
    except (UnknownProfileError, ProfileError, CasesError) as error:
        print(f"eval: {error}", file=sys.stderr)
        return EXIT_BAD_INPUT
    models = fake_models(FAKE_SCRIPTS_DIR / profile.id)
    report = run_eval(cases.cases, profile, models, today=cases.today)
    raw = write_results(report, args.results, profile=profile.id, today=cases.today)
    text = summary(report, profile=profile.id, model=FAKE_MODEL, today=cases.today)
    raw.with_name(f"{raw.stem}-summary.txt").write_text(text, encoding="utf-8")
    print(text + f"Raw results: {raw}")
    return 0 if report.all_ran else EXIT_CASES_FAILED


if __name__ == "__main__":
    sys.exit(main())
