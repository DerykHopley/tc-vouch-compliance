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
fake model in CI and a real model (vouch.eval_models: OpenRouter or a local Ollama model, by
name). On a real model each case also has its tokens, cost (vouch.spending's UsageMeter: the
reported cost, else the model's price; unknown, never $0, without either) and seconds, and with
a judge, the faithfulness of its answer to its cited passages (vouch.faithfulness). Anything but
the fake runs one case first, prints a cost and time estimate, and runs the rest only after a
typed "yes". Each run's raw results are saved (git-ignored), and a copy where the owner says;
vouch.eval_report renders the report from them without calling a model.
"""

import argparse
import json
import re
import shutil
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from langchain_core.language_models.chat_models import BaseChatModel

from vouch.eval_models import (
    FAKE,
    JUDGE,
    MODELS,
    OLLAMA_URL,
    ChatModels,
    ModelUnavailable,
    OllamaNotRunning,
    chat_models,
    cost_of,
    price_of,
)
from vouch.eval_report import KEPT_DIR  # the runs the owner keeps, committed
from vouch.exit_codes import EXIT_UNAVAILABLE
from vouch.faithfulness import Faithfulness, measure_faithfulness
from vouch.fake_model import ScriptedChatModel
from vouch.graph import JUDGED_VERDICTS, Assessment, Verdict, assess
from vouch.isa import Column, UnknownControlError
from vouch.paths import FIXTURES_DIR, REPO_DIR
from vouch.profile import (
    DEFAULT_PROFILE,
    ComplianceProfile,
    ProfileError,
    UnknownProfileError,
    load_named_profile,
)
from vouch.settings import Settings, SettingsError, load_settings
from vouch.spending import Price, Usage, UsageMeter

EXPECTED_ANSWERS = "expected-answers.yaml"  # in the profile's fixtures folder
FAKE_SCRIPTS_DIR = REPO_DIR / "evals" / "scripts"  # one folder per profile, a script per case
RESULTS_DIR = REPO_DIR / "evals" / "results"  # git-ignored
CONFIRM_WORD = "yes"  # typed to run the cases after the dry run
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
    seconds: float | None = field(default=None, compare=False)  # None: not timed
    usage: Usage | None = field(default=None, compare=False)  # None: the fake model
    faithfulness: Faithfulness | None = field(default=None, compare=False)  # None: not judged
    judge_usage: Usage | None = field(default=None, compare=False)
    judge_seconds: float | None = field(default=None, compare=False)

    @property
    def cost_usd(self) -> float | None:
        """The assessment's cost; None on the fake or when some call's cost isn't known."""
        return cost_of(self.usage) if self.usage is not None else None

    @property
    def judge_cost_usd(self) -> float | None:
        return cost_of(self.judge_usage) if self.judge_usage is not None else None

    @property
    def spent(self) -> tuple[float, float | None]:
        """(seconds, US dollars) the case took, its judgement included. The fake is free; the
        dollars are None when any metered part's cost isn't known."""
        seconds = (self.seconds or 0.0) + (self.judge_seconds or 0.0)
        parts = [(self.usage, self.cost_usd), (self.judge_usage, self.judge_cost_usd)]
        if any(usage is not None and cost is None for usage, cost in parts):
            return seconds, None
        return seconds, sum(cost or 0.0 for _, cost in parts)

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

    @property
    def faithfulness(self) -> tuple[float, int] | None:
        """(mean faithfulness, cases judged); None when none was."""
        judged = [
            s.faithfulness.score
            for s in self.scores
            if s.faithfulness is not None and s.faithfulness.score is not None
        ]
        return (sum(judged) / len(judged), len(judged)) if judged else None

    def extend(self, other: "EvalReport") -> None:
        self.scores.extend(other.scores)
        self.bad_cases.extend(other.bad_cases)


def _columns(case: EvalCase, profile: ComplianceProfile) -> dict[str, Column] | str:
    """Each applicable requirement's column, or why the case doesn't fit the profile: an
    unknown control, a requirement that doesn't apply, one named twice or one left out."""
    try:
        control = profile.isa_control(case.control_id)
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


@dataclass(frozen=True)
class EvalModels:
    """The model a run assesses on: its name, a fresh model per case (`model_for(control_id)`),
    and its price (US dollars per million tokens; None: not known). On anything but the fake,
    each case's calls are metered."""

    name: str
    model_for: Callable[[str], BaseChatModel]
    price: Price | None = None

    @property
    def metered(self) -> bool:
        return self.name != FAKE


@dataclass(frozen=True)
class FaithfulnessJudge:
    """The faithfulness judge: its name, a fresh chat model per judgement, its price, and the
    measure (vouch.faithfulness's; a fixed one in tests)."""

    name: str
    models: ChatModels
    price: Price | None = None
    measure: Callable[..., Faithfulness] = measure_faithfulness


def _passages(assessment: Assessment) -> tuple[str, ...]:
    """The checked quotes the assessment cites, each once, in order."""
    quotes = (c.quote for r in assessment.requirements for c in r.citations)
    return tuple(dict.fromkeys(quotes))


def _judged(
    judge: FaithfulnessJudge, question: str, assessment: Assessment, clock: Callable[[], float]
) -> dict[str, Any]:
    """The answer's faithfulness, and what judging it took."""
    meter = UsageMeter(judge.name, judge.price)
    start = clock()
    faithfulness = judge.measure(
        question, assessment.answer, _passages(assessment), judge.models([meter]), judge.name
    )
    return {
        "faithfulness": faithfulness,
        "judge_usage": meter.usage(),
        "judge_seconds": clock() - start,
    }


def run_eval(
    cases: Iterable[EvalCase],
    profile: ComplianceProfile,
    models: "EvalModels | Callable[[str], BaseChatModel]",
    *,
    today: date,
    judge: FaithfulnessJudge | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> EvalReport:
    """Assess each case's control on its own model (`model_for(control_id)`) and score it.

    A case that doesn't fit the profile is a bad case, not run. A run that raises, the model
    not made included, is a failed case with the error's message; the other cases still run.
    `today` is fixed, so the stale rule gives the same verdicts on every run. Each case is
    timed; on a metered model its calls' tokens and cost are kept (a failed case's too: they
    may have been billed); with a judge, each case that ran has its answer judged."""
    if not isinstance(models, EvalModels):
        models = EvalModels(FAKE, models)
    report = EvalReport(scores=[])
    for case in cases:
        columns = _columns(case, profile)
        if isinstance(columns, str):
            report.bad_cases.append(BadCase(case.control_id, columns))
            continue
        meter = UsageMeter(models.name, models.price) if models.metered else None
        start = clock()
        try:
            model = models.model_for(case.control_id)
            if meter is not None:
                model.callbacks = [meter]  # as vouch.model_choice gives the fake its meter
            assessment = assess(case.control_id, profile, model, today=today)
        # Any exception, as a real provider raises its own kinds; the eval goes on.
        except Exception as error:  # noqa: BLE001
            failed = failed_case(case, f"{type(error).__name__}: {error}")
            usage = meter.usage() if meter is not None else None
            report.scores.append(replace(failed, seconds=clock() - start, usage=usage))
            continue
        measured: dict[str, Any] = {
            "seconds": clock() - start,
            "usage": meter.usage() if meter is not None else None,
        }
        if judge is not None:
            question = profile.isa_control(case.control_id).question
            measured |= _judged(judge, question, assessment, clock)
        report.scores.append(replace(score_case(case, assessment, columns), **measured))
    return report


def _money(dollars: float | None) -> str:
    return "cost unknown" if dollars is None else f"${dollars:.4f}"


def _duration(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest} s" if minutes else f"{seconds:.1f} s"


def estimate(dry: CaseScore, remaining: int, models: EvalModels, judge: str | None) -> str:
    """What the dry run took and cost, and from it the estimate for the remaining cases."""
    seconds, dollars = dry.spent
    judged = f", judged by {judge}," if judge else ""
    why_unknown = (
        f" (no reported cost, and no price for {models.name}"
        + (" or the judge" if judge else "")
        + " in the runner's model list)"
        if dollars is None
        else ""
    )
    lines = [
        (
            f"Dry run: {dry.control_id} on {models.name}{judged} took {_duration(seconds)}, "
            f"{_money(dollars)}{why_unknown}."
        )
    ]
    if dry.error is not None:
        lines.append(f"It failed ({dry.error}), so the estimate may be off.")
    rest = None if dollars is None else dollars * remaining
    lines.append(
        f"Estimate for the other {remaining} cases: about {_duration(seconds * remaining)}, "
        f"{_money(rest)}."
    )
    return "\n".join(lines)


def run_confirmed(
    cases: Sequence[EvalCase],
    profile: ComplianceProfile,
    models: EvalModels,
    *,
    today: date,
    confirm: Callable[[str], str] | None = None,  # None: typed at the terminal (input)
    judge: FaithfulnessJudge | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[EvalReport, bool]:
    """Run the first case that fits the profile (the dry run), print the estimate for the rest,
    and run them only when `confirm` is answered with the typed word; anything else, or no
    answer, stops with nothing more sent. Returns the report and whether the rest ran."""
    report = EvalReport(scores=[])
    remaining = list(cases)
    while remaining and not report.scores:
        first, remaining = remaining[:1], remaining[1:]
        report.extend(run_eval(first, profile, models, today=today, judge=judge, clock=clock))
    if not remaining:
        return report, True
    print(estimate(report.scores[0], len(remaining), models, judge.name if judge else None))
    try:
        answer = (confirm or input)(
            f"Type {CONFIRM_WORD} to run the other {len(remaining)} cases: "
        )
    except EOFError:  # no one to answer
        answer = ""
    if answer.strip().lower() != CONFIRM_WORD:
        print(f"Stopped: the other {len(remaining)} cases were not run; nothing more was sent.")
        return report, False
    report.extend(run_eval(remaining, profile, models, today=today, judge=judge, clock=clock))
    return report, True


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


def _tokens(score: CaseScore) -> int | None:
    """The case's tokens: the meter's on a real model, else the graph's own count."""
    if score.usage is not None:
        return score.usage.input_tokens + score.usage.output_tokens
    if score.assessment is not None:
        return score.assessment.token_usage.get("total_tokens")
    return None


def _measures(report: EvalReport) -> list[str]:
    """The faithfulness, tokens, cost and seconds lines, where the run has them."""
    lines = []
    faithfulness = report.faithfulness
    if faithfulness is not None:
        mean, judged = faithfulness
        lines.append(f"Faithfulness: {mean:.2f} mean over {judged} judged answers")
    timed = [s for s in report.scores if s.seconds is not None]
    if not timed:
        return lines
    tokens = [t for t in map(_tokens, timed) if t is not None]
    if tokens:
        lines.append(f"Tokens per assessment: {sum(tokens) / len(tokens):.0f} mean")
    metered = [s for s in timed if s.usage is not None]
    if metered:
        costs = [s.cost_usd for s in metered]
        known = None if None in costs else sum(c or 0.0 for c in costs) / len(costs)
        lines.append(f"Cost per assessment: {_money(known)} mean")
    seconds = sum(s.seconds or 0.0 for s in timed) / len(timed)
    lines.append(f"Seconds per assessment: {seconds:.1f} mean")
    return lines


def summary(report: EvalReport, *, profile: str, model: str, today: date) -> str:
    """A short summary: the scores and measures, a line per case, then the bad cases."""
    lines = [
        f"Eval of {profile} on {model}, staleness judged on {today.isoformat()}",
        f"Verdict accuracy: {_count(report.requirements_right, 'requirements')}",
        f"Control accuracy: {_count(report.controls_right, 'controls')}",
        f"Citation precision: {_count(report.citations_right, 'cited documents')}",
        *_measures(report),
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


def _slug(model: str) -> str:
    """The model's name, safe in a file name."""
    return re.sub(r"[^A-Za-z0-9.-]+", "_", model)


@dataclass(frozen=True)
class RunInfo:
    """What a run was: its profile and staleness date, its model and judge, and whether the
    cases after the dry run were confirmed."""

    profile: str
    today: date
    model: str = FAKE
    judge: str | None = None
    confirmed: bool = True


def write_results(report: EvalReport, folder: Path, run: RunInfo) -> Path:
    """The raw results (the run, its scores and measures, and each case's score, cost, seconds,
    faithfulness and full Assessment) as one JSON file in `folder`, named by the time now, the
    profile and the model; returns its path. vouch.eval_report reads it."""
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    path = folder / f"{now:%Y%m%dT%H%M%S%fZ}-{run.profile}-{_slug(run.model)}.json"
    faithfulness = report.faithfulness
    raw = {
        "run_at": now.isoformat(),
        "profile": run.profile,
        "today": run.today.isoformat(),
        "model": run.model,
        "judge": run.judge,
        "confirmed": run.confirmed,
        "verdict_accuracy": report.verdict_accuracy,
        "control_accuracy": report.control_accuracy,
        "citation_precision": report.citation_precision,
        "requirements_right": report.requirements_right,
        "controls_right": report.controls_right,
        "citations_right": report.citations_right,
        "faithfulness": faithfulness[0] if faithfulness else None,
        "faithfulness_judged": faithfulness[1] if faithfulness else 0,
        "cases": [
            {
                **asdict(replace(s, assessment=None)),
                "tokens": _tokens(s),
                "cost_usd": s.cost_usd,
                "judge_cost_usd": s.judge_cost_usd,
                "assessment": _raw(s.assessment),
            }
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


def real_models(name: str, settings: Settings, ollama_url: str) -> Callable[[str], BaseChatModel]:
    """A fresh model per case for an OpenRouter or Ollama model. Raises ModelUnavailable."""
    make = chat_models(name, settings, ollama_url=ollama_url)
    return lambda control_id: make([])


def model_list() -> str:
    """The runner's model list and judge, with prices."""
    lines = ["Models (US dollars per million tokens, input / output):"]
    for m in MODELS:
        price = f"${m.price.input_per_million} / ${m.price.output_per_million}" if m.price else "?"
        lines.append(f"  {m.id}  {price}  {m.why}")
    lines.append(f"Judge: {JUDGE or 'none named yet (#53); give one with --judge'}")
    lines.append(f"Also: {FAKE} (the scripted fake model, free), and any ollama/<name> (local).")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m vouch.evals",
        description="Score Vouch's verdicts and citations against a profile's expected "
        "answers. On the scripted fake model (the default) it is offline and free; any other "
        "model runs one case first, prints a cost and time estimate, and waits for a typed "
        "yes. `python -m vouch.evals report --help` renders the report from saved results.",
    )
    parser.add_argument("--profile", default=DEFAULT_PROFILE, metavar="<name>")
    parser.add_argument(
        "--model",
        default=FAKE,
        metavar="<name>",
        help=f"{FAKE}, an OpenRouter model id, or ollama/<name> (default: {FAKE})",
    )
    parser.add_argument(
        "--judge",
        default=JUDGE,
        metavar="<name>",
        help="the faithfulness judge: an OpenRouter model id or ollama/<name>"
        + (f" (default: {JUDGE})" if JUDGE else "; without one, faithfulness isn't measured"),
    )
    parser.add_argument("--ollama-url", default=OLLAMA_URL, metavar="<url>")
    parser.add_argument("--results", type=Path, default=RESULTS_DIR, metavar="<folder>")
    parser.add_argument(
        "--keep",
        type=Path,
        nargs="?",
        const=KEPT_DIR,
        metavar="<folder>",
        help=f"also copy the raw results here (default: {KEPT_DIR.relative_to(REPO_DIR)})",
    )
    parser.add_argument("--models", action="store_true", help="list the models and stop")
    return parser


def main(argv: list[str] | None = None) -> int:
    """`python -m vouch.evals [--profile <name>] [--model <name>] [--judge <name>] [--results
    <folder>] [--keep [<folder>]]`: run the profile's cases, print the summary, save the raw
    results and summary. `python -m vouch.evals report ...` renders the report instead.
    Exits 1 when a case failed or was bad, 2 when the profile, its cases or the model can't be
    used, 3 when Ollama isn't running. Stopping at the estimate exits 0."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["report"]:
        from vouch.eval_report import main as report_main

        return report_main(argv[1:])
    args = _parser().parse_args(argv)
    if args.models:
        print(model_list())
        return 0
    try:
        profile = load_named_profile(args.profile)
        cases = load_cases(FIXTURES_DIR / profile.id / EXPECTED_ANSWERS, profile.id)
        judge = None
        if args.model == FAKE:
            models = EvalModels(FAKE, fake_models(FAKE_SCRIPTS_DIR / profile.id))
        else:
            settings = load_settings()
            models = EvalModels(
                args.model,
                real_models(args.model, settings, args.ollama_url),
                price_of(args.model),
            )
        if args.judge:
            settings = load_settings()
            judge_models = chat_models(args.judge, settings, ollama_url=args.ollama_url)
            judge = FaithfulnessJudge(args.judge, judge_models, price_of(args.judge))
    except OllamaNotRunning as error:
        print(f"eval: {error}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    except (UnknownProfileError, ProfileError, CasesError, SettingsError, ModelUnavailable) as e:
        print(f"eval: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    confirmed = True
    if models.metered or judge is not None:
        report, confirmed = run_confirmed(
            cases.cases, profile, models, today=cases.today, judge=judge
        )
    else:
        report = run_eval(cases.cases, profile, models, today=cases.today)
    run = RunInfo(profile.id, cases.today, models.name, judge.name if judge else None, confirmed)
    raw = write_results(report, args.results, run)
    model = FAKE_MODEL if models.name == FAKE else models.name
    text = summary(report, profile=profile.id, model=model, today=cases.today)
    summary_path = raw.with_name(f"{raw.stem}-summary.txt")
    summary_path.write_text(text, encoding="utf-8")
    print(text + f"Raw results: {raw}")
    if args.keep is not None:
        args.keep.mkdir(parents=True, exist_ok=True)
        shutil.copy2(raw, args.keep / raw.name)
        shutil.copy2(summary_path, args.keep / summary_path.name)
        print(f"Kept a copy in {args.keep}")
    if not confirmed:
        return 0
    return 0 if report.all_ran else EXIT_CASES_FAILED


if __name__ == "__main__":
    sys.exit(main())
