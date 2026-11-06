#!/usr/bin/env python3
"""The ticket-lint Action's step (#242): check the opened or edited issue with the vendored ticket
linter (scripts/ticket-lint.py) when it's labelled ready-for-agent, and keep one comment with its
findings, updated on later edits. A clean ticket gets no comment, or its comment updated to say so.

Env: GITHUB_EVENT_PATH (the issues event), GH_REPO (owner/name), and GH_TOKEN for gh.
Standard library only; gh does the API calls."""

import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

MARKER = "<!-- ticket-lint -->"
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


def findings(issue: dict) -> list[str]:
    """What the linter says this ticket breaks, one line each."""
    checked = {
        "number": issue["number"],
        "body": issue.get("body") or "",
        "labels": issue.get("labels", []),
        "createdAt": issue["created_at"],
        "updatedAt": issue["updated_at"],
        "comments": [],  # the event carries none; a brief in a comment is checked by hand
    }
    found = linter().check(checked, datetime.now(UTC), closed=set(), merged_prs=[])
    return [text for kind, text in found if kind in SHOWN]


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    issue = event["issue"]
    if READY not in {label["name"] for label in issue.get("labels", [])}:
        print(f"#{issue['number']} isn't {READY}: skipped")
        return 0
    repo, number = os.environ["GH_REPO"], issue["number"]
    found = findings(issue)
    comments = json.loads(gh("api", "--paginate", f"repos/{repo}/issues/{number}/comments"))
    mine = next((c for c in comments if c["body"].startswith(MARKER)), None)
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
