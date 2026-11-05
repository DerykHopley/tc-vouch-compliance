"""The eval report: rendered from saved raw results alone, with no model and no network (the
suite runs with sockets disabled, so any call out would fail the test)."""

import json
from pathlib import Path

import pytest
from test_eval_runner import FakeEndpoint, answers, models

from vouch.eval_report import load_runs, main, render
from vouch.evals import RunInfo, load_cases, run_confirmed, write_results
from vouch.paths import FIXTURES_DIR
from vouch.profile import load_named_profile

NORDLICHT = load_named_profile("nordlicht")
CASES = load_cases(FIXTURES_DIR / "nordlicht" / "expected-answers.yaml", "nordlicht")


def saved_run(folder: Path, model: str = "someone/priced-model") -> Path:
    """A real run on the fake endpoint, saved as the runner saves it."""
    report, confirmed = run_confirmed(
        CASES.cases[:3],
        NORDLICHT,
        models(FakeEndpoint(), name=model),
        today=CASES.today,
        confirm=answers("yes"),
    )
    return write_results(report, folder, RunInfo("nordlicht", CASES.today, model, None, confirmed))


def raw(profile: str, model: str, run_at: str, accuracy: float, **extra) -> dict:
    return {
        "run_at": run_at,
        "profile": profile,
        "today": "2026-11-04",
        "model": model,
        "judge": "judge/model",
        "confirmed": True,
        "verdict_accuracy": accuracy,
        "control_accuracy": 0.5,
        "citation_precision": 0.75,
        "requirements_right": [int(accuracy * 40), 40],
        "controls_right": [6, 12],
        "citations_right": [30, 40],
        "faithfulness": 0.9,
        "faithfulness_judged": 12,
        "cases": [
            {"control_id": "1.1.1", "tokens": 1000, "cost_usd": 0.002, "seconds": 4.0},
            {"control_id": "1.6.1", "tokens": 3000, "cost_usd": 0.004, "seconds": 6.0},
        ],
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
    assert "| anthropic/claude-haiku-5.5 | 0.90 (12 judged) | 0.90 (12 judged) |" in page


def test_the_latest_run_of_a_model_on_a_profile_counts(tmp_path):
    write(tmp_path, "old.json", raw("nordlicht", "m/x", "2026-11-05T10", 0.25))
    write(tmp_path, "new.json", raw("nordlicht", "m/x", "2026-11-06T10", 0.75))

    page = render(load_runs([tmp_path]))

    assert "| m/x | 75% (30/40) |" in page
    assert "new.json" in page and "old.json" not in page.split("## Runs")[1]


def test_a_cost_unknown_in_any_case_shows_as_unknown_never_zero(tmp_path):
    data = raw("nordlicht", "someone/unpriced", "2026-11-05T10", 0.5)
    data["cases"][1]["cost_usd"] = None
    write(tmp_path, "a.json", data)

    page = render(load_runs([tmp_path]))

    assert "| someone/unpriced | cost unknown |" in page


def test_a_run_stopped_at_the_estimate_is_marked(tmp_path):
    write(tmp_path, "a.json", raw("nordlicht", "m/x", "2026-11-05T10", 0.5, confirmed=False))

    page = render(load_runs([tmp_path]))

    assert "dry run only" in page


@pytest.mark.disable_socket  # explicit: rendering must never reach the network
def test_it_renders_from_the_runners_own_saved_results_and_re_renders_the_same(tmp_path):
    saved_run(tmp_path / "results")
    out = tmp_path / "eval-report.md"

    assert main([str(tmp_path / "results"), "--out", str(out)]) == 0
    first = out.read_text(encoding="utf-8")
    assert main([str(tmp_path / "results"), "--out", str(out)]) == 0

    assert out.read_text(encoding="utf-8") == first
    assert "| someone/priced-model |" in first


def test_no_saved_results_is_a_clear_error(tmp_path, capsys):
    assert main([str(tmp_path)]) == 2
    assert "no saved results" in capsys.readouterr().err
