#!/usr/bin/env python3
"""Extract the trial's evidence from both arms' Claude Code session logs into summary.json.

Keeps only what docs/trial-results.md and the comparison page rely on: token counts,
session times, owner-message times, arm C's workflow step outcomes, and the verifiers'
reports. No conversation text is copied except the verifiers' findings.

Usage: python3 -I summarise.py   (reads ~/.claude/projects, writes summary.json beside this file)
"""
import collections
import json
import pathlib

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
ARMS = {
    # (log folder, queue start, end of the arm's final report), UTC
    "b": ("-var-home-Deryk-development-TuringCollege-sprint3-trail-b", "2026-11-03T08:52:41", "2026-11-03T09:21:21"),
    "c": ("-var-home-Deryk-development-TuringCollege-sprint3-trail-c", "2026-11-03T08:52:57", "2026-11-03T09:59:42"),
}


def events(path):
    for line in path.open(errors="ignore"):
        try:
            yield json.loads(line)
        except ValueError:
            continue


def last_text(path):
    text = ""
    for e in events(path):
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "assistant" and isinstance(content, list):
            for c in content:
                if c.get("type") == "text" and c["text"].strip():
                    text = c["text"]
    return text


def arm_summary(folder, start, end):
    root = PROJECTS / folder
    seen, kinds = set(), collections.defaultdict(collections.Counter)
    first = last = None
    owner_times = []
    for f in root.rglob("*.jsonl"):
        kind = "main" if f.parent == root else ("workflow" if "workflows" in f.parts else "subagent")
        for e in events(f):
            ts = e.get("timestamp") or ""
            m = e.get("message") or {}
            if ts and not (start <= ts <= end):
                continue
            if kind == "main":
                first = first or ts
                last = max(last or ts, ts)
                if e.get("type") == "user" and isinstance(m.get("content"), str) and not m["content"].startswith(("<", "Another Claude session")):
                    owner_times.append(ts)
            u = m.get("usage")
            if e.get("type") != "assistant" or not u:
                continue
            key = (m.get("id"), e.get("requestId"))
            if key in seen:
                continue
            seen.add(key)
            kinds[kind].update(
                input=u.get("input_tokens", 0),
                cache_write=u.get("cache_creation_input_tokens", 0),
                cache_read=u.get("cache_read_input_tokens", 0),
                output=u.get("output_tokens", 0),
                calls=1,
            )
    total = sum(kinds.values(), collections.Counter())
    return {
        "window": [start, end],
        "last_main_event": last,
        "owner_message_times": sorted(owner_times),
        "tokens": {k: dict(v) for k, v in kinds.items()} | {"total": dict(total)},
    }


def b_verifier_reports():
    root = PROJECTS / ARMS["b"][0]
    reports = []
    for meta in sorted(root.rglob("subagents/*.meta.json")):
        info = json.loads(meta.read_text())
        transcript = meta.with_name(meta.name.replace(".meta.json", ".jsonl"))
        if transcript.exists():
            reports.append({"agent": meta.name.split(".")[0], "meta": info, "final_report": last_text(transcript)})
    return reports


def c_workflow_runs():
    root = PROJECTS / ARMS["c"][0]
    runs = []
    for journal in sorted(root.rglob("subagents/workflows/*/journal.jsonl")):
        steps, label = [], None
        for e in events(journal):
            if e.get("type") == "started":
                label = e.get("label")
            elif e.get("type") == "result":
                steps.append({"label_hint": label, "result": e.get("result")})
        runs.append({"run": journal.parent.name, "agents": len(list(journal.parent.glob("agent-*.jsonl"))), "steps": steps})
    return runs


if __name__ == "__main__":
    summary = {
        "note": "Extracted from Claude Code session logs; see summarise.py. label_hint can be off by one when agents ran in parallel: match on the result's content.",
        "arms": {arm: arm_summary(*cfg) for arm, cfg in ARMS.items()},
        "b_subagent_reports": b_verifier_reports(),
        "c_workflow_runs": c_workflow_runs(),
    }
    out = pathlib.Path(__file__).with_name("summary.json")
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"wrote {out}")
