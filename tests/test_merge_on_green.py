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
  "pr checks "*"--watch"*) [ -z "$ON_WATCH" ] || sh -c "$ON_WATCH"; exit 0 ;;
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


def gate(tmp_path: Path, work: Path, head: str, *args: str, base: str = "main", on_watch: str = ""):
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
        "ON_WATCH": on_watch,
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


def spec_branch(work: Path, name: str, file: str, text: str) -> None:
    """Another spec branch off main, changing `file`, pushed to the remote."""
    current = git(work, "rev-parse", "--abbrev-ref", "HEAD")
    git(work, "switch", "-q", "-c", name, "origin/main")
    commit(work, file, text, name)
    git(work, "push", "-q", "origin", name)
    git(work, "switch", "-q", current)


def test_a_spec_branch_deleted_on_the_remote_no_longer_blocks(tmp_path):
    work, head = repo(tmp_path, spec_line="spec says", fix_line="fix says")
    git(work, "fetch", "-q", "origin")  # the clone still knows origin/spec/x
    git(tmp_path / "remote.git", "branch", "-D", "spec/x")
    done, calls = gate(tmp_path, work, head)
    assert done.returncode == 0, done.stderr
    assert merged(calls)


def test_the_suggested_rerun_names_every_conflicting_spec_and_files_whole(tmp_path):
    work, head = repo(tmp_path, spec_line="spec says", fix_line="fix says")
    git(work, "switch", "-q", "fix")
    commit(work, "my file.txt", "fix\n", "a file with a space")
    git(work, "push", "-q", "origin", "fix")
    head = git(work, "rev-parse", "HEAD")
    spec_branch(work, "spec/m6/sub", "my file.txt", "spec\n")

    done, calls = gate(tmp_path, work, head, "--conflicts-ok", "spec/x")

    assert done.returncode != 0 and not merged(calls)
    assert "  my file.txt" in done.stderr  # one line, not split at the space
    assert "--conflicts-ok spec/m6/sub" in done.stderr and "--conflicts-ok spec/x" in done.stderr
    assert "no conflict with open specs" not in done.stdout


def test_a_spec_that_moves_during_the_ci_wait_is_caught_before_the_merge(tmp_path):
    work, head = repo(tmp_path, spec_line=None, fix_line="fix says")
    spec_branch(work, "spec/late", "b.txt", "unrelated\n")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", "-b", "spec/late", str(tmp_path / "remote.git"), str(other))
    move = (
        f"cd {other} && printf 'spec says\\ntwo\\n' > a.txt && git add a.txt"
        " && git -c user.name=t -c user.email=t@t commit -q -m late && git push -q origin spec/late"
    )

    done, calls = gate(tmp_path, work, head, on_watch=move)

    assert "no conflict with open specs" in done.stdout  # clean before the wait
    assert done.returncode != 0, done.stdout
    assert not merged(calls)
    assert "spec/late" in done.stderr and "a.txt" in done.stderr


def main_conflicts_with_spec_x(work: Path) -> None:
    """Main moves a.txt's first line its own way, so main alone already conflicts with spec/x."""
    current = git(work, "rev-parse", "--abbrev-ref", "HEAD")
    git(work, "switch", "-q", "main")
    commit(work, "a.txt", "main says\ntwo\n", "main moves")
    git(work, "push", "-q", "origin", "main")
    git(work, "switch", "-q", current)


def test_a_conflict_main_already_has_with_a_spec_warns_and_merges(tmp_path):
    work, head = repo(tmp_path, spec_line="spec says", fix_line="fix says", fix_file="b.txt")
    main_conflicts_with_spec_x(work)

    done, calls = gate(tmp_path, work, head)

    assert done.returncode == 0, done.stderr
    assert merged(calls)
    assert "main already conflicts with spec/x" in done.stdout
    assert "a.txt" in done.stdout
    assert "no new conflict with open specs" in done.stdout
    assert "no conflict with open specs" not in done.stdout


def test_a_conflict_the_pr_adds_is_refused_naming_only_its_file(tmp_path):
    work, head = repo(tmp_path, spec_line="spec says", fix_line="fix says", fix_file="b.txt")
    git(work, "switch", "-q", "spec/x")
    commit(work, "c.txt", "spec c\n", "spec adds c")
    git(work, "push", "-q", "origin", "spec/x")
    git(work, "switch", "-q", "fix")
    commit(work, "c.txt", "fix c\n", "fix adds c")
    git(work, "push", "-q", "origin", "fix")
    head = git(work, "rev-parse", "HEAD")
    main_conflicts_with_spec_x(work)

    done, calls = gate(tmp_path, work, head)

    assert done.returncode != 0
    assert not merged(calls)
    refusal = done.stderr.split("would make spec/x conflict in:", 1)[1]
    assert "c.txt" in refusal
    assert "a.txt" not in refusal


def test_a_new_conflict_inside_a_file_main_already_conflicts_in_is_refused(tmp_path):
    work, head = repo(tmp_path, spec_line=None, fix_line="fix says", fix_file="b.txt")
    ten = [f"line {n}" for n in range(1, 11)]
    git(work, "switch", "-q", "main")
    commit(work, "d.txt", "\n".join(ten) + "\n", "d")
    git(work, "push", "-q", "origin", "main")
    spec_branch(work, "spec/z", "d.txt", "\n".join(["spec one", *ten[1:9], "spec ten"]) + "\n")
    git(work, "switch", "-q", "fix")
    git(work, "merge", "-q", "origin/main")
    commit(work, "d.txt", "\n".join([*ten[:9], "fix ten"]) + "\n", "fix changes line ten")
    git(work, "push", "-q", "origin", "fix")
    head = git(work, "rev-parse", "HEAD")
    git(work, "switch", "-q", "main")
    commit(work, "d.txt", "\n".join(["main one", *ten[1:]]) + "\n", "main changes line one")
    git(work, "push", "-q", "origin", "main")

    done, calls = gate(tmp_path, work, head)

    assert done.returncode != 0, done.stdout
    assert not merged(calls)
    assert "main already conflicts with spec/z" in done.stdout
    assert "would make spec/z conflict in:\n  d.txt" in done.stderr
