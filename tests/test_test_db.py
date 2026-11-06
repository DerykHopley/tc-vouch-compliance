"""scripts/test-db.sh (#245): the Postgres tests locally, against a throwaway postgres:16 on a free
port (never 5432, the owner's Cloud SQL proxy), removed afterwards; with no arguments, the files
CI's database job runs, read from ci.yml, so the two can't drift.

With a fake container runtime and a fake `uv` that log what they're asked: no container starts."""

import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test-db.sh"
CI = ROOT / ".github" / "workflows" / "ci.yml"
BASH = shutil.which("bash") or "/bin/bash"

FAKE_RUNTIME = r"""#!/bin/sh
echo "runtime $*" >> "$CALLS"
case "$1" in
  run) echo fake-container-id ;;
  exec) exit 0 ;;
esac
"""
FAKE_UV = r"""#!/bin/sh
echo "uv $* DB=$VOUCH_TEST_DATABASE_URL" >> "$CALLS"
[ -z "$UV_OUTPUT" ] || printf '%s\n' "$UV_OUTPUT"
exit "${UV_EXIT:-0}"
"""


def run(
    tmp_path: Path, *args: str, container: str = "fakert", uv_exit: str = "0", uv_output: str = ""
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, text in (("fakert", FAKE_RUNTIME), ("uv", FAKE_UV)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "CONTAINER": container,
        "UV_EXIT": uv_exit,
        "UV_OUTPUT": uv_output,
    }
    done = subprocess.run(
        [BASH, str(SCRIPT), *args], check=False, capture_output=True, text=True, env=env, cwd=ROOT
    )
    return done, calls.read_text() if calls.exists() else ""


def ci_files() -> list[str]:
    """Every test file the database job's Postgres steps run, read as YAML (the script reads
    the job's text): each step that sets VOUCH_TEST_DATABASE_URL, in order."""
    steps = yaml.safe_load(CI.read_text())["jobs"]["database"]["steps"]
    runs = [s["run"] for s in steps if "VOUCH_TEST_DATABASE_URL" in s.get("env", {})]
    return [f for run in runs for f in re.findall(r"tests/\S+\.py", run)]


def published_port(calls: str) -> int:
    match = re.search(r"-p 127\.0\.0\.1:(\d+):5432", calls)
    assert match, calls
    return int(match.group(1))


def test_with_no_arguments_it_runs_ci_s_database_files_on_a_free_port_and_removes_the_container(
    tmp_path,
):
    done, calls = run(tmp_path)
    assert done.returncode == 0, done.stderr
    port = published_port(calls)
    assert port != 5432
    [pytest] = [line for line in calls.splitlines() if line.startswith("uv ")]
    assert "tests/test_database.py" in ci_files()  # the database job's own files, not another job's
    assert [a for a in pytest.split(" DB=")[0].split()[3:] if a.endswith(".py")] == ci_files()
    assert f"DB=postgresql://postgres:throwaway@127.0.0.1:{port}/postgres" in pytest
    assert re.search(r"runtime rm -f vouch-test-db-\d+", calls)  # removed afterwards


def test_arguments_go_to_pytest_and_its_exit_code_comes_back_with_the_container_removed(tmp_path):
    done, calls = run(tmp_path, "tests/test_database.py", "-k", "setup", uv_exit="3")
    assert done.returncode == 3
    assert "uv run pytest tests/test_database.py -k setup" in calls
    assert re.search(r"runtime rm -f vouch-test-db-\d+", calls)


@pytest.mark.enable_socket  # it holds 5432 itself, on the loopback only
def test_a_busy_5432_is_never_used(tmp_path):
    held = socket.socket()
    try:
        held.bind(("127.0.0.1", 5432))  # busy already (the owner's proxy) is the same test
    except OSError:
        held.close()
        held = None
    try:
        _, calls = run(tmp_path)
        assert published_port(calls) != 5432
    finally:
        if held is not None:
            held.close()


def test_without_a_container_runtime_it_stops_with_a_clear_message(tmp_path):
    done, calls = run(tmp_path, container="no-such-runtime")
    assert done.returncode != 0
    assert "no-such-runtime" in done.stderr and "docker or podman" in done.stderr
    assert "uv " not in calls


def test_with_no_arguments_a_skipped_test_fails_the_run_as_in_ci(tmp_path):
    done, calls = run(tmp_path, uv_output="SKIPPED [1] tests/test_database.py:12: no database")
    assert done.returncode != 0
    assert "skipped" in done.stderr.lower()
    assert re.search(r"runtime rm -f vouch-test-db-\d+", calls)
