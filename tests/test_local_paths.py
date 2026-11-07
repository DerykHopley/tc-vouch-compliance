"""Machine-local paths are caught before they're published (#340): in an issue body by the
ticket-lint Action's script, and in a pushed commit by the pre-push hook (trial/ exempt).

Sample paths are built from pieces, so this file never holds one itself (the hook would refuse
it)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "local-paths.py"

HOME = "/" + "home/alice/notes.txt"
VAR_HOME = "/var/" + "home/alice/shots/a.png"
MAC = "/" + "Users/alice/Desktop/x.png"
TMP = "/tmp/" + "claude-1000/-var-home-alice/scratchpad/shots/a.png"


def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


local_paths = module("local_paths", CHECK)


@pytest.mark.parametrize("path", [HOME, VAR_HOME, MAC, TMP])
def test_each_kind_of_local_path_is_found(path):
    assert local_paths.local_paths_in(f"See {path} for the screenshot.") == [path]


@pytest.mark.parametrize(
    "text",
    [
        "Run scripts/dev.sh from the repo root.",
        "A placeholder such as /home/<name>/ or ~/development/rig names no one.",
        "The file A-480-handle-focused-dark.png, by its name.",
        "https://example.com/home/page and /tmp/other-tool/x",
    ],
)
def test_text_without_a_local_path_has_none(text):
    assert local_paths.local_paths_in(text) == []


def diff(*files: tuple[str, str]) -> str:
    """A unified diff adding one line to each (file, line)."""
    return "".join(
        f"diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n@@ -1,0 +2,1 @@\n+{line}\n"
        for f, line in files
    )


def test_an_added_line_with_a_local_path_is_named_with_its_file_and_line():
    found = local_paths.in_added_lines(diff(("docs/notes.md", f"shot at {VAR_HOME}")))

    assert found == [("docs/notes.md", 2, VAR_HOME)]


def test_trial_is_exempt():
    assert local_paths.in_added_lines(diff(("trial/log.md", f"ran in {HOME}"))) == []


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def push_check(repo: Path, base: str) -> subprocess.CompletedProcess:
    """The check as the pre-push hook runs it: git's ref lines on stdin."""
    head = git(repo, "rev-parse", "HEAD")
    refs = f"refs/heads/fix {head} refs/heads/fix {base}\n"
    return subprocess.run(
        [sys.executable, "-I", str(CHECK), "--pre-push"],
        cwd=repo,
        input=refs,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("start\n")
    git(tmp_path, "add", "README.md")
    git(tmp_path, "commit", "-q", "-m", "start")
    return tmp_path


def commit(repo: Path, name: str, text: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)


def test_the_push_check_refuses_a_commit_adding_a_local_path_and_names_it(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "docs/notes.md", f"one\nshot at {HOME}\n")

    done = push_check(repo, base)

    assert done.returncode == 1
    assert "docs/notes.md:2" in done.stderr
    assert "file name" in done.stderr


def test_the_push_check_allows_one_under_trial_and_a_clean_one(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "trial/log.md", f"ran in {HOME}\n")
    commit(repo, "docs/notes.md", "the shot is A-480.png\n")

    assert push_check(repo, base).returncode == 0


def test_a_new_branch_is_checked_against_main(repo):
    git(repo, "switch", "-q", "-c", "fix")
    commit(repo, "docs/notes.md", f"{TMP}\n")

    done = push_check(repo, "0" * 40)  # a new branch on the remote: no remote sha yet

    assert done.returncode == 1
    assert "docs/notes.md:1" in done.stderr


# The ticket-lint Action's script (#242): a local path in a ready-for-agent issue is a finding.
lint_comment = module("ticket_lint_comment", ROOT / "scripts" / "ticket-lint-comment.py")
CLEAN = """## What happens now
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


def issue(body: str) -> dict:
    labels = [{"name": "ready-for-agent"}, {"name": "bug"}]
    stamp = "2026-11-07T10:00:00Z"
    return {"number": 7, "body": body, "labels": labels, "created_at": stamp, "updated_at": stamp}


def test_a_local_path_in_an_issue_body_is_a_ticket_lint_finding_naming_it():
    found = lint_comment.findings(issue(CLEAN + f"\nScreenshot: {TMP}\n"), [])

    assert any(TMP in line for line in found), found


def test_a_body_with_no_local_path_passes_as_before():
    assert lint_comment.findings(issue(CLEAN), []) == []


def test_the_finding_survives_json_round_trip():
    json.dumps(lint_comment.findings(issue(CLEAN + VAR_HOME), []))


@pytest.mark.parametrize(
    "text",
    [
        "https://x.com/" + "home/page/a",  # a URL's own /home/
        "frontend/src/pages/" + "home/parts/x.tsx",  # a repo path with a home folder in it
    ],
)
def test_home_inside_a_longer_path_or_url_is_not_a_local_path(text):
    assert local_paths.local_paths_in(text) == []


def test_a_trailing_full_stop_is_not_part_of_the_path():
    assert local_paths.local_paths_in(f"It is at {HOME}.") == [HOME]


def test_an_added_line_starting_with_plus_plus_is_a_line_not_a_header():
    tricky = "diff --git a/notes.md b/notes.md\n--- a/notes.md\n+++ b/notes.md\n@@ -0,0 +1,2 @@\n"
    tricky += "+++ a heading of pluses\n" + f"+{HOME}\n"

    assert local_paths.in_added_lines(tricky) == [("notes.md", 2, HOME)]


def test_a_remote_commit_not_here_is_checked_from_main_not_a_crash(repo):
    git(repo, "switch", "-q", "-c", "fix")
    commit(repo, "docs/notes.md", f"{HOME}\n")

    done = push_check(repo, "1" * 40)  # the remote's commit, never fetched here

    assert done.returncode == 1, done.stderr
    assert "docs/notes.md:1" in done.stderr
    assert "Traceback" not in done.stderr
