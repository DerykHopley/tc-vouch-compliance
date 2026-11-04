"""The deploy wizard, with fake gcloud, docker, curl and secret-tool that log each call.

Nothing reaches Google or the real keyring: the fake secret-tool reads planted values from a
folder, and the fake gcloud keeps its secrets in another, with what came on standard input.
"""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "scripts" / "deploy-wizard.sh"
BASH = shutil.which("bash") or "/bin/bash"
URL = "https://vouch-123.europe-west1.run.app"  # the deterministic URL, for project number 123
IMAGE = "europe-west1-docker.pkg.dev/vouch-demo-123/vouch/vouch"
SQL_INSTANCE = "vouch-demo-123:europe-west1:vouch-db"
DEFAULT_ACCOUNT = "123-compute@developer.gserviceaccount.com"
YES = "y\n" * 40

# Planted keyring values. Each is distinctive, so a leak anywhere is found.
PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAplantedJwtKeyLineOne\n"
    "plantedJwtKeyLineTwo+/==\n"
    "-----END PUBLIC KEY-----\n"
)
SECRETS = {  # keyring entry: (the secret's name in Secret Manager, which is its env var; value)
    "clerk-vouch-secret": ("CLERK_SECRET_KEY", "sk_test_plantedClerkSecret123"),
    "clerk-vouch-jwt-key": ("CLERK_JWT_KEY", PEM),
    "openrouter": ("OPENROUTER_API_KEY", "sk-or-v1-plantedOpenRouterKey456"),
    "cloudsql-vouch-run-url": (
        "VOUCH_DATABASE_URL",
        f"postgresql://vouch:plantedDbPassword789@/vouch?host=/cloudsql/{SQL_INSTANCE}",
    ),
}
PUBLISHABLE = "pk_test_publishableIsNotSecret"
ALL_APIS = (
    "run.googleapis.com artifactregistry.googleapis.com "
    "secretmanager.googleapis.com sqladmin.googleapis.com"
)

# A fake for each outside tool: it logs its arguments, then answers like a signed-in gcloud would.
# $FAIL makes the calls that start with it fail. Secrets live in $STATE: NAME.latest is the newest
# version's bytes, exactly as they came on standard input; NAME.versions has a line per version.
FAKE = r"""#!/bin/sh
tool=$(basename "$0")
echo "$tool $*" >> "$CALLS"
if [ -n "$FAIL" ]; then case "$tool $*" in "$FAIL"*) echo "ERROR: (fake) refused" >&2; exit 1 ;; esac; fi
case "$tool $*" in
  "secret-tool lookup service "*) [ -f "$KEYRING/$3" ] || exit 1; cat "$KEYRING/$3" ;;
  "gcloud auth list"*) [ -n "$SIGNED_IN" ] && echo owner@example.com ;;
  "gcloud services list"*) printf '%s\n' $APIS_ON ;;
  "gcloud projects describe"*"projectNumber"*) echo 123 ;;
  "gcloud sql instances describe"*) [ -n "$SQL" ] || exit 1; echo "$SQL" ;;
  "gcloud run services describe"*"serviceAccountName"*) echo "$SERVICE_ACCOUNT" ;;
  "gcloud run services describe"*) [ -n "$EXISTING" ] || [ -e "$STATE/deployed" ] || exit 1
    echo "$URL" ;;
  "gcloud run deploy"*) touch "$STATE/deployed" ;;
  "gcloud secrets describe"*) [ -e "$STATE/$3.created" ] || exit 1 ;;
  "gcloud secrets create"*) touch "$STATE/$3.created" ;;
  "gcloud secrets versions add"*) [ -e "$STATE/$4.created" ] || exit 1
    cat > "$STATE/$4.latest"; echo version >> "$STATE/$4.versions" ;;
  "gcloud secrets versions access"*) for a in "$@"; do case "$a" in --secret=*) name=${a#--secret=} ;; esac; done
    [ -f "$STATE/$name.latest" ] || exit 1; cat "$STATE/$name.latest" ;;
  "curl"*) [ -n "$HEALTHY" ] || exit 22
    echo '{"status": "ok", "version": "0.1.0", "database": "'"$DATABASE"'"}' ;;
esac
exit 0
"""


