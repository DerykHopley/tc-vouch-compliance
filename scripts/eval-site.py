"""Build the eval report's data for GitHub Pages, from saved raw results, with no model call.

    python3 -I -B scripts/eval-site.py

Reads every run in `evals/kept/*.json` (the eval runner's raw results, `python -m vouch.evals
--keep`) and the full-context comparison in `evals/full-context/scores.json`, and writes
`docs/evals/data.js`, which `docs/evals/index.html` renders. Standard library only, so it runs
without the project's environment; the same results give the same file.
"""

import json
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEPT = ROOT / "evals" / "kept"
FULL = ROOT / "evals" / "full-context" / "scores.json"
OUT = ROOT / "docs" / "evals" / "data.js"

LABEL = {
    "anthropic/claude-haiku-5.5": "Haiku 5.5",
    "anthropic/claude-sonnet-5.5": "Sonnet 5.5",
    "ollama/gpt-oss:20b": "gpt-oss 20b (local)",
    "typesafe/jev-router": "Jev Router",
    "openai/gpt-6-luna": "GPT-6 Luna",
    "openai/gpt-6.1-terra": "GPT-6.1 Terra",
    "openai/gpt-6.1-sol": "GPT-6.1 Sol",
}
FULL_LABEL = {
    "opus": "Opus 5.5",
    "fable": "Fable 5.1",
    "sonnet": "Sonnet 5.5",
    "haiku": "Haiku 5.5",
}


def failure_kind(error: str) -> str:
    if "CallBudget" in error:
        return "Call budget spent"
    if "parsing tool call" in error:
        return "Tool call unreadable"
    if "audit result" in error:
        return "Claimed an audit result"
    return error.split(":")[0]


def mean(values: list[float], digits: int) -> float | None:
    return round(statistics.mean(values), digits) if values else None


def run_rows(path: Path, runs: list, cases: list, reqs: list) -> None:
    d = json.loads(path.read_text())
    model, profile = d["model"], d["profile"].capitalize()
    run, label = f"{model}|{profile}", LABEL.get(model, model)
    ok = [c for c in d["cases"] if not c.get("error")]
    judged = [
        c["faithfulness"]["score"]
        for c in ok
        if isinstance(c.get("faithfulness"), dict) and c["faithfulness"].get("score") is not None
    ]
    runs.append(
        {
            "run": run,
            "model": label,
            "model_id": model,
            "profile": profile,
            "judge": LABEL.get(d.get("judge", ""), d.get("judge", "")),
            "run_at": d["run_at"][:16].replace("T", " "),
            "verdict_accuracy": d["verdict_accuracy"],
            "verdicts": d["requirements_right"],
            "control_accuracy": d["control_accuracy"],
            "controls": d["controls_right"],
            "citation_precision": d["citation_precision"],
            "citations": d["citations_right"],
            "faithfulness": mean(judged, 3),
            "judged": len(judged),
            "tokens": mean([c["tokens"] for c in ok], 0),
            "cost": mean([c["cost_usd"] for c in ok], 4),
            "seconds": mean([c["seconds"] for c in ok], 1),
            "run_cost": round(
                sum((c.get("cost_usd") or 0) + (c.get("judge_cost_usd") or 0) for c in d["cases"]),
                4,
            ),
            "cases": len(d["cases"]),
            "failed": len(d["cases"]) - len(ok),
            "file": path.name,
        }
    )
    for c in d["cases"]:
        error = c.get("error") or ""
        rs = c.get("requirements") or []
        cases.append(
            {
                "run": run,
                "control": c["control_id"],
                "outcome": "failed"
                if error
                else ("right" if c.get("verdict") == c.get("expected_verdict") else "wrong"),
                "right": None
                if error
                else sum(1 for r in rs if r.get("verdict") == r.get("expected")),
                "total": len(rs) or None,
                "failure": failure_kind(error) if error else "",
                "error": error[:200],
            }
        )
        if not error:
            reqs.extend(
                {"run": run, "expected": r["expected"], "verdict": r["verdict"]} for r in rs
            )


def full_context_rows() -> list:
    rows = []
    for key, s in json.loads(FULL.read_text()).items():

        def frac(text: str) -> list[int]:
            right, total = re.match(r"(\d+)/(\d+)", text).groups()
            return [int(right), int(total)]

        rows.append(
            {
                "model": FULL_LABEL.get(key, key),
                "verdicts": frac(s["verdicts"]),
                "controls": frac(s["controls"]),
                "citations": frac(s["citations"]),
                "stricter": s["stricter"],
                "generous": s["generous"],
            }
        )
    return rows


def main() -> None:
    runs, cases, reqs = [], [], []
    for path in sorted(p for p in KEPT.glob("*.json")):
        run_rows(path, runs, cases, reqs)
    data = {"runs": runs, "cases": cases, "requirements": reqs, "full_context": full_context_rows()}
    OUT.write_text(
        "// Built by scripts/eval-site.py from evals/kept and evals/full-context; don't edit by hand.\n"
        f"window.EVAL = {json.dumps(data, ensure_ascii=False, separators=(',', ':'))};\n"
    )
    print(
        f"{OUT.relative_to(ROOT)}: {len(runs)} runs, {len(cases)} cases, {len(reqs)} requirement verdicts"
    )


if __name__ == "__main__":
    main()
