"""The eval report: a Markdown page rendered from saved raw results, with no model call.

`python -m vouch.evals report [<file or folder>...] [--out <file>]` reads the runner's raw
results (vouch.evals.write_results; the kept ones in `evals/kept/` by default) and writes one
table per measure, a row per model and a column per profile, from the latest run of each model
on each profile. Nothing here calls a model or the network, so the page re-renders for free and
the same results give the same page. The reading of the numbers and the recommendation are the
owner's, written around it (#55).
"""

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from vouch.eval_models import FAKE
from vouch.exit_codes import EXIT_BAD_INPUT
from vouch.paths import REPO_DIR

KEPT_DIR = REPO_DIR / "evals" / "kept"
NOT_RUN = "not run"

Run = tuple[str, dict[str, Any]]  # the file's name, its raw results


class ReportError(ValueError):
    """Saved results that can't be read, or none."""


def load_runs(paths: Iterable[Path]) -> list[Run]:
    """Every saved run in `paths` (files, or folders of `*.json`). Raises ReportError."""
    files: list[Path] = []
    for path in paths:
        files.extend(sorted(path.glob("*.json")) if path.is_dir() else [path])
    if not files:
        raise ReportError("no saved results to render")
    runs = []
    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not {"profile", "model", "run_at"} <= data.keys():
                raise ValueError("not the eval runner's raw results")
        except (OSError, ValueError) as error:
            raise ReportError(f"{file}: {error}") from error
        runs.append((file.name, data))
    return runs


def _latest(runs: Sequence[Run]) -> dict[tuple[str, str], Run]:
    """The latest run of each model on each profile."""
    latest: dict[tuple[str, str], Run] = {}
    for run in sorted(runs, key=lambda r: r[1]["run_at"]):
        latest[(run[1]["model"], run[1]["profile"])] = run
    return latest


def _share(data: dict[str, Any], share: str, count: str) -> str:
    value = data.get(share)
    if value is None:
        return "n/a"
    part, whole = data[count]
    return f"{value:.0%} ({part}/{whole})"


def _mean(data: dict[str, Any], key: str) -> float | None:
    values = [c[key] for c in data["cases"] if c.get(key) is not None]
    return sum(values) / len(values) if values else None


def _faithfulness(data: dict[str, Any]) -> str:
    if data.get("faithfulness") is None:
        return "not measured"
    return f"{data['faithfulness']:.2f} ({data['faithfulness_judged']} judged)"


def _tokens(data: dict[str, Any]) -> str:
    mean = _mean(data, "tokens")
    return "n/a" if mean is None else f"{mean:.0f}"


def _cost(data: dict[str, Any]) -> str:
    """The mean cost of an assessment; unknown when any case's is, never $0 for it."""
    if data["model"] == FAKE:
        return "n/a (fake)"
    costs = [c.get("cost_usd") for c in data["cases"]]
    if not costs:
        return "n/a"
    if None in costs:
        return "cost unknown"
    return f"${sum(costs) / len(costs):.4f}"


def _seconds(data: dict[str, Any]) -> str:
    mean = _mean(data, "seconds")
    return "n/a" if mean is None else f"{mean:.1f} s"


MEASURES: tuple[tuple[str, str, Callable[[dict[str, Any]], str]], ...] = (
    (
        "Verdict accuracy",
        "Right verdicts of all requirements scored.",
        lambda d: _share(d, "verdict_accuracy", "requirements_right"),
    ),
    (
        "Control accuracy",
        "Controls whose rolled-up verdict is the expected one.",
        lambda d: _share(d, "control_accuracy", "controls_right"),
    ),
    (
        "Citation precision",
        "Cited documents that are expected, of all documents cited after the quote check.",
        lambda d: _share(d, "citation_precision", "citations_right"),
    ),
    (
        "Faithfulness",
        (
            "DeepEval's faithfulness of each answer to its cited passages, by the judge model; "
            "the mean over the answers judged."
        ),
        _faithfulness,
    ),
    ("Tokens per assessment", "Input and output tokens, the mean.", _tokens),
    (
        "Cost per assessment",
        (
            "US dollars, the mean: OpenRouter's reported cost, else the model's listed price; a "
            "local model costs nothing. The judge's cost is not included."
        ),
        _cost,
    ),
    ("Seconds per assessment", "Wall-clock seconds, the mean.", _seconds),
)


def _table(latest: dict[tuple[str, str], Run], cell: Callable[[dict[str, Any]], str]) -> list[str]:
    models = sorted({model for model, _ in latest})
    profiles = sorted({profile for _, profile in latest})
    lines = [
        "| Model | " + " | ".join(profiles) + " |",
        "|---|" + "---|" * len(profiles),
    ]
    for model in models:
        cells = [cell(latest[(model, p)][1]) if (model, p) in latest else NOT_RUN for p in profiles]
        lines.append(f"| {model} | " + " | ".join(cells) + " |")
    return lines


def _run_line(name: str, data: dict[str, Any]) -> str:
    judge = f", judged by {data['judge']}" if data.get("judge") else ""
    cases = len(data["cases"])
    stopped = ", dry run only" if data.get("confirmed") is False else ""
    return (
        f"- `{name}`: {data['model']} on {data['profile']}{judge}, run {data['run_at']}, "
        f"{cases} cases{stopped}"
    )


def render(runs: Sequence[Run]) -> str:
    """The report page for these runs: a table per measure, then the runs it was made from."""
    latest = _latest(runs)
    lines = [
        "# Eval report",
        "",
        (
            "Rendered from the saved raw results by `python -m vouch.evals report`, without calling "
            "a model. Each cell is the latest run of that model on that profile."
        ),
    ]
    for title, explained, cell in MEASURES:
        lines += ["", f"## {title}", "", explained, "", *_table(latest, cell)]
    lines += ["", "## Runs", ""]
    lines += [_run_line(*run) for run in sorted(latest.values(), key=lambda r: r[0])]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Render the report to `--out`, or print it. Exits 2 when there's nothing to render."""
    parser = argparse.ArgumentParser(
        prog="python -m vouch.evals report",
        description="Render the eval report from saved raw results (no model, no network).",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[KEPT_DIR],
        metavar="<file or folder>",
        help=f"raw results (default: {KEPT_DIR.relative_to(REPO_DIR)})",
    )
    parser.add_argument("--out", type=Path, metavar="<file>")
    args = parser.parse_args(argv)
    try:
        page = render(load_runs(args.paths))
    except ReportError as error:
        print(f"eval report: {error}", file=sys.stderr)
        return EXIT_BAD_INPUT
    if args.out is None:
        print(page, end="")
    else:
        args.out.write_text(page, encoding="utf-8")
        print(f"Wrote {args.out}")
    return 0
