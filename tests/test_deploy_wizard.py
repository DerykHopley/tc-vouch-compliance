"""The deploy wizard, with fake gcloud, docker and curl that log each call: nothing reaches Google."""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "scripts" / "deploy-wizard.sh"
BASH = shutil.which("bash") or "/bin/bash"
URL = "https://vouch-123.europe-west1.run.app"
IMAGE = "europe-west1-docker.pkg.dev/vouch-demo-123/vouch/vouch"

# A fake for each outside tool: it logs its arguments, then answers like a signed-in gcloud would.
FAKE = """#!/bin/sh
echo "$(basename "$0") $*" >> "$CALLS"
case "$(basename "$0") $*" in
  "gcloud auth list"*) [ -n "$SIGNED_IN" ] && echo owner@example.com ;;
  "gcloud services list"*) printf 'run.googleapis.com\\nartifactregistry.googleapis.com\\n' ;;
  "gcloud run services describe"*) echo "$URL" ;;
  "curl"*) [ -n "$HEALTHY" ] || exit 22; echo '{"status": "ok", "version": "0.1.0"}' ;;
esac
exit 0
"""


def run_wizard(
    tmp_path: Path,
    *args: str,
    answers: str = "",
    signed_in: bool = True,
    healthy: bool = False,
    path: str | None = None,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in ("gcloud", "docker", "curl"):
        (bin_dir / tool).write_text(FAKE)
        (bin_dir / tool).chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin" if path is None else path,
        "HOME": str(tmp_path),
        "CALLS": str(tmp_path / "calls.log"),
        "URL": URL,
        "SIGNED_IN": "yes" if signed_in else "",
        "HEALTHY": "yes" if healthy else "",
        "WIZARD_ENV": str(tmp_path / "wizard.env"),
    }
    result = subprocess.run(
        [BASH, str(WIZARD), *args],
        check=False,
        input=answers,
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=60,
    )
    log = tmp_path / "calls.log"
    return result, log.read_text() if log.exists() else ""


def saved(tmp_path: Path, **answers: str):
    lines = [f"{key}={value}" for key, value in answers.items()]
    (tmp_path / "wizard.env").write_text("\n".join(lines) + "\n")


def test_the_script_parses():
    assert subprocess.run([BASH, "-n", str(WIZARD)], check=False).returncode == 0


def test_help_lists_the_steps(tmp_path):
    result, calls = run_wizard(tmp_path, "--help")
    assert result.returncode == 0
    for n, step in enumerate(
        (
            "Sign in",
            "Project",
            "Artifact Registry",
            "Build and push",
            "Deploy",
            "URL",
            "Health check",
        ),
        start=1,
    ):
        assert f"{n}. {step}" in result.stdout
    assert calls == ""


def test_an_unknown_option_is_refused_on_stderr(tmp_path):
    result, _ = run_wizard(tmp_path, "--deploy-now")
    assert result.returncode == 2
    assert "Unknown option" in result.stderr


def test_a_dry_run_prints_every_command_runs_none_and_writes_nothing(tmp_path):
    result, calls = run_wizard(tmp_path, "--dry-run", answers="vouch-demo-123\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ""
    assert not (tmp_path / "wizard.env").exists()
    for command in (
        "gcloud auth list --filter=status:ACTIVE --format=value\\(account\\)",
        "gcloud artifacts repositories create vouch",
        f"docker build -t {IMAGE}:v1 .",
        f"docker push {IMAGE}:v1",
        f"gcloud run deploy vouch --image={IMAGE}:v1 --region=europe-west1",
        "--min-instances=0 --max-instances=1",
        "--max=1",
        "--allow-unauthenticated",
        "/api/health",
    ):
        assert command in result.stdout
    assert "7/7 · Health check" in result.stdout


def test_a_dry_run_needs_no_gcloud_docker_or_curl(tmp_path):
    result, calls = run_wizard(tmp_path, "--dry-run", answers="vouch-demo-123\n", path="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker push {IMAGE}:v1" in result.stdout
    assert calls == ""


def test_a_deploy_saves_only_plain_answers_and_finishes(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + "y\n" * 10, healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"gcloud run deploy vouch --image={IMAGE}:v1" in calls
    assert "Done." in result.stdout
    assert (tmp_path / "wizard.env").read_text().splitlines() == [
        "PROJECT_ID=vouch-demo-123",
        "REGION=europe-west1",
        "TAG=v1",
    ]


def test_the_deploy_caps_the_service_not_just_the_revision_at_one_instance(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + "y\n" * 10, healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    [deploy] = [line for line in calls.splitlines() if line.startswith("gcloud run deploy ")]
    args = deploy.split()
    assert "--max=1" in args  # the service-level limit, shared by every revision with traffic
    assert "--max-instances=1" in args and "--min-instances=0" in args


def test_the_project_id_is_asked_once(tmp_path):
    run_wizard(tmp_path, answers="vouch-demo-123\n" + "y\n" * 10, healthy=True)
    result, _ = run_wizard(tmp_path, answers="y\n" * 10, healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Project id:" not in result.stdout
    assert f"gcloud run deploy vouch --image={IMAGE}:v2" in result.stdout


def test_a_skipped_build_deploys_the_last_pushed_image(tmp_path):
    saved(tmp_path, PROJECT_ID="vouch-demo-123", REGION="europe-west1", TAG="v3")
    # configure docker: yes; build: no; deploy: yes
    result, calls = run_wizard(tmp_path, answers="y\nn\ny\n", healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "\ndocker " not in "\n" + calls
    assert f"gcloud run deploy vouch --image={IMAGE}:v3" in calls
    assert "TAG=v3" in (tmp_path / "wizard.env").read_text()


def test_a_skipped_first_build_stops_before_the_deploy(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\ny\nn\n")
    assert result.returncode != 0
    assert "nothing to deploy" in result.stderr
    assert "gcloud run deploy" not in calls
    assert "TAG=" not in (tmp_path / "wizard.env").read_text()


def test_the_answers_file_is_git_ignored():
    ignored = ["git", "check-ignore", "-q", "deploy/cloudrun/wizard.env"]
    assert subprocess.run(ignored, check=False, cwd=ROOT).returncode == 0


def test_not_signed_in_stops_with_the_sign_in_command(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n", signed_in=False)
    assert result.returncode != 0
    assert "gcloud auth login" in result.stderr
    assert "\ndocker " not in "\n" + calls


def test_a_failed_health_check_stops_and_prints_the_logs_command(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + "y\n" * 10)
    assert result.returncode != 0
    assert "gcloud run deploy vouch" in calls
    assert f"{URL}/api/health" in result.stderr
    assert "gcloud logging read" in result.stderr
