"""The deploy wizard (scripts/deploy-wizard.sh), driven from outside with fake gcloud, docker and curl.

The fakes record every call, so a dry run can prove it ran nothing; nothing reaches Google Cloud.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "scripts" / "deploy-wizard.sh"
URL = "https://vouch-123.europe-west1.run.app"

# A fake for each outside tool: it logs its arguments, then answers like a signed-in gcloud would.
FAKE = """#!/bin/sh
echo "$(basename "$0") $*" >> "$CALLS"
case "$(basename "$0") $*" in
  "gcloud auth list"*) [ -n "$SIGNED_IN" ] && echo owner@example.com ;;
  "gcloud services list"*) printf 'run.googleapis.com\\nartifactregistry.googleapis.com\\n' ;;
  "gcloud run services describe"*) echo "$URL" ;;
  "curl"*) exit 22 ;;
esac
exit 0
"""


def run_wizard(tmp_path: Path, *args: str, answers: str = "", signed_in: bool = True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in ("gcloud", "docker", "curl"):
        (bin_dir / tool).write_text(FAKE)
        (bin_dir / tool).chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CALLS": str(tmp_path / "calls.log"),
        "URL": URL,
        "SIGNED_IN": "yes" if signed_in else "",
        "WIZARD_ENV": str(tmp_path / "wizard.env"),
    }
    result = subprocess.run(
        ["bash", str(WIZARD), *args],
        check=False,
        input=answers,
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=60,
    )
    calls = (tmp_path / "calls.log").read_text() if (tmp_path / "calls.log").exists() else ""
    return result, calls


def test_the_script_parses():
    assert subprocess.run(["bash", "-n", str(WIZARD)], check=False).returncode == 0


def test_help_lists_the_steps(tmp_path):
    result, calls = run_wizard(tmp_path, "--help")
    assert result.returncode == 0
    for step in ("Sign in", "Artifact Registry", "Build and push", "Deploy", "URL", "Health check"):
        assert step in result.stdout
    assert calls == ""


def test_a_dry_run_prints_every_command_and_runs_none(tmp_path):
    result, calls = run_wizard(tmp_path, "--dry-run", answers="vouch-demo-123\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ""
    out = result.stdout
    image = "europe-west1-docker.pkg.dev/vouch-demo-123/vouch/vouch:v1"
    for command in (
        "gcloud auth list",
        "gcloud artifacts repositories create vouch",
        f"docker build -t {image} .",
        f"docker push {image}",
        f"gcloud run deploy vouch --image={image} --region=europe-west1",
        "--min-instances=0 --max-instances=1",
        "--allow-unauthenticated",
        "/api/health",
    ):
        assert command in out


def test_the_answers_file_holds_only_plain_answers(tmp_path):
    run_wizard(tmp_path, "--dry-run", answers="vouch-demo-123\n")
    keys = {line.split("=")[0] for line in (tmp_path / "wizard.env").read_text().splitlines()}
    assert keys <= {"PROJECT_ID", "REGION", "TAG"}
    assert "PROJECT_ID" in keys


def test_the_project_id_is_asked_once(tmp_path):
    run_wizard(tmp_path, "--dry-run", answers="vouch-demo-123\n")
    result, _ = run_wizard(tmp_path, "--dry-run", answers="")
    assert result.returncode == 0
    assert "vouch-demo-123" in result.stdout


def test_the_answers_file_is_git_ignored():
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "deploy/cloudrun/wizard.env"], check=False, cwd=ROOT
    )
    assert ignored.returncode == 0


def test_not_signed_in_stops_with_the_sign_in_command(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n", signed_in=False)
    assert result.returncode != 0
    assert "gcloud auth login" in result.stdout
    assert "docker" not in calls


def test_a_failed_health_check_stops_and_prints_the_logs_command(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + "y\n" * 20)
    assert result.returncode != 0
    assert "gcloud run deploy vouch" in calls
    assert URL in result.stdout
    assert "gcloud logging read" in result.stdout
