"""The eval runner on real models: a dry run and an estimate first, the typed confirmation, a
model failing mid-run, faithfulness, tokens, cost and seconds, and the saved raw results.

The "real" model here is a fake endpoint: the scripted fake model, played through the runner's
real-model path, each model made counted. Nothing leaves the machine and no key is needed.
"""

import json

import pytest

from vouch import evals
from vouch.eval_models import LOCAL_PRICE, cost_of
from vouch.evals import (
    EvalModels,
    FaithfulnessJudge,
    load_cases,
    main,
    run_confirmed,
    run_eval,
)
from vouch.faithfulness import Faithfulness
from vouch.fake_model import ScriptedChatModel
from vouch.paths import FIXTURES_DIR, REPO_DIR
from vouch.profile import load_named_profile
from vouch.spending import Price, UsageMeter

NORDLICHT = load_named_profile("nordlicht")
SCRIPTS = REPO_DIR / "evals" / "scripts" / "nordlicht"
ANSWERS = FIXTURES_DIR / "nordlicht" / "expected-answers.yaml"
CASES = load_cases(ANSWERS, "nordlicht")
PRICED = Price(1.0, 2.0)
MODEL = "someone/priced-model"


class FakeEndpoint:
    """Makes the scripted fake model per case, counting each one made; `failing` controls
    raise as a provider's error would."""

    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        self.asked: list[str] = []
        self.failing = failing

    def __call__(self, control_id: str) -> ScriptedChatModel:
        self.asked.append(control_id)
        if control_id in self.failing:
            raise ConnectionError("the provider answered 502")
        return ScriptedChatModel.from_file(SCRIPTS / f"{control_id}.json")


def models(endpoint: FakeEndpoint, price: Price | None = PRICED, name: str = MODEL) -> EvalModels:
    return EvalModels(name, endpoint, price)


def answers(*replies: str):
    """A confirmation prompt answered with these, in turn; it records each prompt."""
    asked: list[str] = []
    queue = list(replies)

    def confirm(prompt: str) -> str:
        asked.append(prompt)
        return queue.pop(0)

    confirm.asked = asked  # type: ignore[attr-defined]
    return confirm


def test_the_dry_run_prints_an_estimate_and_stops_without_a_typed_yes(capsys):
    endpoint = FakeEndpoint()
    confirm = answers("no")

    report, confirmed = run_confirmed(
        CASES.cases, NORDLICHT, models(endpoint), today=CASES.today, confirm=confirm
    )

    out = capsys.readouterr().out
    assert confirmed is False
    assert endpoint.asked == [CASES.cases[0].control_id]  # no further calls
    assert [s.control_id for s in report.scores] == [CASES.cases[0].control_id]
    assert "Estimate for the other 11 cases" in out
    assert "$" in out and " s" in out
    assert "Stopped" in out
    assert confirm.asked == ["Type yes to run the other 11 cases: "]


def test_no_answer_at_all_stops_too(capsys):
    endpoint = FakeEndpoint()

    def closed_stdin(prompt: str) -> str:
        raise EOFError

    _, confirmed = run_confirmed(
        CASES.cases, NORDLICHT, models(endpoint), today=CASES.today, confirm=closed_stdin
    )

    assert confirmed is False
    assert len(endpoint.asked) == 1


def test_a_typed_yes_runs_the_rest():
    endpoint = FakeEndpoint()

    report, confirmed = run_confirmed(
        CASES.cases, NORDLICHT, models(endpoint), today=CASES.today, confirm=answers("yes")
    )

    assert confirmed is True
    assert endpoint.asked == [c.control_id for c in CASES.cases]
    assert report.verdict_accuracy == 1


def test_a_model_failing_one_case_records_it_and_the_run_goes_on():
    failing = CASES.cases[3].control_id
    endpoint = FakeEndpoint(failing=frozenset({failing}))

    report, _ = run_confirmed(
        CASES.cases, NORDLICHT, models(endpoint), today=CASES.today, confirm=answers("yes")
    )

    assert endpoint.asked == [c.control_id for c in CASES.cases]
    [failed] = [s for s in report.scores if s.error is not None]
    assert failed.control_id == failing
    assert "ConnectionError: the provider answered 502" in failed.error
    assert sum(s.error is None for s in report.scores) == 11


def test_each_case_has_its_tokens_seconds_and_cost_from_the_price(monkeypatch):
    ticks = iter(range(100))
    case = CASES.cases[0]

    report = run_eval(
        [case], NORDLICHT, models(FakeEndpoint()), today=CASES.today, clock=lambda: next(ticks)
    )

    [score] = report.scores
    assert score.seconds == 1
    assert score.usage is not None and score.usage.input_tokens > 0
    expected = PRICED.cost(score.usage.input_tokens, score.usage.output_tokens)
    assert score.cost_usd == pytest.approx(expected)


def test_a_real_model_with_no_known_price_has_an_unknown_cost_never_zero(capsys):
    report, _ = run_confirmed(
        CASES.cases[:2],
        NORDLICHT,
        models(FakeEndpoint(), price=None),
        today=CASES.today,
        confirm=answers("no"),
    )

    [score] = report.scores
    assert score.usage is not None and score.usage.input_tokens > 0
    assert score.cost_usd is None
    out = capsys.readouterr().out
    assert "cost unknown" in out
    assert "$0" not in out


