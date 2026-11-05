"""The eval report: a Markdown page rendered from saved raw results, with no model call.

`python -m vouch.evals report [<file or folder>...] [--out <file>]` reads the runner's raw
results (vouch.evals; the kept results in `evals/kept/` by default) and writes one table per
measure, a row per model and a column per profile. Each cell is the newest complete run of that
model on that profile; a partial run (interrupted) stands in only when there is no complete
one, and a dry run only when there is nothing else, each marked in its cell. Nothing here calls
a model or the network, so the page re-renders for free and the same results give the same
page. In an `--out` file the rendered part sits between two marker comments (TABLES_START,
TABLES_END); the reading of the numbers and the recommendation are written around them by hand
(#55), and a re-render replaces only what is between the markers.

The per-assessment measures (assessment_measures) are computed here once, from the raw cases,
for this page and the runner's summary alike: failed cases are left out of the tokens, cost and
seconds means and counted instead.

The served models (served_models, #185) are the models OpenRouter named as serving a run's
calls: a router's picks, shown for a router run by this page and the summary alike, and "not
recorded" for a run saved before they were.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from vouch.exit_codes import EXIT_BAD_INPUT
from vouch.paths import REPO_DIR

KEPT_DIR = REPO_DIR / "evals" / "kept"
NOT_RUN = "not run"
TABLES_START = (
    "<!-- eval tables: rendered by `python -m vouch.evals report`, edits here are lost -->"
)
TABLES_END = "<!-- end of eval tables -->"
"""Around the rendered part of an `--out` page; the reading lives outside them."""

Status = Literal["running", "complete", "interrupted", "stopped at the estimate"]
"""A run's state in its raw results. `running` left behind means it crashed: partial."""
RANK: dict[str, int] = {"complete": 2, "interrupted": 1, "running": 1}
"""Which run a cell prefers; anything else (a dry run stopped at the estimate) ranks 0."""

Run = tuple[str, dict[str, Any]]  # the file's name, its raw results


class ReportError(ValueError):
    """No saved results to render."""


