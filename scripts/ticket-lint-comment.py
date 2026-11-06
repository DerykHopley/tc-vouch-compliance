#!/usr/bin/env python3
"""The ticket-lint Action's step (#242): check the opened or edited issue with the vendored ticket
linter (scripts/ticket-lint.py) when it's labelled ready-for-agent, and keep one comment with its
findings, updated on later edits. A clean ticket gets no comment, or its comment updated to say so.

Env: GITHUB_EVENT_PATH (the issues event), GH_REPO (owner/name), and GH_TOKEN for gh.
Standard library only; gh does the API calls."""

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

MARKER = "<!-- ticket-lint -->"
BOT = "github-actions[bot]"  # the Action's own comments: a person quoting the marker isn't one
READY = "ready-for-agent"
SHOWN = {"fail", "labels"}  # what the standard requires; notes and staleness aren't for this


def linter():
    path = Path(__file__).with_name("ticket-lint.py")
    spec = importlib.util.spec_from_file_location("ticket_lint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def findings(issue: dict, comments: list[dict]) -> list[str]:
    """What the linter says this ticket breaks, one line each. `comments` count as the CLI's do:
    the latest "Agent Brief" comment is the brief, when there is one."""
    checked = {
        "number": issue["number"],
        "body": issue.get("body") or "",
        "labels": issue.get("labels", []),
        "createdAt": issue["created_at"],
        "updatedAt": issue["updated_at"],
        "comments": [{"body": c["body"]} for c in comments],
    }
    lint = linter()
    found = [
        text for kind, text in lint.check(checked, datetime.now(UTC), set(), []) if kind in SHOWN
    ]
    # Name each criterion that lacks its Check line, not only how many do.
    done = lint.sections(lint.contract(checked)).get("done", [])
    unchecked = [
        line.strip()
        for line in done
        if re.match(r"\s*[-*]\s+\[[ xX]\]", line)
        and not re.search(r"\bcheck:", line, re.IGNORECASE)
    ]
    return found + [f"no **Check:** in: {line}" for line in unchecked]


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    issue = event["issue"]
    if READY not in {label["name"] for label in issue.get("labels", [])}:
        print(f"#{issue['number']} isn't {READY}: skipped")
        return 0
    if linter().is_spec(issue):  # a spec issue isn't a ticket: the CLI skips it too
        print(f"#{issue['number']} is a spec: skipped")
        return 0
    repo, number = os.environ["GH_REPO"], issue["number"]
    listed = gh(
        "api",
        "--paginate",
        f"repos/{repo}/issues/{number}/comments?per_page=100",
        "--jq",
        ".[] | {id, body, login: .user.login}",
    )
    comments = [json.loads(line) for line in listed.splitlines() if line.strip()]
    mine = next((c for c in comments if c["login"] == BOT and c["body"].startswith(MARKER)), None)
    found = findings(issue, [c for c in comments if c is not mine])
    if found:
        lines = "\n".join(f"- {text}" for text in found)
        body = f"{MARKER}\n**Ticket lint:** this ticket doesn't meet the standard yet:\n{lines}"
    elif mine:
        body = f"{MARKER}\n**Ticket lint:** clean."
    else:
        print(f"#{number}: clean")
        return 0
    if mine:
        gh("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{mine['id']}", "-f", f"body={body}")
    else:
        gh("api", "-X", "POST", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}")
    print(f"#{number}: " + ("; ".join(found) or "clean"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
