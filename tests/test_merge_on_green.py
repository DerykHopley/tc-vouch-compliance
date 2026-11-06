"""The merge gate, with a fake gh and a real git remote (a local bare repo).

The fake gh answers the gate's questions about one PR (its branches, size and checks) and logs
`pr merge` instead of merging, so a test reads from the log whether the gate merged.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "merge-on-green.sh"
BASH = shutil.which("bash") or "/bin/bash"

FAKE_GH = r"""#!/bin/sh
echo "gh $*" >> "$CALLS"
case "$*" in
  "pr view"*baseRefName*) printf '%s\n%s\n%s\n' "$BASE" "$HEAD_BRANCH" "$HEAD_SHA" ;;
  "pr view"*files*) echo "3 notes.txt" ;;
  "pr checks"*--watch*) exit 0 ;;
  "pr checks"*) echo 1 ;;
  "pr merge"*) exit 0 ;;
  *) echo "fake gh: unexpected call: $*" >&2; exit 2 ;;
esac
"""

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(cwd, *args):
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit_file(repo, name, text, message):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def remote(tmp_path):
    """A bare origin with main, and a clone the gate runs in."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    git(work, "switch", "-q", "-c", "main")
    commit_file(work, "notes.txt", "one\ntwo\nthree\n", "base")
    commit_file(work, "other.txt", "x\n", "other")
    git(work, "push", "-q", "origin", "main")
    return work


def push_branch(work, branch, name, text, base="origin/main"):
    git(work, "switch", "-q", "-c", branch, base)
    commit_file(work, name, text, branch)
    git(work, "push", "-q", "origin", branch)
    sha = git(work, "rev-parse", "HEAD")
    git(work, "switch", "-q", "main")
    return sha


def run_gate(work, tmp_path, head_branch, head_sha, *args, base="main"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    calls = tmp_path / "calls.log"
    calls.write_text("")
    env = {
        **os.environ,
        **GIT_ENV,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "BASE": base,
        "HEAD_BRANCH": head_branch,
        "HEAD_SHA": head_sha,
    }
    for unset in ("GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):  # CI runners have no identity
        env.pop(unset)
    result = subprocess.run(
        [BASH, str(GATE), "7", *args],
        check=False,
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )
    return result, calls.read_text()


def test_a_pr_into_main_that_conflicts_with_an_open_spec_is_refused(remote, tmp_path):
    push_branch(remote, "spec/x", "notes.txt", "one\nSPEC\nthree\n")
    sha = push_branch(remote, "fix/a", "notes.txt", "one\nFIX\nthree\n")

    result, calls = run_gate(remote, tmp_path, "fix/a", sha)

    assert result.returncode != 0
    assert "spec/x" in result.stderr
    assert "notes.txt" in result.stderr
    assert "--conflicts-ok spec/x" in result.stderr
    assert "pr merge" not in calls


def test_conflicts_ok_names_the_spec_and_the_gate_merges(remote, tmp_path):
    push_branch(remote, "spec/x", "notes.txt", "one\nSPEC\nthree\n")
    sha = push_branch(remote, "fix/a", "notes.txt", "one\nFIX\nthree\n")

    result, calls = run_gate(remote, tmp_path, "fix/a", sha, "--conflicts-ok", "spec/x")

    assert result.returncode == 0, result.stderr
    assert f"pr merge 7 --merge --match-head-commit {sha}" in calls


def test_conflicts_ok_for_one_spec_does_not_cover_another(remote, tmp_path):
    push_branch(remote, "spec/x", "notes.txt", "one\nSPEC\nthree\n")
    push_branch(remote, "spec/y", "notes.txt", "one\nOTHER\nthree\n")
    sha = push_branch(remote, "fix/a", "notes.txt", "one\nFIX\nthree\n")

    result, calls = run_gate(remote, tmp_path, "fix/a", sha, "--conflicts-ok", "spec/x")

    assert result.returncode != 0
    assert "spec/y" in result.stderr
    assert "pr merge" not in calls


def test_a_pr_that_does_not_conflict_merges(remote, tmp_path):
    push_branch(remote, "spec/x", "notes.txt", "one\nSPEC\nthree\n")
    sha = push_branch(remote, "fix/a", "other.txt", "y\n")

    result, calls = run_gate(remote, tmp_path, "fix/a", sha)

    assert result.returncode == 0, result.stderr
    assert "no conflict with open specs" in result.stdout
    assert "allowed" not in result.stdout
    assert "pr merge 7" in calls


def test_with_no_spec_branch_the_gate_merges_as_before(remote, tmp_path):
    sha = push_branch(remote, "fix/a", "notes.txt", "one\nFIX\nthree\n")

    result, calls = run_gate(remote, tmp_path, "fix/a", sha)

    assert result.returncode == 0, result.stderr
    assert "pr merge 7" in calls


def test_a_landed_spec_branch_is_not_checked(remote, tmp_path):
    push_branch(remote, "spec/old", "notes.txt", "one\nOLD\nthree\n")
    git(remote, "merge", "-q", "--ff-only", "origin/spec/old")
    git(remote, "push", "-q", "origin", "main")
    sha = push_branch(remote, "fix/a", "notes.txt", "one\nFIX\nthree\n")

    result, calls = run_gate(remote, tmp_path, "fix/a", sha)

    assert result.returncode == 0, result.stderr
    assert "pr merge 7" in calls


def test_a_ticket_pr_into_a_spec_branch_is_not_checked_against_other_specs(remote, tmp_path):
    push_branch(remote, "spec/x", "notes.txt", "one\nSPEC\nthree\n")
    push_branch(remote, "spec/y", "other.txt", "base y\n")
    sha = push_branch(remote, "ticket/b", "notes.txt", "one\nTICKET\nthree\n", base="origin/spec/y")

    result, calls = run_gate(remote, tmp_path, "ticket/b", sha, base="spec/y")

    assert result.returncode == 0, result.stderr
    assert "pr merge 7" in calls


def test_a_pr_that_conflicts_with_main_itself_is_refused_with_its_own_message(remote, tmp_path):
    push_branch(remote, "spec/x", "other.txt", "spec\n")
    sha = push_branch(remote, "fix/a", "notes.txt", "one\nFIX\nthree\n")
    commit_file(remote, "notes.txt", "one\nMAIN\nthree\n", "main moves")
    git(remote, "push", "-q", "origin", "main")

    result, calls = run_gate(remote, tmp_path, "fix/a", sha)

    assert result.returncode != 0
    assert "conflicts with main itself" in result.stderr
    assert "pr merge" not in calls


def test_a_head_that_cannot_be_fetched_is_refused(remote, tmp_path):
    push_branch(remote, "spec/x", "other.txt", "spec\n")

    result, calls = run_gate(remote, tmp_path, "fix/gone", "0" * 40)

    assert result.returncode != 0
    assert "could not fetch" in result.stderr
    assert "pr merge" not in calls
