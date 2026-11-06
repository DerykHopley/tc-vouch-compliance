#!/usr/bin/env python3
# Vendored from the rig (~/development/rig/scripts/ticket-lint.py) at b024524 (2026-11-06) for the
# ticket-lint Action (#242): the rig lives outside this repo. Update by copying it again.
"""Check a repo's open GitHub issues against docs/ticket-standard.md, as a Markdown table.

Usage: ticket-lint.py [--repo owner/name] [--stale-days 21] [--json] [--against REF] [--issue N ...]
Run it from the project's checkout (or pass --repo). Reads issues and merged PRs through `gh`.
--against REF lists the code names a brief uses (`Type`, `func()`, `module.name`) that `git grep` can't find
at REF: each is either new, built by the ticket, or a sign the code moved since the ticket was written.
Exit 1 when a ready-for-agent, ready-for-human or waiting-for-agent ticket breaks the standard; 0 otherwise.
Standard library only. Only what a script can check: whether a criterion is really checkable is judgement.
"""
import argparse, json, re, subprocess, sys
from datetime import datetime, timezone

STATES = ["needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "waiting-for-agent", "wontfix"]
CATEGORIES = ["bug", "enhancement"]
READY = {"ready-for-agent", "ready-for-human", "waiting-for-agent"}

# Section name -> the headings that count as it (lower case, matched at the start).
SECTIONS = {
    "now": ["what happens", "current behavior", "current behaviour", "what happened"],
    "want": ["what should happen", "desired behavior", "desired behaviour", "what to build"],
    "done": ["done when", "acceptance criteria", "how to check it's done"],
    "needs": ["needs"],
    "out": ["out of scope", "not this issue"],
    "blocked": ["blocked by"],
    "why_human": ["why a human"],
    "finish": ["how to finish"],
}
REQUIRED = {
    "ready-for-agent": ["now", "want", "done", "needs", "out"],
    "ready-for-human": ["now", "want", "done", "needs", "out", "why_human", "finish"],
    "waiting-for-agent": ["now", "want", "done", "needs", "out", "why_human", "finish", "blocked"],
}
LABEL = {"now": "What happens now", "want": "What should happen", "done": "Done when", "needs": "Needs",
         "out": "Out of scope", "blocked": "Blocked by", "why_human": "Why a human", "finish": "How to finish"}

# A heading, a bold label on its own line, or a bullet starting with a bold label.
HEAD = re.compile(r"^\s*(?:#{1,4}\s+(?P<h>.+?)\s*:?\s*$|(?:[-*]\s+)?\*\*(?P<b>[^*]+?):?\*\*:?\s*(?P<rest>.*)$)")
CLOSES = r"(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)"


def gh(*args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"gh {' '.join(args)} failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def sections(text):
    """Split Markdown into {section: [lines]} for the sections the standard knows."""
    found, current = {}, None
    for line in (text or "").splitlines():
        m = HEAD.match(line)
        name = m and (m.group("h") or m.group("b")).strip().lower()
        key = name and next((k for k, names in SECTIONS.items() if any(name.startswith(n) for n in names)), None)
        if m and key:
            current = key
            found.setdefault(key, [])
            if m.group("rest"):
                found[key].append(m.group("rest"))
        elif m and m.group("h"):
            current = None  # an unknown heading ends the section; an unknown bold label inside one doesn't
        elif current:
            found[current].append(line)
    return found


NOTHING = {"", "none", "-", "n/a", "na", "nothing"}  # a Blocked by line that means "no blockers"


def contract(issue):
    """The text the standard applies to: the latest Agent Brief comment, else the body."""
    briefs = [c["body"] for c in issue.get("comments", []) if re.search(r"^#+\s*Agent Brief", c["body"], re.M)]
    return briefs[-1] if briefs else issue.get("body", "")


def is_spec(issue):
    return any(l["name"] == "spec" for l in issue.get("labels", []))


def check(issue, now, closed, merged_prs, stale_days=21, states=STATES, categories=CATEGORIES):
    """Findings for one issue: a list of (kind, text). Kinds: fail, labels, stale, check, note."""
    labels = [l["name"] for l in issue.get("labels", [])]
    state = [l for l in labels if l in states]
    cats = [l for l in labels if l in categories]
    out = []
    if len(state) != 1:
        out.append(("labels", f"{len(state)} state labels{': ' + ', '.join(state) if state else ''}"))
    if len(cats) != 1:
        out.append(("labels", f"{len(cats)} category labels{': ' + ', '.join(cats) if cats else ''}"))

    secs = sections(contract(issue))
    st = state[0] if len(state) == 1 else None
    missing = [LABEL[k] for k in REQUIRED.get(st, REQUIRED["ready-for-agent"]) if k not in secs]
    done = [l for l in secs.get("done", []) if l.strip()]
    boxes = [l for l in done if re.match(r"\s*[-*]\s+\[[ xX]\]", l)]
    unchecked = [l for l in boxes if not re.search(r"\bcheck:", l, re.I)]
    gaps = list(missing)
    if "done" in secs and not boxes:
        gaps.append("Done when has no checkboxes")
    elif unchecked:
        gaps.append(f"{len(unchecked)} of {len(boxes)} criteria lack a Check:")
    if missing:
        gaps[:len(missing)] = ["missing " + ", ".join(missing)]
    if st in READY and gaps:
        out.append(("fail", "; ".join(gaps)))
    elif gaps:
        bare = len(missing) == len(REQUIRED["ready-for-agent"])
        out.append(("note", "to be ready: " + ("no standard sections yet" if bare else "; ".join(gaps))))

    blocked_text = "\n".join(secs.get("blocked", []))
    blockers = [int(n) for n in re.findall(r"#(\d+)", blocked_text)]
    said = [l for l in (re.sub(r"^\s*[-*]?\s*", "", l).rstrip(" .").lower() for l in blocked_text.splitlines()) if l]
    if not blockers and any(l not in NOTHING for l in said):
        # "Blocked by: T1." reads as a blocker but counts as none; a queue would start it.
        out.append(("fail" if st in READY else "note", "Blocked by names no issue number (#N)"))
    if blockers and all(n in closed for n in blockers):
        nxt = "move to ready-for-human" if st == "waiting-for-agent" else "unblocked"
        out.append(("check", f"every blocker closed ({', '.join('#%d' % n for n in blockers)}): {nxt}"))

    age = (now - datetime.fromisoformat(issue["updatedAt"].replace("Z", "+00:00"))).days
    if age > stale_days:
        out.append(("stale", f"untouched {age} days"))

    n, created = issue["number"], issue["createdAt"]
    ref = re.compile(rf"(?<![\w/])#{n}\b|/issues/{n}\b")
    fixes = re.compile(rf"{CLOSES}:?\s+(?:#{n}\b|\S*/issues/{n}\b)", re.I)
    after = [p for p in merged_prs if p["mergedAt"] > created]
    says_fixes = [p["number"] for p in after if fixes.search(f"{p['title']}\n{p['body']}")]
    mentions = [p["number"] for p in after if p["number"] not in says_fixes and ref.search(f"{p['title']}\n{p['body']}")]
    if says_fixes:
        out.append(("check", "merged PR says it fixes this: " + ", ".join(f"#{p}" for p in says_fixes)))
    if mentions:
        out.append(("note", "mentioned by merged PR " + ", ".join(f"#{p}" for p in mentions[:5])))
    return out


# A backticked code name: `Type`, `snake_name`, `obj.attr`, `func()`. Not a path, a header, a command or a plain word.
CODE = re.compile(r"`([A-Za-z_][\w.]*?)(\(\))?`")


def code_names(text):
    """Code names in the brief's What happens now and What should happen, last dotted part, in order."""
    secs = sections(text)
    names = []
    for line in secs.get("now", []) + secs.get("want", []):
        for m in CODE.finditer(line):
            name, call = m.group(1), m.group(2)
            if not (call or "." in name or "_" in name or re.search(r"[A-Z]", name)):
                continue
            last = name.rstrip(".").rsplit(".", 1)[-1]
            if last and last not in names:
                names.append(last)
    return names


def names_finding(issue, exists, ref):
    """("names", text) for the brief's code names `exists` can't find at ref, else None."""
    missing = [n for n in code_names(contract(issue)) if not exists(n)]
    if missing:
        return ("names", f"not in {ref} (new, or the code moved?): " + ", ".join(f"`{n}`" for n in missing))
    return None


# Code only: a name the docs mention (a plan, a decision) but no code defines is still missing.
NOT_CODE = [":!*.md", ":!*.lock", ":!*-lock.json", ":!*.txt"]


def git_has(ref):
    return lambda name: subprocess.run(["git", "grep", "-q", "-w", "-F", name, ref, "--", *NOT_CODE],
                                       capture_output=True).returncode == 0


ORDER = ["fail", "labels", "names", "check", "stale", "note"]
VERDICT = {"fail": "breaks standard", "labels": "fix labels", "names": "check names", "check": "check: maybe done",
           "stale": "stale", "note": "ok"}


def verdict(findings):
    kinds = [k for k, _ in findings]
    return next((VERDICT[k] for k in ORDER if k in kinds), "ok")


def table(rows):
    lines = ["| # | State | Verdict | Findings | Title |", "|---|---|---|---|---|"]
    for r in rows:
        f = "<br>".join(f"{k}: {t}" for k, t in r["findings"]) or "–"
        lines.append(f"| #{r['number']} | {r['state']} | {r['verdict']} | {f} | {r['title'].replace('|', '/')} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", help="owner/name; default: the current checkout's repo")
    ap.add_argument("--stale-days", type=int, default=21)
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    ap.add_argument("--against", metavar="REF", help="list the brief's code names missing at this git ref")
    ap.add_argument("--issue", type=int, action="append", help="only these issue numbers (repeatable)")
    a = ap.parse_args()
    repo = ["--repo", a.repo] if a.repo else []

    issues = gh("issue", "list", *repo, "--state", "open", "--limit", "500",
                "--json", "number,title,body,labels,comments,createdAt,updatedAt")
    closed = {i["number"] for i in gh("issue", "list", *repo, "--state", "closed", "--limit", "2000", "--json", "number")}
    merged = gh("pr", "list", *repo, "--state", "merged", "--limit", "500", "--json", "number,title,body,mergedAt")
    now = datetime.now(timezone.utc)

    rows = []
    # A spec issue (label `spec`) is the parent of tickets, not a ticket: /to-spec labels it ready-for-agent.
    exists = git_has(a.against) if a.against else None
    for i in sorted((i for i in issues if not is_spec(i)), key=lambda i: i["number"]):
        if a.issue and i["number"] not in a.issue:
            continue
        f = check(i, now, closed, merged, a.stale_days)
        names = exists and names_finding(i, exists, a.against)
        if names:
            f.append(names)
        state = next((l["name"] for l in i["labels"] if l["name"] in STATES), "none")
        rows.append({"number": i["number"], "title": i["title"], "state": state, "verdict": verdict(f), "findings": f})
    rows.sort(key=lambda r: (list(VERDICT.values()).index(r["verdict"]), r["number"]))

    if a.json:
        print(json.dumps(rows, indent=2))
    else:
        counts = {}
        for r in rows:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        print(f"{len(rows)} open tickets: " + ", ".join(f"{n} {v}" for v, n in counts.items()) + "\n")
        print(table(rows))
    sys.exit(1 if any(r["state"] in READY and r["verdict"] == "breaks standard" for r in rows) else 0)


if __name__ == "__main__":
    main()
