"""The eval report: rendered from saved raw results alone, with no model and no network (the
suite runs with sockets disabled, so any call out would fail the test)."""

import json
from pathlib import Path

import pytest
from test_eval_runner import FakeEndpoint, answers, models

from vouch.eval_report import (
    assessment_measures,
    load_runs,
    main,
    render,
    routed,
    write_atomic,
)
from vouch.evals import ResultsFile, RunInfo, load_cases, run_confirmed
from vouch.paths import FIXTURES_DIR
from vouch.profile import load_named_profile

NORDLICHT = load_named_profile("nordlicht")
CASES = load_cases(FIXTURES_DIR / "nordlicht" / "expected-answers.yaml", "nordlicht")


def saved_run(folder: Path, model: str = "someone/priced-model") -> Path:
    """A real run on the fake endpoint, saved as the runner saves it."""
    results = ResultsFile(folder, RunInfo("nordlicht", CASES.today, model))
    report, _ = run_confirmed(
        CASES.cases[:3],
        NORDLICHT,
        models(FakeEndpoint(), name=model),
        today=CASES.today,
        confirm=answers("yes"),
        on_case=results.add,
    )
    results.finish(report, "complete")
    return results.path


def case(control_id: str, tokens: int, cost: float | None, seconds: float, **extra) -> dict:
    return {
        "control_id": control_id,
        "error": None,
        "metered": True,
        "tokens": tokens,
        "cost_usd": cost,
        "seconds": seconds,
        "faithfulness": {"score": 0.9, "reason": "r", "error": None},
        **extra,
    }


def raw(profile: str, model: str, run_at: str, accuracy: float, **extra) -> dict:
    return {
        "run_at": run_at,
        "profile": profile,
        "today": "2026-11-04",
        "model": model,
        "judge": "judge/model",
        "status": "complete",
        "verdict_accuracy": accuracy,
        "control_accuracy": 0.5,
        "citation_precision": 0.75,
        "requirements_right": [int(accuracy * 40), 40],
        "controls_right": [6, 12],
        "citations_right": [30, 40],
        "cases": [case("1.1.1", 1000, 0.002, 4.0), case("1.6.1", 3000, 0.004, 6.0)],
        "bad_cases": [],
        **extra,
    }


def write(folder: Path, name: str, data: dict) -> Path:
    path = folder / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_one_table_per_measure_by_model_and_profile(tmp_path):
    write(tmp_path, "a.json", raw("nordlicht", "anthropic/claude-haiku-5.5", "2026-11-05T10", 0.8))
    write(tmp_path, "b.json", raw("alpenwerk", "anthropic/claude-haiku-5.5", "2026-11-05T11", 0.6))
    write(tmp_path, "c.json", raw("nordlicht", "ollama/qwen3:14b", "2026-11-05T12", 0.5))

    page = render(load_runs([tmp_path]))

    for measure in (
        "Verdict accuracy",
        "Control accuracy",
        "Citation precision",
        "Faithfulness",
        "Tokens per assessment",
        "Cost per assessment",
        "Seconds per assessment",
    ):
        assert f"## {measure}" in page
    assert "| Model | alpenwerk | nordlicht |" in page
    assert "| anthropic/claude-haiku-5.5 | 60% (24/40) | 80% (32/40) |" in page
    assert "| ollama/qwen3:14b | not run | 50% (20/40) |" in page
    assert "| anthropic/claude-haiku-5.5 | 2000 | 2000 |" in page  # tokens, the mean
    assert "| anthropic/claude-haiku-5.5 | $0.0030 | $0.0030 |" in page
    assert "| anthropic/claude-haiku-5.5 | 5.0 s | 5.0 s |" in page
    assert "| anthropic/claude-haiku-5.5 | 0.90 (2 judged) | 0.90 (2 judged) |" in page


def test_failed_cases_are_left_out_of_the_means_and_counted():
    failed = case("5.2.9", 99999, 9.99, 999.0, error="ConnectionError: 502", faithfulness=None)
    cases = [case("1.1.1", 1000, 0.002, 4.0), case("1.6.1", 3000, 0.004, 6.0), failed]

    measures = assessment_measures(cases)

    assert (measures.ran, measures.failed) == (2, 1)
    assert measures.tokens == 2000
    assert measures.cost_usd == pytest.approx(0.003)
    assert measures.seconds == 5.0


def test_a_failed_case_shows_as_a_count_in_the_cells(tmp_path):
    data = raw("nordlicht", "m/x", "2026-11-05T10", 0.5)
    data["cases"].append(case("5.2.9", 99999, 9.99, 999.0, error="ConnectionError: 502"))
    write(tmp_path, "a.json", data)

    page = render(load_runs([tmp_path]))

    assert "| m/x | 5.0 s (1 failed, left out) |" in page


def test_the_newest_complete_run_counts_over_newer_dry_runs_and_partial_runs(tmp_path):
    write(tmp_path, "old.json", raw("nordlicht", "m/x", "2026-11-05T10", 0.25))
    write(tmp_path, "full.json", raw("nordlicht", "m/x", "2026-11-06T10", 0.75))
    stopped = raw("nordlicht", "m/x", "2026-11-07T10", 0.1, status="stopped at the estimate")
    write(tmp_path, "dry.json", stopped)
    write(tmp_path, "cut.json", raw("nordlicht", "m/x", "2026-11-08T10", 0.2, status="running"))

    page = render(load_runs([tmp_path]))

    assert "| m/x | 75% (30/40) |" in page
    runs = page.split("## Runs")[1]
    assert "full.json" in runs and not {"old.json", "dry.json", "cut.json"} & set(runs.split("`"))