def plant(tmp_path: Path, missing: tuple[str, ...] = (), **changed: str) -> None:
    """The fake keyring: a file per entry, holding its value with nothing added."""
    keyring = tmp_path / "keyring"
    keyring.mkdir(exist_ok=True)
    values = {entry: value for entry, (_, value) in SECRETS.items()}
    values["clerk-vouch-publishable"] = PUBLISHABLE
    for entry, value in (values | changed).items():
        (keyring / entry).unlink(missing_ok=True)
        if entry not in missing:
            (keyring / entry).write_text(value)


def run_wizard(
    tmp_path: Path,
    *args: str,
    answers: str = "",
    signed_in: bool = True,
    healthy: bool = False,
    database: str = "reachable",
    path: str | None = None,
    existing: bool = True,
    url: str = URL,
    sql: str = SQL_INSTANCE,
    service_account: str = "",
    fail: str = "",
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    # Every tool is faked, secret-tool too: the real one would read the owner's keyring.
    for tool in ("gcloud", "docker", "curl", "secret-tool"):
        (bin_dir / tool).write_text(FAKE)
        (bin_dir / tool).chmod(0o755)
    (tmp_path / "state").mkdir(exist_ok=True)
    if not (tmp_path / "keyring").exists():
        plant(tmp_path)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin" if path is None else path,
        "HOME": str(tmp_path),
        "CALLS": str(tmp_path / "calls.log"),
        "KEYRING": str(tmp_path / "keyring"),
        "STATE": str(tmp_path / "state"),
        "URL": url,
        "SIGNED_IN": "yes" if signed_in else "",
        "HEALTHY": "yes" if healthy else "",
        "DATABASE": database,
        "EXISTING": "yes" if existing else "",
        "SQL": sql,
        "SERVICE_ACCOUNT": service_account,
        "APIS_ON": ALL_APIS,
        "FAIL": fail,
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


def calls_to(calls: str, start: str) -> list[list[str]]:
    """The recorded calls that start with `start`, each as its arguments."""
    return [line.split(" ") for line in calls.splitlines() if line.startswith(start)]


def deploy_args(calls: str) -> dict[str, str]:
    """The one `gcloud run deploy`'s --flag=value arguments, as a dict."""
    [deploy] = calls_to(calls, "gcloud run deploy ")
    return dict(arg.split("=", 1) for arg in deploy if arg.startswith("--") and "=" in arg)


def versions(tmp_path: Path, secret: str) -> int:
    path = tmp_path / "state" / f"{secret}.versions"
    return len(path.read_text().splitlines()) if path.exists() else 0


def test_the_script_parses():
    assert subprocess.run([BASH, "-n", str(WIZARD)], check=False).returncode == 0


def test_help_lists_the_steps(tmp_path):
    result, calls = run_wizard(tmp_path, "--help")
    assert result.returncode == 0
    for n, step in enumerate(
        (
            "Sign in",
            "Keyring",
            "Project",
            "Artifact Registry",
            "Build and push",
            "Secret Manager",
            "Access",
            "Database",
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
    assert calls == ""  # no gcloud, docker, curl or secret-tool call
    assert not (tmp_path / "wizard.env").exists()
    for command in (
        "gcloud auth list --filter=status:ACTIVE --format=value\\(account\\)",
        "gcloud artifacts repositories create vouch",
        f"docker build -t {IMAGE}:v1 .",
        f"docker push {IMAGE}:v1",
        "gcloud secrets create CLERK_JWT_KEY",
        (
            "secret-tool lookup service clerk-vouch-jwt-key | "
            "gcloud secrets versions add CLERK_JWT_KEY --data-file=-"
        ),
        "--role=roles/secretmanager.secretAccessor",
        "--role=roles/cloudsql.client",
        "vouch-db-setup",
        f"gcloud run deploy vouch --image={IMAGE}:v1 --region=europe-west1",
        "--min-instances=0 --max-instances=1",
        "--max=1",
        "--allow-unauthenticated",
        "--set-secrets=",
        "--add-cloudsql-instances=",
        "/api/health",
    ):
        assert command in result.stdout
    assert "11/11 · Health check" in result.stdout


def test_a_dry_run_needs_no_gcloud_docker_curl_or_secret_tool(tmp_path):
    result, calls = run_wizard(tmp_path, "--dry-run", answers="vouch-demo-123\n", path="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker push {IMAGE}:v1" in result.stdout
    assert calls == ""


def test_a_deploy_saves_only_plain_answers_and_finishes(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + YES, healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"gcloud run deploy vouch --image={IMAGE}:v1" in calls
    assert "Done." in result.stdout
    assert (tmp_path / "wizard.env").read_text().splitlines() == [
        "PROJECT_ID=vouch-demo-123",
        "REGION=europe-west1",
        "TAG=v1",
    ]


def test_secret_values_reach_gcloud_only_on_standard_input(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + YES, healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    seen = calls + result.stdout + result.stderr + (tmp_path / "wizard.env").read_text()
    assert "planted" not in seen  # every planted secret holds "planted"; not one shows anywhere
    for secret, value in SECRETS.values():
        # Byte for byte what the keyring holds: the PEM's lines and final newline survive the pipe.
        assert (tmp_path / "state" / f"{secret}.latest").read_text() == value
        add = f"gcloud secrets versions add {secret} --data-file=- --project=vouch-demo-123"
        assert calls_to(calls, f"gcloud secrets versions add {secret} ") == [add.split(" ")]


def test_a_missing_keyring_entry_stops_before_anything_changes(tmp_path):
    plant(tmp_path, missing=("clerk-vouch-jwt-key", "clerk-vouch-publishable"))
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + YES, healthy=True)
    assert result.returncode != 0
    assert "clerk-vouch-jwt-key" in result.stderr
    assert "clerk-vouch-publishable" in result.stderr
    assert "clerk-vouch-secret" not in result.stderr  # only what's missing is named
    gcloud = [" ".join(call) for call in calls_to(calls, "gcloud ")]
    assert gcloud == ["gcloud auth list --filter=status:ACTIVE --format=value(account)"]
    assert "\ndocker " not in "\n" + calls
    assert list((tmp_path / "state").iterdir()) == []
    assert not (tmp_path / "wizard.env").exists()


def test_the_deploy_carries_the_secrets_the_settings_the_instance_and_one_instance(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + YES, healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    args = deploy_args(calls)
    assert sorted(args["--set-secrets"].split(",")) == sorted(
        f"{secret}={secret}:latest" for secret, _ in SECRETS.values()
    )
    assert args["--set-env-vars"].split(",") == [
        f"CLERK_PUBLISHABLE_KEY={PUBLISHABLE}",
        f"CLERK_AUTHORIZED_PARTIES={URL}",
    ]
    assert args["--add-cloudsql-instances"] == SQL_INSTANCE
    assert args["--max-instances"] == "1" and args["--max"] == "1"
    assert args["--min-instances"] == "0"


def test_the_service_account_may_read_each_secret_and_connect_to_cloud_sql(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + YES, healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    member = f"--member=serviceAccount:{DEFAULT_ACCOUNT}"  # no account set: Compute's default
    granted = calls_to(calls, "gcloud secrets add-iam-policy-binding ")
    assert sorted(call[3] for call in granted) == sorted(s for s, _ in SECRETS.values())
    for call in granted:
        assert member in call and "--role=roles/secretmanager.secretAccessor" in call
    [sql] = calls_to(calls, "gcloud projects add-iam-policy-binding ")
    assert sql[3] == "vouch-demo-123"
    assert member in sql and "--role=roles/cloudsql.client" in sql and "--condition=None" in sql


def test_the_service_s_own_account_gets_the_roles_when_it_has_one(tmp_path):
    account = "vouch-run@vouch-demo-123.iam.gserviceaccount.com"
    answers = "vouch-demo-123\n" + YES
    result, calls = run_wizard(tmp_path, answers=answers, healthy=True, service_account=account)
    assert result.returncode == 0, result.stdout + result.stderr
    bindings = calls_to(calls, "gcloud secrets add-iam-policy-binding ")
    bindings += calls_to(calls, "gcloud projects add-iam-policy-binding ")
    assert len(bindings) == 5
    assert all(f"--member=serviceAccount:{account}" in call for call in bindings)


def test_a_rerun_adds_a_version_only_for_a_secret_that_changed(tmp_path):
    run_wizard(tmp_path, answers="vouch-demo-123\n" + YES, healthy=True)
    plant(tmp_path, openrouter="sk-or-v1-plantedRotatedKey000")
    result, _ = run_wizard(tmp_path, answers=YES, healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert versions(tmp_path, "OPENROUTER_API_KEY") == 2
    for secret in ("CLERK_SECRET_KEY", "CLERK_JWT_KEY", "VOUCH_DATABASE_URL"):
        assert versions(tmp_path, secret) == 1
    assert "planted" not in result.stdout + result.stderr


def test_the_deploy_waits_for_the_owner_to_confirm_the_database_setup(tmp_path):
    saved(tmp_path, PROJECT_ID="vouch-demo-123", REGION="europe-west1")
    # docker config, build, push; 4 creates and 4 versions; 5 grants: yes. vouch-db-setup ran: no.
    result, calls = run_wizard(tmp_path, answers="y\n" * 16 + "n\n" + YES)
    assert result.returncode != 0
    assert "vouch-db-setup" in result.stderr
    assert f"cloud-sql-proxy {SQL_INSTANCE}" in result.stdout  # how to run it
    assert "gcloud run deploy" not in calls


def test_a_missing_cloud_sql_instance_stops_before_the_deploy(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + YES, sql="")
    assert result.returncode != 0
    assert "vouch-db" in result.stderr
    assert "gcloud run deploy" not in calls


def test_a_failed_gcloud_step_stops_there_naming_the_step_and_the_rerun(tmp_path):
    answers = "vouch-demo-123\n" + YES
    result, calls = run_wizard(tmp_path, answers=answers, fail="gcloud secrets create")
    assert result.returncode != 0
    assert "Secret Manager" in result.stderr
    assert "scripts/deploy-wizard.sh again" in result.stderr
    assert "gcloud secrets versions add" not in calls
    assert "gcloud run deploy" not in calls


def test_a_first_deploy_accepts_sign_in_from_the_deterministic_url(tmp_path):
    answers = "vouch-demo-123\n" + YES
    result, calls = run_wizard(tmp_path, answers=answers, healthy=True, existing=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"CLERK_AUTHORIZED_PARTIES={URL}" in deploy_args(calls)["--set-env-vars"]


def test_a_service_url_other_than_the_one_set_says_to_run_again(tmp_path):
    hashed = "https://vouch-abc123xyz-ew.a.run.app"
    answers = "vouch-demo-123\n" + YES
    result, calls = run_wizard(tmp_path, answers=answers, healthy=True, existing=False, url=hashed)
    assert result.returncode != 0
    assert f"CLERK_AUTHORIZED_PARTIES={URL}" in deploy_args(calls)["--set-env-vars"]
    assert hashed in result.stderr
    assert "again" in result.stderr


def test_the_deploy_caps_the_service_not_just_the_revision_at_one_instance(tmp_path):
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + YES, healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    [deploy] = [line for line in calls.splitlines() if line.startswith("gcloud run deploy ")]
    args = deploy.split()
    assert "--max=1" in args  # the service-level limit, shared by every revision with traffic
    assert "--max-instances=1" in args and "--min-instances=0" in args


def test_the_project_id_is_asked_once(tmp_path):
    run_wizard(tmp_path, answers="vouch-demo-123\n" + YES, healthy=True)
    result, _ = run_wizard(tmp_path, answers=YES, healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Project id:" not in result.stdout
    assert f"gcloud run deploy vouch --image={IMAGE}:v2" in result.stdout


def test_a_skipped_build_deploys_the_last_pushed_image(tmp_path):
    saved(tmp_path, PROJECT_ID="vouch-demo-123", REGION="europe-west1", TAG="v3")
    # configure docker: yes; build: no; the rest: yes
    result, calls = run_wizard(tmp_path, answers="y\nn\n" + YES, healthy=True)
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
    result, calls = run_wizard(tmp_path, answers="vouch-demo-123\n" + YES)
    assert result.returncode != 0
    assert "gcloud run deploy vouch" in calls
    assert f"{URL}/api/health" in result.stderr
    assert "gcloud logging read" in result.stderr


def test_a_health_check_with_the_database_unavailable_stops_the_deploy(tmp_path):
    """From M3 the live app always has a database (#33): status ok alone isn't a working deploy."""
    answers = "vouch-demo-123\n" + YES
    result, _ = run_wizard(tmp_path, answers=answers, healthy=True, database="unavailable")
    assert result.returncode != 0
    assert "database" in result.stderr
    assert "gcloud logging read" in result.stderr