def write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` whole or not at all: a temporary file beside it, then a rename,
    so a crash or Ctrl-C never leaves a cut-off file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class Measures:
    """One run's per-assessment measures, over the cases that ran (failed ones counted apart).
    `cost_usd` is None when some case's cost isn't known; `metered` is False on the fake."""

    ran: int
    failed: int
    tokens: float | None
    metered: bool
    cost_usd: float | None
    seconds: float | None
    faithfulness: float | None
    judged: int

    def cost_text(self) -> str:
        if not self.metered:
            return "n/a (fake)"
        return "cost unknown" if self.cost_usd is None else f"${self.cost_usd:.4f}"


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def assessment_measures(cases: Sequence[dict[str, Any]]) -> Measures:
    """The measures of a run's raw cases (vouch.evals.raw_case)."""
    ran = [c for c in cases if c.get("error") is None]
    metered = [c for c in ran if c.get("metered")]
    costs = [c.get("cost_usd") for c in metered]
    scores = [
        c["faithfulness"]["score"]
        for c in ran
        if c.get("faithfulness") and c["faithfulness"].get("score") is not None
    ]
    return Measures(
        ran=len(ran),
        failed=len(cases) - len(ran),
        tokens=_mean([c["tokens"] for c in ran if c.get("tokens") is not None]),
        metered=bool(metered),
        cost_usd=None if None in costs else _mean([c or 0.0 for c in costs]),
        seconds=_mean([c["seconds"] for c in ran if c.get("seconds") is not None]),
        faithfulness=_mean(scores),
        judged=len(scores),
    )


def served_models(cases: Sequence[dict[str, Any]]) -> dict[str, int] | None:
    """The calls each model served over a run's metered raw cases, failed ones included (their
    calls were served); None when some case was saved before served models were (#185)."""
    total: dict[str, int] = {}
    for c in cases:
        if not c.get("metered"):
            continue
        served = (c.get("usage") or {}).get("served_models")
        if served is None:
            return None
        for model, calls in served.items():
            total[model] = total.get(model, 0) + calls
    return total


OLLAMA = "ollama/"
"""vouch.eval_models' OLLAMA_PREFIX, which Ollama's answers leave out; written again here so the
report needn't import the model code."""
DATED = re.compile(r"-\d{8}$")


def _same_model(requested: str, served: str) -> bool:
    """Whether `served` is the model asked for, under another of its names. A local model is
    asked for as `ollama/<name>:<tag>` and answers as `<name>:<tag>` (the tag is the model, so
    it's kept). OpenRouter may answer with a model's dated canonical slug
    (`anthropic/claude-sonnet-5.5-20260928`) and without a `:variant` suffix (`:free`), so both
    are left out of the match. That OpenRouter's answers carry the dated slug is unverified
    without a paid call: the catalogue gives each model one, so the match allows for it."""
    if requested.startswith(OLLAMA):
        return requested.removeprefix(OLLAMA) == served

    def plain(model: str) -> str:
        return DATED.sub("", model.split(":", 1)[0])

    return plain(requested) == plain(served)


def routed(model: str, served: dict[str, int] | None) -> bool:
    """Whether some call was served by a model other than the one asked for: a router's run.
    The served ids stay as the answers gave them; only this decision matches names."""
    return any(not _same_model(model, s) for s in served or {})


def served_text(served: dict[str, int]) -> str:
    """The mix, most calls first: "openai/gpt-6-luna 7 calls, anthropic/claude-sonnet-5.5 3 calls"."""
    ordered = sorted(served.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{model} {n} call{'' if n == 1 else 's'}" for model, n in ordered)


def _served_cell(data: dict[str, Any]) -> str:
    served = served_models(data["cases"])
    if served is None:
        return "not recorded"
    if not any(c.get("metered") for c in data["cases"]):
        return "n/a (fake)"
    return served_text(served) if routed(data["model"], served) else "as requested"


def load_runs(paths: Iterable[Path]) -> list[Run]:
    """Every saved run in `paths` (files, or folders of `*.json`). A file that can't be read
    as raw results (cut off, say) is skipped with a warning. Raises ReportError when none is
    left."""
    files: list[Path] = []
    for path in paths:
        files.extend(sorted(path.glob("*.json")) if path.is_dir() else [path])
    runs = []
    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not {"profile", "model", "run_at"} <= data.keys():
                raise ValueError("not the eval runner's raw results")
        except (OSError, ValueError) as error:
            print(f"eval report: skipped {file}: {error}", file=sys.stderr)
            continue
        runs.append((file.name, data))
    if not runs:
        raise ReportError("no saved results to render")
    return runs


def _latest(runs: Sequence[Run]) -> dict[tuple[str, str], Run]:
    """Per model and profile, the newest of the best-ranked runs: complete, else partial, else
    a dry run."""
    chosen: dict[tuple[str, str], Run] = {}
    for run in sorted(runs, key=lambda r: (RANK.get(r[1].get("status", ""), 0), r[1]["run_at"])):
        chosen[(run[1]["model"], run[1]["profile"])] = run
    return chosen


def _mark(data: dict[str, Any]) -> str:
    """What a cell says about a run that isn't complete."""
    status = data.get("status")
    if status == "complete":
        return ""
    if RANK.get(status or "", 0):
        return f" (partial: {len(data['cases'])} cases)"
    return " (dry run only)"


def _share(data: dict[str, Any], share: str, count: str) -> str:
    value = data.get(share)
    if value is None:
        return "n/a"
    part, whole = data[count]
    return f"{value:.0%} ({part}/{whole})"


def _faithfulness(m: Measures) -> str:
    return "not measured" if m.faithfulness is None else f"{m.faithfulness:.2f} ({m.judged} judged)"


def _failed(m: Measures) -> str:
    return f" ({m.failed} failed, left out)" if m.failed else ""


Cell = Callable[[dict[str, Any], Measures], str]

MEASURES: tuple[tuple[str, str, Cell], ...] = (
    (
        "Verdict accuracy",
        "Right verdicts of all requirements scored (a failed case's count as wrong).",
        lambda d, m: _share(d, "verdict_accuracy", "requirements_right"),
    ),
    (
        "Control accuracy",
        "Controls whose rolled-up verdict is the expected one.",
        lambda d, m: _share(d, "control_accuracy", "controls_right"),
    ),
    (
        "Citation precision",
        "Cited documents that are expected, of all documents cited after the quote check.",
        lambda d, m: _share(d, "citation_precision", "citations_right"),
    ),
    (
        "Faithfulness",
        (
            "DeepEval's faithfulness of each answer to its cited passages, by the judge model; "
            "the mean over the answers judged (one citing nothing or claiming nothing isn't)."
        ),
        lambda d, m: _faithfulness(m),
    ),
    (
        "Tokens per assessment",
        "Input and output tokens, the mean over the cases that ran.",
        lambda d, m: ("n/a" if m.tokens is None else f"{m.tokens:.0f}") + _failed(m),
    ),
    (
        "Cost per assessment",
        (
            "US dollars, the mean over the cases that ran: OpenRouter's reported cost, else the "
            "model's listed price; a local model costs nothing. The judge's cost is not included."
        ),
        lambda d, m: m.cost_text() + _failed(m),
    ),
    (
        "Seconds per assessment",
        "Wall-clock seconds, the mean over the cases that ran.",
        lambda d, m: ("n/a" if m.seconds is None else f"{m.seconds:.1f} s") + _failed(m),
    ),
    (
        "Served models",
        (
            "The models OpenRouter named as serving the assessments' calls, with how many each "
            'served: a router\'s picks. "As requested" when the model asked for served them all; '
            '"not recorded" for a run saved before served models were (#185).'
        ),
        lambda d, m: _served_cell(d),
    ),
)


def _table(latest: dict[tuple[str, str], Run], cell: Cell) -> list[str]:
    models = sorted({model for model, _ in latest})
    profiles = sorted({profile for _, profile in latest})
    lines = [
        "| Model | " + " | ".join(profiles) + " |",
        "|---|" + "---|" * len(profiles),
    ]
    for model in models:
        cells = []
        for profile in profiles:
            if (model, profile) not in latest:
                cells.append(NOT_RUN)
                continue
            data = latest[(model, profile)][1]
            cells.append(cell(data, assessment_measures(data["cases"])) + _mark(data))
        lines.append(f"| {model} | " + " | ".join(cells) + " |")
    return lines


def _run_line(name: str, data: dict[str, Any]) -> str:
    judge = f", judged by {data['judge']}" if data.get("judge") else ""
    return (
        f"- `{name}`: {data['model']} on {data['profile']}{judge}, run {data['run_at']}, "
        f"{len(data['cases'])} cases, {data.get('status', 'status unknown')}"
    )


def render(runs: Sequence[Run]) -> str:
    """The report page for these runs: a table per measure, then the runs it was made from."""
    latest = _latest(runs)
    lines = [
        "# Eval report",
        "",
        (
            "Rendered from the saved raw results by `python -m vouch.evals report`, without "
            "calling a model. Each cell is the newest complete run of that model on that profile; "
            "a partial run or a dry run stands in, marked, only when there is no complete one."
        ),
    ]
    for title, explained, cell in MEASURES:
        lines += ["", f"## {title}", "", explained, "", *_table(latest, cell)]
    lines += ["", "## Runs", ""]
    lines += [_run_line(*run) for run in sorted(latest.values(), key=lambda r: r[0])]
    return "\n".join(lines) + "\n"


def keep_reading(page: str, existing: str | None) -> str:
    """The rendered page inside its tables block, with whatever an existing file has around
    that block (the reading) kept as it is. A new file, or one that is exactly this page with
    no markers (the renderer's own output, no reading), is written whole. Any other file whose
    block is missing or broken (both markers gone, one missing, doubled or out of order) raises
    ReportError and is left alone, so a reading is never lost."""
    block = f"{TABLES_START}\n{page}{TABLES_END}\n"
    if existing is None or existing == page:
        return block
    starts, ends = existing.count(TABLES_START), existing.count(TABLES_END)
    before, _, rest = existing.partition(TABLES_START)
    if starts != 1 or ends != 1 or TABLES_END not in rest:
        state = "has no tables markers" if starts == ends == 0 else "has a broken tables block"
        raise ReportError(
            f"the page {state}, so a re-render could overwrite its reading: restore one "
            f"{TABLES_START} line before the rendered tables and one {TABLES_END} line after "
            "them (git shows where they were), then render again"
        )
    after = rest.partition(TABLES_END)[2].removeprefix("\n")
    return before + block + after


def main(argv: list[str] | None = None) -> int:
    """Render the report to `--out` (written whole or not at all; a reading around its tables
    block kept, keep_reading), or print it. Exits 2 when there's nothing to render, or when
    `--out`'s tables block is broken."""
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
        if args.out is not None:
            existing = args.out.read_text(encoding="utf-8") if args.out.exists() else None
            page = keep_reading(page, existing)
    except ReportError as error:
        print(f"eval report: {error}", file=sys.stderr)
        return EXIT_BAD_INPUT
    if args.out is None:
        print(page, end="")
    else:
        write_atomic(args.out, page)
        print(f"Wrote {args.out}")
    return 0