def test_a_partial_run_stands_in_over_a_dry_run_and_is_marked(tmp_path):
    stopped = raw("nordlicht", "m/x", "2026-11-07T10", 0.1, status="stopped at the estimate")
    write(tmp_path, "dry.json", stopped)
    write(tmp_path, "cut.json", raw("nordlicht", "m/x", "2026-11-06T10", 0.5, status="running"))

    page = render(load_runs([tmp_path]))

    assert "| m/x | 50% (20/40) (partial: 2 cases) |" in page


def test_a_dry_run_is_used_only_when_nothing_else_exists_and_is_marked(tmp_path):
    stopped = raw("nordlicht", "m/x", "2026-11-05T10", 0.5, status="stopped at the estimate")
    write(tmp_path, "a.json", stopped)

    page = render(load_runs([tmp_path]))

    assert "| m/x | 50% (20/40) (dry run only) |" in page


def test_a_cost_unknown_in_any_case_shows_as_unknown_never_zero(tmp_path):
    data = raw("nordlicht", "someone/unpriced", "2026-11-05T10", 0.5)
    data["cases"][1]["cost_usd"] = None
    write(tmp_path, "a.json", data)

    page = render(load_runs([tmp_path]))

    assert "| someone/unpriced | cost unknown |" in page


def test_a_cut_off_file_is_skipped_with_a_warning(tmp_path, capsys):
    write(tmp_path, "a.json", raw("nordlicht", "m/x", "2026-11-05T10", 0.5))
    (tmp_path / "b.json").write_text('{"profile": "nordlicht", "mod', encoding="utf-8")

    page = render(load_runs([tmp_path]))

    assert "| m/x | 50% (20/40) |" in page
    err = capsys.readouterr().err
    assert "skipped" in err and "b.json" in err


@pytest.mark.disable_socket  # explicit: rendering must never reach the network
def test_it_renders_from_the_runners_own_saved_results_and_re_renders_the_same(tmp_path):
    saved_run(tmp_path / "results")
    out = tmp_path / "eval-report.md"

    assert main([str(tmp_path / "results"), "--out", str(out)]) == 0
    first = out.read_text(encoding="utf-8")
    assert main([str(tmp_path / "results"), "--out", str(out)]) == 0

    assert out.read_text(encoding="utf-8") == first
    assert "| someone/priced-model |" in first
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_an_atomic_write_interrupted_leaves_the_old_file_whole(tmp_path, monkeypatch):
    target = tmp_path / "page.md"
    target.write_text("the old page\n", encoding="utf-8")

    def interrupted(source, destination):
        raise KeyboardInterrupt

    monkeypatch.setattr("vouch.eval_report.os.replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        write_atomic(target, "a new page, half writ")

    assert target.read_text(encoding="utf-8") == "the old page\n"
    assert [p.name for p in tmp_path.iterdir()] == ["page.md"]


def test_no_saved_results_is_a_clear_error(tmp_path, capsys):
    assert main([str(tmp_path)]) == 2
    assert "no saved results" in capsys.readouterr().err


def served(models: dict[str, int]) -> dict:
    return {"usage": {"served_models": models}}


def test_the_served_models_show_the_routers_mix_and_not_recorded_for_an_older_result(tmp_path):
    router = "typesafe/jev-router"
    luna, sonnet = "openai/gpt-6-luna", "anthropic/claude-sonnet-5.5"
    new = raw("nordlicht", router, "2026-11-05T10", 0.8)
    new["cases"] = [
        case("1.1.1", 1000, 0.002, 4.0, **served({luna: 4, sonnet: 3})),
        case("1.6.1", 3000, 0.004, 6.0, **served({luna: 3})),
    ]
    direct = raw("nordlicht", sonnet, "2026-11-05T11", 0.8)
    direct["cases"] = [
        case("1.1.1", 1000, 0.002, 4.0, **served({sonnet: 5})),
        case("1.6.1", 1000, 0.002, 4.0, **served({f"{sonnet}-20260928": 2})),  # its dated slug
    ]
    write(tmp_path, "new.json", new)
    write(tmp_path, "direct.json", direct)
    write(tmp_path, "old.json", raw("alpenwerk", router, "2026-11-04T10", 0.6))  # before #185

    page = render(load_runs([tmp_path]))

    table = page.split("## Served models", 1)[1].split("## Runs", 1)[0]
    assert f"| {router} | not recorded | {luna} 7 calls, {sonnet} 3 calls |" in table
    assert f"| {sonnet} | not run | as requested |" in table


@pytest.mark.parametrize(
    ("model", "served", "is_routed"),
    [
        ("anthropic/claude-sonnet-5.5", {"anthropic/claude-sonnet-5.5-20260928": 3}, False),
        ("openai/gpt-6-luna:free", {"openai/gpt-6-luna": 2}, False),  # a :variant dropped
        ("ollama/gpt-oss:20b", {"gpt-oss:20b": 4}, False),
        ("ollama/gpt-oss:20b", {"gpt-oss:120b": 4}, True),  # Ollama's tag is the model
        ("typesafe/jev-router", {"openai/gpt-6-luna-20260901": 1}, True),
        ("typesafe/jev-router", {}, False),
    ],
)
def test_routed_matches_the_same_model_under_its_dated_slug_variant_or_ollama_name(
    model, served, is_routed
):
    assert routed(model, served) is is_routed