def test_a_local_model_costs_nothing():
    meter = UsageMeter("ollama/qwen3:14b", LOCAL_PRICE)

    assert cost_of(meter.usage()) == 0


def test_the_judge_gets_the_question_the_answer_and_the_cited_passages():
    seen: list[tuple[str, str, tuple[str, ...]]] = []

    def measure(question, answer, passages, chat, name):
        seen.append((question, answer, tuple(passages)))
        return Faithfulness(0.75, "three of four claims backed")

    case = next(c for c in CASES.cases if any(r.cites for r in c.requirements))
    judge = FaithfulnessJudge("judge/fixed", lambda callbacks: None, PRICED, measure=measure)

    report = run_eval([case], NORDLICHT, models(FakeEndpoint()), today=CASES.today, judge=judge)

    [score] = report.scores
    assert score.faithfulness == Faithfulness(0.75, "three of four claims backed")
    [(question, answer, passages)] = seen
    assert question == NORDLICHT.isa_control(case.control_id).question
    assert answer == score.assessment.answer
    quotes = {c.quote for r in score.assessment.requirements for c in r.citations}
    assert set(passages) == quotes and passages


def test_a_case_whose_run_failed_is_not_judged():
    def measure(*args):
        raise AssertionError("judged a failed run")

    judge = FaithfulnessJudge("judge/fixed", lambda callbacks: None, PRICED, measure=measure)
    case = CASES.cases[0]

    report = run_eval(
        [case],
        NORDLICHT,
        models(FakeEndpoint(failing=frozenset({case.control_id}))),
        today=CASES.today,
        judge=judge,
    )

    assert report.scores[0].faithfulness is None


# Through the command: the model by name, the confirmation on stdin, the raw results kept.


def fake_endpoint_for(endpoint: FakeEndpoint):
    def real_models(name, settings, ollama_url):
        return endpoint

    return real_models


def test_the_command_stops_at_the_estimate_and_saves_the_dry_run(tmp_path, monkeypatch, capsys):
    endpoint = FakeEndpoint()
    monkeypatch.setattr(evals, "real_models", fake_endpoint_for(endpoint))
    monkeypatch.setattr("builtins.input", answers("n"))

    code = main(["--model", MODEL, "--results", str(tmp_path)])

    assert code == 0
    assert len(endpoint.asked) == 1
    [raw] = tmp_path.glob("*.json")
    saved = json.loads(raw.read_text(encoding="utf-8"))
    assert saved["model"] == MODEL
    assert saved["confirmed"] is False
    assert len(saved["cases"]) == 1
    assert saved["cases"][0]["seconds"] is not None
    assert "someone_priced-model" in raw.name


def test_a_real_model_with_no_key_is_refused_and_the_fake_still_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)  # a key loaded by .envrc stays unused
    assert main(["--model", "anthropic/claude-sonnet-5.5", "--results", str(tmp_path)]) == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []

    assert main(["--model", "fake", "--results", str(tmp_path)]) == 0


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_the_command_with_ollama_not_running_gives_the_start_command(tmp_path, capsys):
    from test_eval_models import closed_port

    url = f"http://127.0.0.1:{closed_port()}"

    code = main(["--model", "ollama/qwen3:14b", "--ollama-url", url, "--results", str(tmp_path)])

    assert code == 3
    assert "ollama serve" in capsys.readouterr().err


def test_keep_copies_the_raw_results_where_the_owner_says(tmp_path):
    kept = tmp_path / "kept"

    assert main(["--results", str(tmp_path / "results"), "--keep", str(kept)]) == 0

    [raw] = (tmp_path / "results").glob("*.json")
    assert (kept / raw.name).read_text(encoding="utf-8") == raw.read_text(encoding="utf-8")


def test_the_fake_model_runs_without_a_dry_run(tmp_path, monkeypatch):
    def no_input(prompt):
        raise AssertionError("asked to confirm a free run")

    monkeypatch.setattr("builtins.input", no_input)

    assert main(["--results", str(tmp_path)]) == 0


def test_the_model_list_is_printed(capsys):
    assert main(["--models"]) == 0
    out = capsys.readouterr().out
    assert "anthropic/claude-haiku-5.5" in out
    assert "judge" in out.lower()


def test_both_profiles_have_twelve_cases_that_fit_them_about_24_in_all():
    """Alpenwerk's cases come from its draft expected answers (#50 reviews them); on a model
    that fails every case they are failed cases, never bad ones."""
    total = 0
    for name in ("nordlicht", "alpenwerk"):
        profile = load_named_profile(name)
        cases = load_cases(FIXTURES_DIR / name / "expected-answers.yaml", name)
        everything = frozenset(c.control_id for c in cases.cases)

        report = run_eval(
            cases.cases, profile, models(FakeEndpoint(failing=everything)), today=cases.today
        )

        assert report.bad_cases == []
        assert len(report.scores) == 12
        total += len(report.scores)
    assert total == 24
