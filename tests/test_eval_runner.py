"""The eval runner on real models: a dry run and an estimate first, the typed confirmation, a
model failing mid-run, faithfulness, tokens, cost and seconds, and the saved raw results.

The "real" model here is a fake endpoint: the scripted fake model, played through the runner's
real-model path, each model made counted. Nothing leaves the machine and no key is needed.
"""

import json

import pytest

from vouch import eval_models, evals
from vouch.eval_models import LOCAL_PRICE, cost_of
from vouch.evals import (
    EvalCase,
    EvalModels,
    ExpectedRequirement,
    FaithfulnessJudge,
    ResultsFile,
    RunInfo,
    estimate,
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
NOT_APPLICABLE = EvalCase("5.1.2", (ExpectedRequirement("S3", "evidenced", frozenset()),))


class FakeEndpoint:
    """Makes the scripted fake model per case, counting each one made and keeping the callbacks
    it was given; `failing` controls raise as a provider's error would, and `interrupt_at`
    stands for Ctrl-C at that control."""

    def __init__(self, failing: frozenset[str] = frozenset(), interrupt_at: str = "") -> None:
        self.asked: list[str] = []
        self.callbacks: list[list] = []
        self.failing = failing
        self.interrupt_at = interrupt_at

    def __call__(self, control_id: str, callbacks: list) -> ScriptedChatModel:
        self.asked.append(control_id)
        self.callbacks.append(callbacks)
        if control_id == self.interrupt_at:
            raise KeyboardInterrupt
        if control_id in self.failing:
            raise ConnectionError("the provider answered 502")
        return ScriptedChatModel.from_file(SCRIPTS / f"{control_id}.json", callbacks=callbacks)


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


def fixed_judge(faithfulness: Faithfulness, cost_known: bool = True) -> FaithfulnessJudge:
    return FaithfulnessJudge(
        "judge/fixed",
        lambda callbacks: None,
        PRICED if cost_known else None,
        measure=lambda *args: faithfulness,
    )


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


@pytest.mark.parametrize("stop", [EOFError, KeyboardInterrupt])
def test_no_answer_or_ctrl_c_at_the_prompt_stops_too(stop, capsys):
    endpoint = FakeEndpoint()

    def no_answer(prompt: str) -> str:
        raise stop

    _, confirmed = run_confirmed(
        CASES.cases, NORDLICHT, models(endpoint), today=CASES.today, confirm=no_answer
    )

    assert confirmed is False
    assert len(endpoint.asked) == 1
    assert "Stopped" in capsys.readouterr().out


def test_a_typed_yes_runs_the_rest():
    endpoint = FakeEndpoint()

    report, confirmed = run_confirmed(
        CASES.cases, NORDLICHT, models(endpoint), today=CASES.today, confirm=answers("yes")
    )

    assert confirmed is True
    assert endpoint.asked == [c.control_id for c in CASES.cases]
    assert report.verdict_accuracy == 1


def test_the_estimate_counts_only_the_cases_that_will_run(capsys):
    cases = [NOT_APPLICABLE, *CASES.cases[:3], NOT_APPLICABLE]

    report, _ = run_confirmed(
        cases, NORDLICHT, models(FakeEndpoint()), today=CASES.today, confirm=answers("no")
    )

    assert "Estimate for the other 2 cases" in capsys.readouterr().out
    assert [b.control_id for b in report.bad_cases] == ["5.1.2", "5.1.2"]


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


def test_each_case_has_its_tokens_seconds_and_cost_from_the_price():
    ticks = iter(range(100))

    report = run_eval(
        CASES.cases[:1],
        NORDLICHT,
        models(FakeEndpoint()),
        today=CASES.today,
        clock=lambda: next(ticks),
    )

    [score] = report.scores
    assert score.seconds == 1
    assert score.usage is not None and score.usage.input_tokens > 0
    expected = PRICED.cost(score.usage.input_tokens, score.usage.output_tokens)
    assert score.cost_usd == pytest.approx(expected)


def test_the_meter_goes_into_the_model_factory_on_a_real_model_and_none_on_the_fake():
    real, fake = FakeEndpoint(), FakeEndpoint()

    run_eval(CASES.cases[:1], NORDLICHT, models(real), today=CASES.today)
    run_eval(CASES.cases[:1], NORDLICHT, models(fake, name="fake"), today=CASES.today)

    [[meter]] = real.callbacks
    assert isinstance(meter, UsageMeter)
    assert fake.callbacks == [[]]


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


@pytest.mark.parametrize(
    ("faithfulness", "why"),
    [
        (
            Faithfulness(None, "The answer cites nothing, so nothing cited to check it against."),
            "cites nothing",
        ),
        (Faithfulness(None, error="ValueError: bad JSON"), "bad JSON"),
    ],
)
def test_a_judge_cost_the_dry_run_cannot_show_is_warned_about_never_priced_at_zero(
    faithfulness, why
):
    [dry] = run_eval(
        CASES.cases[:1],
        NORDLICHT,
        models(FakeEndpoint()),
        today=CASES.today,
        judge=fixed_judge(faithfulness),
    ).scores

    text = estimate(dry, 11, models(FakeEndpoint()), "judge/fixed")

    assert "Warning: the judge's cost can't be estimated" in text
    assert why in text
    assert "judge/fixed (its cost not estimated)" in text.splitlines()[-1]


def test_a_judged_dry_run_includes_the_judges_cost_in_the_estimate():
    [dry] = run_eval(
        CASES.cases[:1],
        NORDLICHT,
        models(FakeEndpoint()),
        today=CASES.today,
        judge=fixed_judge(Faithfulness(0.8, "r")),
    ).scores

    text = estimate(dry, 11, models(FakeEndpoint()), "judge/fixed")

    assert "Warning" not in text
    assert "the judge judge/fixed included" in text


# Through the command: the model by name, the confirmation on stdin, the raw results kept.


def fake_endpoint_for(endpoint: FakeEndpoint):
    def real_models(name, settings, **ollama):
        return endpoint

    return real_models


def saved(folder) -> dict:
    [raw] = folder.glob("*.json")
    return json.loads(raw.read_text(encoding="utf-8"))


def test_the_command_stops_at_the_estimate_and_saves_the_dry_run(tmp_path, monkeypatch, capsys):
    endpoint = FakeEndpoint()
    monkeypatch.setattr(evals, "_judge_name", lambda args: None)  # unjudged: a judge needs a key
    monkeypatch.setattr(evals, "real_models", fake_endpoint_for(endpoint))
    monkeypatch.setattr("builtins.input", answers("n"))

    code = main(["--model", MODEL, "--results", str(tmp_path)])

    assert code == 0
    assert len(endpoint.asked) == 1
    [raw] = tmp_path.glob("*.json")
    data = saved(tmp_path)
    assert data["model"] == MODEL
    assert data["status"] == "stopped at the estimate"
    assert len(data["cases"]) == 1
    assert data["cases"][0]["seconds"] is not None
    assert "someone_priced-model" in raw.name


def test_each_case_is_saved_as_it_runs_so_ctrl_c_keeps_the_paid_ones(tmp_path, monkeypatch):
    stop_at = CASES.cases[4].control_id
    endpoint = FakeEndpoint(interrupt_at=stop_at)
    monkeypatch.setattr(evals, "_judge_name", lambda args: None)  # unjudged: a judge needs a key
    monkeypatch.setattr(evals, "real_models", fake_endpoint_for(endpoint))
    monkeypatch.setattr("builtins.input", answers("yes"))

    code = main(["--model", MODEL, "--results", str(tmp_path)])

    assert code == 130
    data = saved(tmp_path)
    assert data["status"] == "interrupted"
    assert [c["control_id"] for c in data["cases"]] == [c.control_id for c in CASES.cases[:4]]
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_a_crash_mid_run_leaves_the_cases_before_it_saved_whole(tmp_path):
    results = ResultsFile(tmp_path, RunInfo("nordlicht", CASES.today, MODEL))

    def crash_at_third(control_id, callbacks):
        if control_id == CASES.cases[2].control_id:
            raise SystemExit("the machine went away")  # not caught as a failed case
        return FakeEndpoint()(control_id, callbacks)

    with pytest.raises(SystemExit):
        run_eval(
            CASES.cases,
            NORDLICHT,
            EvalModels(MODEL, crash_at_third, PRICED),
            today=CASES.today,
            on_case=results.add,
        )

    data = saved(tmp_path)
    assert data["status"] == "running"
    assert len(data["cases"]) == 2


def test_a_real_model_with_no_key_is_refused_and_the_fake_still_runs(tmp_path, capsys):
    assert main(["--model", "anthropic/claude-sonnet-5.5", "--results", str(tmp_path)]) == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []

    assert main(["--model", "fake", "--results", str(tmp_path)]) == 0


@pytest.mark.parametrize("auth_mode", [None, "bogus"])
def test_the_eval_starts_whatever_the_apps_sign_in_settings_say(
    auth_mode, tmp_path, monkeypatch
):
    """The eval signs nobody in, so Clerk keys with no (or a wrong) auth mode don't stop it;
    it reads only the OpenRouter key (#173)."""
    for name in ("CLERK_SECRET_KEY", "CLERK_PUBLISHABLE_KEY", "CLERK_JWT_KEY"):
        monkeypatch.setenv(name, "clerk-test-value")
    if auth_mode is None:
        monkeypatch.delenv("VOUCH_AUTH_MODE", raising=False)
    else:
        monkeypatch.setenv("VOUCH_AUTH_MODE", auth_mode)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    endpoint = FakeEndpoint()
    seen = []

    def real_models(name, settings, **ollama):
        seen.append(settings.openrouter_api_key.get_secret_value())
        return endpoint

    monkeypatch.setattr(evals, "real_models", real_models)
    monkeypatch.setattr(evals, "_judge_name", lambda args: None)
    monkeypatch.setattr("builtins.input", answers("no"))

    assert main(["--model", MODEL, "--results", str(tmp_path)]) == 0
    assert seen == ["sk-test-not-real"]
    assert saved(tmp_path)["status"] == "stopped at the estimate"


@pytest.mark.parametrize(
    ("named", "judge"), [([], eval_models.JUDGE.id), (["--judge", "j/other"], "j/other")]
)
def test_a_real_model_is_judged_by_the_lists_judge_unless_another_is_named(
    named, judge, tmp_path, monkeypatch, capsys
):
    endpoint = FakeEndpoint()
    monkeypatch.setattr(evals, "real_models", fake_endpoint_for(endpoint))

    code = main(["--model", MODEL, *named, "--results", str(tmp_path)])

    assert code == 2  # the judge runs on OpenRouter, and no key is set
    assert f"The judge {judge} runs on OpenRouter" in capsys.readouterr().err
    assert endpoint.asked == []


@pytest.mark.parametrize(
    ("model", "named", "judge"),
    [
        ("openai/gpt-6-luna", [], "anthropic/claude-sonnet-5.5"),
        ("openai/gpt-6-luna", ["--judge", "j/other"], "j/other"),
    ],
)
def test_a_model_from_the_judges_provider_gets_another_judge_unless_one_is_named(
    model, named, judge, tmp_path, monkeypatch, capsys
):
    endpoint = FakeEndpoint()
    monkeypatch.setattr(evals, "real_models", fake_endpoint_for(endpoint))

    code = main(["--model", model, *named, "--results", str(tmp_path)])

    assert code == 2  # the judge runs on OpenRouter, and no key is set
    captured = capsys.readouterr()
    assert f"The judge {judge} runs on OpenRouter" in captured.err
    assert f"Judge: {judge}" in captured.out
    assert endpoint.asked == []


def test_a_fake_run_never_brings_in_the_lists_judge(tmp_path, monkeypatch):
    """With JUDGE named (#53), a plain fake run without a key still runs, free and unjudged."""

    def no_input(prompt):
        raise AssertionError("asked to confirm a free run")

    monkeypatch.setattr("builtins.input", no_input)

    assert main(["--results", str(tmp_path)]) == 0
    assert saved(tmp_path)["judge"] is None


def test_the_fake_judge_is_refused_before_anything_runs(tmp_path, capsys):
    assert main(["--judge", "fake", "--results", str(tmp_path)]) == 2
    assert "leave out --judge" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_a_real_judge_without_deepeval_is_refused_before_any_paid_call(
    tmp_path, monkeypatch, capsys
):
    endpoint = FakeEndpoint()
    monkeypatch.setattr(evals, "real_models", fake_endpoint_for(endpoint))
    monkeypatch.setattr(evals, "chat_models", lambda name, settings, **kwargs: None)

    def missing():
        raise evals.JudgeUnavailable("DeepEval can't be imported; run `uv sync`.")

    monkeypatch.setattr(evals, "prepare_deepeval", missing)

    code = main(["--model", MODEL, "--judge", "j/x", "--results", str(tmp_path)])

    assert code == 2
    assert "uv sync" in capsys.readouterr().err
    assert endpoint.asked == []


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_the_command_with_ollama_not_running_gives_the_start_command(tmp_path, capsys, closed_port):
    url = f"http://127.0.0.1:{closed_port}"

    code = main(["--model", "ollama/qwen3:14b", "--ollama-url", url, "--results", str(tmp_path)])

    assert code == 3
    assert "ollama serve" in capsys.readouterr().err


def test_a_malformed_ollama_url_is_a_clear_error(tmp_path, capsys):
    code = main(["--model", "ollama/qwen3:14b", "--ollama-url", "localhost", "--results", "x"])

    assert code == 2
    assert "--ollama-url" in capsys.readouterr().err


def test_keep_copies_the_raw_results_where_the_owner_says(tmp_path):
    kept = tmp_path / "kept"

    assert main(["--results", str(tmp_path / "results"), "--keep", str(kept)]) == 0

    [raw] = (tmp_path / "results").glob("*.json")
    assert (kept / raw.name).read_text(encoding="utf-8") == raw.read_text(encoding="utf-8")
    assert [p for p in kept.iterdir() if p.name.endswith(".tmp")] == []


def test_the_fake_model_runs_without_a_dry_run(tmp_path, monkeypatch):
    def no_input(prompt):
        raise AssertionError("asked to confirm a free run")

    monkeypatch.setattr("builtins.input", no_input)

    assert main(["--results", str(tmp_path)]) == 0
    assert saved(tmp_path)["status"] == "complete"


def test_the_model_list_is_printed(capsys):
    assert main(["--models"]) == 0
    out = capsys.readouterr().out
    for m in eval_models.MODELS:
        assert m.id in out
        assert m.source in out
    assert "typesafe/jev-router  price varies per request" in out
    assert f"Judge: {eval_models.JUDGE.id}" in out
    assert eval_models.JUDGE.source in out


def test_both_profiles_have_twelve_cases_that_fit_them_about_24_in_all():
    """Alpenwerk's cases come from its expected answers (approved in #50); on a model
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


def test_the_ollama_timeout_flag_reaches_the_local_model(tmp_path, monkeypatch):
    asked = {}

    def chat_models(name, settings, **kwargs):
        asked.update(kwargs)
        raise eval_models.ModelUnavailable("stop here")

    monkeypatch.setattr(evals, "chat_models", chat_models)

    main(["--model", "ollama/q", "--ollama-timeout", "900", "--results", str(tmp_path)])

    assert asked["ollama_timeout"] == 900


# --- The served models (#185) ------------------------------------------------------------------

ROUTER = "typesafe/jev-router"
LUNA = "openai/gpt-6-luna"


class RoutedEndpoint(FakeEndpoint):
    """The scripted fake model behind a router: every answer names `served` as its model."""

    def __init__(self, served: str) -> None:
        super().__init__()
        self.served = served

    def __call__(self, control_id: str, callbacks: list) -> ScriptedChatModel:
        model = super().__call__(control_id, callbacks)
        model.served_model = self.served
        return model


def test_a_router_runs_raw_results_and_summary_list_the_served_models_with_call_counts(tmp_path):
    results = ResultsFile(tmp_path, RunInfo("nordlicht", CASES.today, ROUTER))
    report, _ = run_confirmed(
        CASES.cases[:2],
        NORDLICHT,
        models(RoutedEndpoint(LUNA), name=ROUTER),
        today=CASES.today,
        confirm=answers("yes"),
        on_case=results.add,
    )
    results.finish(report, "complete")

    saved = json.loads(results.path.read_text(encoding="utf-8"))["cases"]
    calls = [case["usage"]["served_models"][LUNA] for case in saved]
    assert all(n > 0 for n in calls)
    assert all(case["usage"]["served_models"] == {LUNA: n} for case, n in zip(saved, calls))
    text = evals.summary(report, profile="nordlicht", model=ROUTER, today=CASES.today)
    assert f"Served models: {LUNA} {sum(calls)} calls" in text
    first = CASES.cases[0].control_id
    [line] = [line for line in text.splitlines() if line.strip().startswith(first)]
    assert line.endswith(f"; served {LUNA} {calls[0]} calls")


@pytest.mark.parametrize(
    ("requested", "served"),
    [
        (MODEL, MODEL),
        ("anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-5.5-20260928"),  # dated slug
        ("ollama/gpt-oss:20b", "gpt-oss:20b"),  # Ollama answers without the prefix
    ],
    ids=["same-id", "dated-slug", "local"],
)
def test_a_model_asked_for_directly_shows_no_served_models_in_the_summary(requested, served):
    endpoint = RoutedEndpoint(served)
    report = run_eval(CASES.cases[:1], NORDLICHT, models(endpoint, name=requested), today=CASES.today)

    [score] = report.scores
    assert score.usage is not None and set(score.usage.served_models) == {served}  # kept as given
    text = evals.summary(report, profile="nordlicht", model=requested, today=CASES.today)
    assert "served" not in text.lower()
