"""The ticket-lint Action's script (#242), with a fake gh that keeps the issue's comments in a file.

On an opened or edited issue labelled ready-for-agent, the script checks it with the vendored
ticket linter and keeps one comment naming what's missing, updated on later edits; a clean ticket
gets none, or its comment updated to say so. Other issues are skipped."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ticket-lint-comment.py"

FAKE_GH = r"""#!/usr/bin/env python3
import json, os, sys
store = os.environ["COMMENTS"]
comments = json.load(open(store))
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as log:
    log.write(" ".join(args) + "\n")
if args[:2] == ["api", "--paginate"]:
    print(json.dumps(comments))
elif args[:3] == ["api", "-X", "POST"]:
    body = args[args.index("-f") + 1].split("=", 1)[1]
    comments.append({"id": len(comments) + 1, "body": body})
elif args[:3] == ["api", "-X", "PATCH"]:
    cid = int(args[3].rsplit("/", 1)[1])
    body = args[args.index("-f") + 1].split("=", 1)[1]
    for c in comments:
        if c["id"] == cid:
            c["body"] = body
else:
    sys.exit(f"fake gh: unexpected {args}")
json.dump(comments, open(store, "w"))
"""

GOOD = """## What happens now
It breaks.

## What should happen
It works.

## Done when
- [ ] It works. **Check:** a test.

## Needs
None

## Out of scope
Nothing else.
"""
NO_CHECKS = GOOD.replace(" **Check:** a test.", "")


@pytest.fixture
def action(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    comments, calls = tmp_path / "comments.json", tmp_path / "calls.log"
    comments.write_text("[]")
    calls.write_text("")

    def run(body: str, labels=("ready-for-agent", "bug")):
        event = tmp_path / "event.json"
        issue = {
            "number": 7,
            "body": body,
            "labels": [{"name": n} for n in labels],
            "created_at": "2026-11-06T10:00:00Z",
            "updated_at": "2026-11-06T10:00:00Z",
        }
        event.write_text(json.dumps({"action": "edited", "issue": issue}))
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_EVENT_PATH": str(event),
            "GH_REPO": "o/r",
            "COMMENTS": str(comments),
            "CALLS": str(calls),
        }
        done = subprocess.run(
            [shutil.which("python3") or sys.executable, "-I", str(SCRIPT)],
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr
        return json.loads(comments.read_text())

    return run


def test_a_ticket_missing_check_lines_gets_one_comment_naming_them(action):
    [comment] = action(NO_CHECKS)

    assert "lack a Check:" in comment["body"]


def test_editing_it_updates_the_same_comment(action):
    action(NO_CHECKS)
    action(NO_CHECKS.replace("## Needs\nNone\n", ""))

    [comment] = action(GOOD)

    assert "clean" in comment["body"].lower()
    assert "lack a Check:" not in comment["body"]


def test_a_second_edit_with_the_same_findings_keeps_one_comment(action):
    action(NO_CHECKS)

    comments = action(NO_CHECKS)

    assert len(comments) == 1


def test_a_clean_ticket_gets_no_comment(action):
    assert action(GOOD) == []


def test_an_issue_without_ready_for_agent_is_skipped(action):
    assert action(NO_CHECKS, labels=("needs-triage", "bug")) == []
