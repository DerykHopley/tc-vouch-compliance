"""scripts/main-is-red.sh (#241): CI's last job, on a red run on main, opens one Fix ticket
("Main is red: <jobs>") with the run link, the commit and the failing test ids, or comments on
the one already open, so a red streak makes one issue.

With a fake `gh` that answers from the environment and logs each call and each body it is sent:
nothing reaches GitHub."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "main-is-red.sh"
BASH = shutil.which("bash") or "/bin/bash"

FAKE_GH = r"""#!/bin/sh
echo "gh $*" >> "$CALLS"
case "$*" in
  "api repos/o/r/actions/runs/42/jobs"*) printf '%b' "$FAILED_JOBS" ;;
  "api --allow-escape-sequences repos/o/r/actions/jobs/"*"/logs"*) id=$(echo "$*" | sed 's|.*jobs/\([0-9]*\)/logs.*|\1|')
    eval "printf '%b' \"\$LOG_$id\"" ;;
  "issue list"*) printf '%s' "$EXISTING" ;;
  "issue create"*) cat > "$STATE/created"; echo "https://github.com/o/r/issues/99" ;;
  "issue comment"*) cat > "$STATE/commented" ;;
esac
"""

LOG_FIREFOX = (
    "2026-11-06T10:00:00Z FAILED test_remediation.py::test_discard[firefox] - AssertionError\n"
    "2026-11-06T10:00:01Z FAILED test_remediation.py::test_discard[firefox] - AssertionError\n"
    "2026-11-06T10:00:02Z 1 failed, 81 passed\n"
)
LOG_FAST = "FAILED tests/test_api.py::test_health - assert 500 == 200\n"


def run(tmp_path: Path, *, failed_jobs: str, existing: str = "", **logs: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    calls = tmp_path / "calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "STATE": str(state),
        "FAILED_JOBS": failed_jobs,
        "EXISTING": existing,
        "REPO": "o/r",
        "RUN_ID": "42",
        "RUN_URL": "https://github.com/o/r/actions/runs/42",
        "SHA": "40ed6270000000000000000000000000000000aa",
        **{f"LOG_{job}": text for job, text in logs.items()},
    }
    done = subprocess.run([BASH, str(SCRIPT)], check=False, capture_output=True, text=True, env=env)
    read = lambda name: (state / name).read_text() if (state / name).exists() else None
    return done, calls.read_text() if calls.exists() else "", read("created"), read("commented")


def test_a_red_run_opens_one_fix_naming_the_jobs_run_commit_and_failing_tests(tmp_path):
    done, calls, created, commented = run(
        tmp_path, failed_jobs="11\\tBrowser check (firefox)\\n", **{"11": LOG_FIREFOX}
    )
    assert done.returncode == 0, done.stderr
    [create] = [line for line in calls.splitlines() if line.startswith("gh issue create")]
    assert "--title Main is red: Browser check (firefox)" in create
    assert "--label bug" in create and "--label ready-for-agent" in create
    assert commented is None
    assert "https://github.com/o/r/actions/runs/42" in created
    assert "40ed627" in created
    # each failing test once, though its log names it twice
    assert created.count("test_remediation.py::test_discard[firefox]") == 1
    for heading in (
        "## Parent",
        "## What happens now",
        "## What should happen",
        "## Done when",
        "## Needs",
        "## Out of scope",
    ):
        assert heading in created  # ticket-lint's headings


def test_a_second_red_run_comments_on_the_open_issue_and_opens_none(tmp_path):
    done, calls, created, commented = run(
        tmp_path,
        failed_jobs="11\\tBrowser check (firefox)\\n",
        existing="87",
        **{"11": LOG_FIREFOX},
    )
    assert done.returncode == 0, done.stderr
    assert "gh issue create" not in calls
    assert "gh issue comment 87" in calls
    assert created is None
    assert "https://github.com/o/r/actions/runs/42" in commented
    assert "test_remediation.py::test_discard[firefox]" in commented


def test_several_failed_jobs_are_all_named_with_their_tests(tmp_path):
    done, calls, created, _ = run(
        tmp_path,
        failed_jobs="11\\tBrowser check (firefox)\\n12\\tfast-lane\\n",
        **{"11": LOG_FIREFOX, "12": LOG_FAST},
    )
    assert done.returncode == 0, done.stderr
    assert "--title Main is red: Browser check (firefox), fast-lane" in calls
    assert "tests/test_api.py::test_health" in created
    assert "test_remediation.py::test_discard[firefox]" in created


def test_with_no_failed_job_found_it_still_opens_one_naming_the_run(tmp_path):
    # A job that was cancelled or timed out has no FAILED lines: the run link still goes in.
    done, calls, created, _ = run(tmp_path, failed_jobs="")
    assert done.returncode == 0, done.stderr
    assert "--title Main is red: the run" in calls
    assert "https://github.com/o/r/actions/runs/42" in created
