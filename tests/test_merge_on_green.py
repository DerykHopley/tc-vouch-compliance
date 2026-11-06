"""scripts/merge-on-green.sh (#235): before a PR into main merges, the gate checks the PR's
merge result against every open spec branch (`origin/spec/*`) with `git merge-tree`, and
refuses on a conflict unless `--conflicts-ok <branch>` names it. A PR into a spec branch isn't
checked against other specs.

Against a temporary bare remote and a clone of it, with a fake `gh` that answers from the
environment and logs each call: nothing reaches GitHub."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "merge-on-green.sh"
BASH = shutil.which("bash") or "/bin/bash"

FAKE_GH = r"""#!/bin/sh
echo "gh $*" >> "$CALLS"
case "$*" in
  "pr view "*"headRefOid"*) echo "$HEAD_SHA" ;;
  "pr view "*"baseRefName"*) echo "$BASE" ;;
  "pr view "*"files"*) echo "2 a.txt" ;;
  "pr checks "*"--watch"*) exit 0 ;;
  "pr checks "*"length"*) echo 1 ;;
  "pr checks "*) echo "ci pass" ;;
  "pr merge "*) exit 0 ;;
esac
"""


def git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env
    )
    return done.stdout.strip()


def commit(cwd: Path, name: str, text: str, message: str) -> None:
    (cwd / name).write_text(text)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", message)


def repo(tmp_path: Path, *, spec_line: str | None, fix_line: str, fix_file: str = "a.txt"):
    """A bare remote with main (a.txt, b.txt), optionally spec/x changing a.txt's first line,
    and a PR branch `fix` changing `fix_file`'s first line; a clone of it to run the gate in."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(work))
    commit(work, "a.txt", "one\ntwo\n", "start")
    commit(work, "b.txt", "one\ntwo\n", "b")
    git(work, "push", "-q", "origin", "HEAD:main")
    if spec_line is not None:
        git(work, "switch", "-q", "-c", "spec/x")
        commit(work, "a.txt", f"{spec_line}\ntwo\n", "spec")
        git(work, "push", "-q", "origin", "spec/x")
        git(work, "switch", "-q", "main")
    git(work, "switch", "-q", "-c", "fix")
    original = (work / fix_file).read_text().splitlines()[1]
    commit(work, fix_file, f"{fix_line}\n{original}\n", "fix")
    git(work, "push", "-q", "origin", "fix")
    return work, git(work, "rev-parse", "HEAD")


def gate(tmp_path: Path, work: Path, head: str, *args: str, base: str = "main"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    calls = tmp_path / "calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "HEAD_SHA": head,
        "BASE": base,
    }
    done = subprocess.run(
        [BASH, str(GATE), "7", *args],
        check=False,
        cwd=work,
        capture_output=True,
        text=True,
        env=env,
    )
    return done, calls.read_text() if calls.exists() else ""


def merged(calls: str) -> bool:
    return any(line.startswith("gh pr merge 7") for line in calls.splitlines())


def test_a_pr_into_main_that_would_make_a_spec_conflict_is_refused_naming_it(tmp_path):
    work, head = repo(tmp_path, spec_line="spec says", fix_line="fix says")
    done, calls = gate(tmp_path, work, head)
    assert done.returncode != 0
    assert not merged(calls)
    assert "spec/x" in done.stderr and "a.txt" in done.stderr
    assert "--conflicts-ok spec/x" in done.stderr
    assert "gh pr checks 7 --watch" not in calls  # refused before waiting for CI


def test_conflicts_ok_for_that_spec_merges(tmp_path):
    work, head = repo(tmp_path, spec_line="spec says", fix_line="fix says")
    done, calls = gate(tmp_path, work, head, "--conflicts-ok", "spec/x")
    assert done.returncode == 0, done.stderr
    assert merged(calls)
    assert "spec/x" in done.stdout


def test_no_conflict_with_an_open_spec_merges_and_says_so(tmp_path):
    work, head = repo(tmp_path, spec_line="spec says", fix_line="fix says", fix_file="b.txt")
    done, calls = gate(tmp_path, work, head)
    assert done.returncode == 0, done.stderr
    assert "no conflict with open specs" in done.stdout
    assert merged(calls)


def test_with_no_open_spec_it_merges_as_before(tmp_path):
    work, head = repo(tmp_path, spec_line=None, fix_line="fix says")
    done, calls = gate(tmp_path, work, head)
    assert done.returncode == 0, done.stderr
    assert merged(calls)


def test_a_pr_into_a_spec_branch_is_not_checked_against_other_specs(tmp_path):
    work, head = repo(tmp_path, spec_line="spec says", fix_line="fix says")
    done, calls = gate(tmp_path, work, head, base="spec/y")
    assert done.returncode == 0, done.stderr
    assert merged(calls)
    assert "open specs" not in done.stdout
