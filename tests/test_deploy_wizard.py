"""The deploy wizard, with fake gcloud, docker, curl and secret-tool that log each call.

Nothing reaches Google or the real keyring: the fake secret-tool reads planted values from a
folder, and the fake gcloud keeps its secrets' versions in another, as they came on standard input.
The tests answer the wizard's prompts by their text, as the owner would, not by counting them.
"""

import os
import select
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "scripts" / "deploy-wizard.sh"
BASH = shutil.which("bash") or "/bin/bash"
URL = "https://vouch-123.europe-west1.run.app"  # the deterministic URL, for project number 123
IMAGE = "europe-west1-docker.pkg.dev/vouch-demo-123/vouch/vouch"
SQL_INSTANCE = "vouch-demo-123:europe-west1:vouch-db"
DEFAULT_ACCOUNT = "123-compute@developer.gserviceaccount.com"
DB_READY = "--db-ready"

# Planted keyring values. Every secret holds "planted", so a leak anywhere is found.
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
PUBLISHABLE = "pk_test_cHVibGlzaGFibGUuY2xlcmsuYWNjb3VudHMuZGV2JA"
ALL_APIS = (
    "run.googleapis.com artifactregistry.googleapis.com "
    "secretmanager.googleapis.com sqladmin.googleapis.com"
)

# A fake for each outside tool: it logs its arguments, then answers like a signed-in gcloud would.
# $FAIL makes the calls that start with it fail. $FAIL_LOOKUP=entry:N makes the Nth lookup of that
# entry print part of its value and fail. Secrets live in $STATE: NAME.vN holds version N's bytes
# exactly as they came on standard input, NAME.vN.disabled marks it disabled. The registry's tags
# are $TAGS plus each tag `docker push` sends (in $STATE/tags); $TAGS_ERROR makes listing them fail.
FAKE = r"""#!/bin/sh
tool=$(basename "$0")
echo "$tool $*" >> "$CALLS"
case "$tool $*" in "gcloud secrets"*) echo "${CLOUDSDK_CORE_LOG_HTTP-unset} ${CLOUDSDK_CORE_VERBOSITY-unset}" >> "$STATE/log_http" ;; esac
if [ -n "$FAIL" ]; then case "$tool $*" in "$FAIL"*) echo "ERROR: (fake) PERMISSION_DENIED" >&2; exit 1 ;; esac; fi
secret_flag() { for a in "$@"; do case "$a" in --secret=*) echo "${a#--secret=}" ;; esac; done; }
not_found() { echo "ERROR: (gcloud.secrets) NOT_FOUND: Secret [$1] not found or has no versions." >&2; exit 1; }
newest() { n=0; while [ -e "$STATE/$1.v$((n + 1))" ]; do n=$((n + 1)); done; echo "$n"; }
case "$tool $*" in
  "secret-tool lookup service "*)
    count="$CALLS.lookups.$3"; n=$(( $(cat "$count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$count"
    [ -f "$KEYRING/$3" ] || exit 1
    if [ "$FAIL_LOOKUP" = "$3:$n" ]; then head -c 12 "$KEYRING/$3"; exit 1; fi
    cat "$KEYRING/$3" ;;
  "gcloud auth list"*) [ -n "$SIGNED_IN" ] && echo owner@example.com ;;
  "gcloud services list"*) printf '%s\n' $APIS_ON ;;
  "gcloud artifacts docker tags list"*) [ -z "$TAGS_ERROR" ] || { echo "ERROR: $TAGS_ERROR" >&2; exit 1; }
    for t in $TAGS $(cat "$STATE/tags" 2>/dev/null); do
      echo "projects/vouch-demo-123/locations/europe-west1/repositories/vouch/packages/vouch/tags/$t"
    done ;;
  "docker push"*) echo "${2##*:}" >> "$STATE/tags" ;;
  "gcloud projects describe"*"projectNumber"*) echo 123 ;;
  "gcloud sql instances describe"*) [ -n "$SQL" ] || exit 1; echo "$SQL" ;;
  "gcloud run services describe"*"serviceAccountName"*) echo "$SERVICE_ACCOUNT" ;;
  "gcloud run services describe"*) [ -n "$EXISTING" ] || [ -e "$STATE/deployed" ] || exit 1
    echo "$URL" ;;
  "gcloud run deploy"*) touch "$STATE/deployed" ;;
  "gcloud secrets describe"*) [ -e "$STATE/$3.created" ] || not_found "$3" ;;
  "gcloud secrets create"*) touch "$STATE/$3.created" ;;
  "gcloud secrets versions add"*) [ -e "$STATE/$4.created" ] || not_found "$4"
    n=$(( $(newest "$4") + 1 )); cat > "$STATE/$4.v$n"; echo "projects/123/secrets/$4/versions/$n" ;;
  "gcloud secrets versions describe latest"*) name=$(secret_flag "$@"); n=$(newest "$name")
    [ "$n" -gt 0 ] || not_found "$name"; echo "projects/123/secrets/$name/versions/$n" ;;
  "gcloud secrets versions access"*) name=$(secret_flag "$@")
    [ -f "$STATE/$name.v$4" ] && [ ! -e "$STATE/$name.v$4.disabled" ] || exit 1; cat "$STATE/$name.v$4" ;;
  "gcloud secrets versions disable"*) name=$(secret_flag "$@"); touch "$STATE/$name.v$4.disabled" ;;
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
    for entry, value in (values | {k.replace("_", "-"): v for k, v in changed.items()}).items():
        (keyring / entry).unlink(missing_ok=True)
        if entry not in missing:
            (keyring / entry).write_text(value)


def answer_prompts(proc: subprocess.Popen, project_id: str, no: tuple[str, ...]) -> str:
    """The wizard's output, answering each prompt by its text as it comes.

    "Run it?" gets n when the question or its command holds one of `no`, else y; "Project id:"
    gets `project_id`. A prompt left unanswered fails the test instead of hanging it.
    """
    assert proc.stdout and proc.stdin
    fd = proc.stdout.fileno()
    out = b""
    deadline = time.monotonic() + 30
    while True:
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([fd], [], [], left)[0]:
            proc.kill()
            raise AssertionError("the wizard waited on: " + out.decode(errors="replace")[-400:])
        chunk = os.read(fd, 65536)
        if not chunk:
            return out.decode(errors="replace")
        out += chunk
        text = out.decode(errors="replace")
        if text.endswith("Run it? [y/N] "):
            asked = "\n".join(text.splitlines()[-3:-1])
            reply = "n" if any(word in asked for word in no) else "y"
        elif text.endswith("Project id: "):
            reply = project_id
        else:
            continue
        try:
            proc.stdin.write(f"{reply}\n".encode())
            proc.stdin.flush()
        except BrokenPipeError:
            pass


def run_wizard(
    tmp_path: Path,
    *args: str,
    project_id: str = "vouch-demo-123",
    no: tuple[str, ...] = (),
    signed_in: bool = True,
    healthy: bool = False,
    database: str = "reachable",
    path: str | None = None,
    existing: bool = True,
    url: str = URL,
    sql: str = SQL_INSTANCE,
    service_account: str = "",
    fail: str = "",
    fail_lookup: str = "",
    tags: str = "",
    tags_error: str = "",
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
    log = tmp_path / "calls.log"
    for counter in tmp_path.glob("calls.log.lookups.*"):
        counter.unlink()  # each run counts its own lookups
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin" if path is None else path,
        "HOME": str(tmp_path),
        "CALLS": str(log),
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
        "FAIL_LOOKUP": fail_lookup,
        "TAGS": tags,
        "TAGS_ERROR": tags_error,
        "WIZARD_ENV": str(tmp_path / "wizard.env"),
    }
    proc = subprocess.Popen(
        [BASH, str(WIZARD), *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=ROOT,
    )
    errors: list[bytes] = []
    reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()))  # type: ignore[union-attr]
    reader.start()
    stdout = answer_prompts(proc, project_id, no)
    proc.wait(timeout=30)
    reader.join(timeout=30)
    assert proc.stdin
    proc.stdin.close()
    result = SimpleNamespace(
        returncode=proc.returncode, stdout=stdout, stderr=b"".join(errors).decode(errors="replace")
    )
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


def pinned(calls: str) -> dict[str, str]:
    """The deploy's --set-secrets, as env var: secret:version."""
    return dict(pair.split("=", 1) for pair in deploy_args(calls)["--set-secrets"].split(","))


def versions(tmp_path: Path, secret: str) -> dict[int, str]:
    """The fake's versions of `secret`: number -> enabled or disabled."""
    state = tmp_path / "state"
    found = {}
    for path in state.glob(f"{secret}.v*"):
        number = path.name.removeprefix(f"{secret}.v")
        if number.isdigit():
            disabled = (state / f"{path.name}.disabled").exists()
            found[int(number)] = "disabled" if disabled else "enabled"
    return found


def deployed(tmp_path: Path, **kwargs):
    """A full run, every question answered yes, the database set up, the service healthy."""
    return run_wizard(tmp_path, DB_READY, healthy=True, **kwargs)


def test_the_script_parses():
    assert subprocess.run([BASH, "-n", str(WIZARD)], check=False).returncode == 0


def test_help_lists_the_steps_and_the_db_ready_flag(tmp_path):
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
    assert "--db-ready" in result.stdout
    assert calls == ""


def test_an_unknown_option_is_refused_on_stderr(tmp_path):
    result, _ = run_wizard(tmp_path, "--deploy-now")
    assert result.returncode == 2
    assert "Unknown option" in result.stderr


def test_a_dry_run_prints_every_command_runs_none_and_writes_nothing(tmp_path):
    result, calls = run_wizard(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    # no gcloud, docker, curl or secret-tool call but one read: the registry's tags (#135)
    assert calls.splitlines() == [f"gcloud artifacts docker tags list {IMAGE} --format=value(tag)"]
    assert not (tmp_path / "wizard.env").exists()
    for command in (
        "gcloud auth list --filter=status:ACTIVE --format=value\\(account\\)",
        "check:  $ secret-tool lookup service clerk-vouch-publishable",
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
    result, calls = run_wizard(tmp_path, "--dry-run", path="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker push {IMAGE}:v1" in result.stdout
    assert calls == ""


def test_a_deploy_saves_only_plain_answers_and_finishes(tmp_path):
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"gcloud run deploy vouch --image={IMAGE}:v1" in calls
    assert "Done." in result.stdout
    assert (tmp_path / "wizard.env").read_text().splitlines() == [
        "PROJECT_ID=vouch-demo-123",
        "REGION=europe-west1",
    ]  # the tag comes from the registry, not this file (#135)


def test_secret_values_reach_gcloud_only_on_standard_input(tmp_path):
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    seen = calls + result.stdout + result.stderr + (tmp_path / "wizard.env").read_text()
    assert "planted" not in seen  # not one planted secret shows anywhere
    for secret, value in SECRETS.values():
        # Byte for byte what the keyring holds: the PEM's lines and final newline survive the pipe.
        assert (tmp_path / "state" / f"{secret}.v1").read_text() == value
        add = f"gcloud secrets versions add {secret} --data-file=- --project=vouch-demo-123"
        [call] = calls_to(calls, f"gcloud secrets versions add {secret} ")
        assert " ".join(call).startswith(add)


def test_every_secret_manager_call_runs_with_http_logging_off_and_no_debug_verbosity(tmp_path):
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    logged = (tmp_path / "state" / "log_http").read_text().splitlines()
    assert len(logged) == len(calls_to(calls, "gcloud secrets ")) > 0
    assert set(logged) == {"false warning"}  # beats core/log_http and core/verbosity in config
    assert "--verbosity" not in calls and "--log-http" not in calls


def test_a_missing_keyring_entry_stops_before_anything_changes(tmp_path):
    plant(tmp_path, missing=("clerk-vouch-jwt-key", "clerk-vouch-publishable"))
    result, calls = deployed(tmp_path)
    assert result.returncode != 0
    assert "clerk-vouch-jwt-key" in result.stderr
    assert "clerk-vouch-publishable" in result.stderr
    assert "clerk-vouch-secret" not in result.stderr  # only what's wrong is named
    gcloud = [" ".join(call) for call in calls_to(calls, "gcloud ")]
    assert gcloud == ["gcloud auth list --filter=status:ACTIVE --format=value(account)"]
    assert "\ndocker " not in "\n" + calls
    assert list((tmp_path / "state").iterdir()) == []
    assert not (tmp_path / "wizard.env").exists()


def test_a_jwt_key_stored_on_one_line_passes_and_reaches_secret_manager_unchanged(tmp_path):
    # The app drops a PEM's line breaks before parsing it (vouch.auth), so one line is fine (#125).
    one_line = "".join(PEM.splitlines())
    plant(tmp_path, clerk_vouch_jwt_key=one_line)
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "state" / "CLERK_JWT_KEY.v1").read_text() == one_line
    assert "planted" not in calls + result.stdout + result.stderr


def test_an_entry_of_only_whitespace_or_a_jwt_key_with_no_pem_counts_as_missing(tmp_path):
    plant(tmp_path, openrouter=" \n\t \n", clerk_vouch_jwt_key="sk_test_plantedNotAPem")
    result, calls = deployed(tmp_path)
    assert result.returncode != 0
    assert "openrouter" in result.stderr and "clerk-vouch-jwt-key" in result.stderr
    assert "planted" not in result.stdout + result.stderr + calls
    assert [" ".join(c) for c in calls_to(calls, "gcloud ")] == [
        "gcloud auth list --filter=status:ACTIVE --format=value(account)"
    ]


def test_a_publishable_key_that_isnt_one_is_never_shown_or_deployed(tmp_path):
    # A secret stored under the wrong entry: it must not become a plain env var.
    plant(tmp_path, clerk_vouch_publishable="sk_test_plantedSecretInTheWrongEntry")
    result, calls = deployed(tmp_path)
    assert result.returncode != 0
    assert "clerk-vouch-publishable" in result.stderr
    assert "pk_test_" in result.stderr  # says what it should look like
    assert "planted" not in result.stdout + result.stderr + calls
    assert "gcloud run deploy" not in calls
    assert list((tmp_path / "state").iterdir()) == []


def test_the_deploy_pins_each_secret_s_version_and_carries_the_settings_and_instance(tmp_path):
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert pinned(calls) == {secret: f"{secret}:1" for secret, _ in SECRETS.values()}
    args = deploy_args(calls)
    assert args["--set-env-vars"].split(",") == [
        f"CLERK_PUBLISHABLE_KEY={PUBLISHABLE}",
        f"CLERK_AUTHORIZED_PARTIES={URL}",
    ]
    assert args["--add-cloudsql-instances"] == SQL_INSTANCE
    assert args["--max-instances"] == "1" and args["--max"] == "1"
    assert args["--min-instances"] == "0"


def test_a_rerun_adds_a_version_only_for_a_changed_secret_and_pins_it(tmp_path):
    deployed(tmp_path)
    plant(tmp_path, openrouter="sk-or-v1-plantedRotatedKey000")
    result, calls = deployed(tmp_path, project_id="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert versions(tmp_path, "OPENROUTER_API_KEY") == {1: "enabled", 2: "enabled"}
    for secret in ("CLERK_SECRET_KEY", "CLERK_JWT_KEY", "VOUCH_DATABASE_URL"):
        assert versions(tmp_path, secret) == {1: "enabled"}
    last = calls.split("gcloud auth list")[-1]  # the second run's calls
    assert pinned(last)["OPENROUTER_API_KEY"] == "OPENROUTER_API_KEY:2"
    assert pinned(last)["CLERK_JWT_KEY"] == "CLERK_JWT_KEY:1"
    assert "planted" not in result.stdout + result.stderr


def test_a_lookup_failing_during_the_add_disables_that_version_and_stops(tmp_path):
    # clerk-vouch-jwt-key's lookups in a fresh project: the keyring check, the check just before
    # the add, then the add's own. The third prints part of the PEM and fails.
    result, calls = deployed(tmp_path, fail_lookup="clerk-vouch-jwt-key:3")
    assert result.returncode != 0
    assert versions(tmp_path, "CLERK_JWT_KEY") == {1: "disabled"}
    assert "CLERK_JWT_KEY" in result.stderr and "disabled" in result.stderr
    assert "gcloud run deploy" not in calls
    assert "planted" not in result.stdout + result.stderr + calls


def test_an_entry_emptied_since_the_keyring_step_stops_before_the_add(tmp_path):
    result, calls = deployed(tmp_path, fail_lookup="clerk-vouch-jwt-key:2")
    assert result.returncode != 0
    assert "clerk-vouch-jwt-key" in result.stderr
    assert versions(tmp_path, "CLERK_JWT_KEY") == {}
    assert "gcloud run deploy" not in calls


def test_a_skipped_create_stops_before_access_naming_the_secret(tmp_path):
    result, calls = deployed(tmp_path, no=("Create the secret CLERK_JWT_KEY",))
    assert result.returncode != 0
    assert "CLERK_JWT_KEY" in result.stderr
    assert "add-iam-policy-binding" not in calls
    assert "gcloud run deploy" not in calls


def test_a_skipped_version_stops_before_access_naming_the_secret(tmp_path):
    result, calls = deployed(tmp_path, no=("Put clerk-vouch-jwt-key",))
    assert result.returncode != 0
    assert "CLERK_JWT_KEY" in result.stderr
    assert "add-iam-policy-binding" not in calls
    assert "gcloud run deploy" not in calls


def test_a_secret_manager_error_other_than_not_found_says_wait_and_run_again(tmp_path):
    result, calls = deployed(tmp_path, fail="gcloud secrets describe")
    assert result.returncode != 0
    assert "PERMISSION_DENIED" in result.stderr
    assert "wait a minute" in result.stderr
    assert "gcloud secrets create" not in calls


def test_the_service_account_may_read_each_secret_and_connect_to_cloud_sql(tmp_path):
    result, calls = deployed(tmp_path)
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
    result, calls = deployed(tmp_path, service_account=account)
    assert result.returncode == 0, result.stdout + result.stderr
    bindings = calls_to(calls, "gcloud secrets add-iam-policy-binding ")
    bindings += calls_to(calls, "gcloud projects add-iam-policy-binding ")
    assert len(bindings) == 5
    assert all(f"--member=serviceAccount:{account}" in call for call in bindings)


def test_without_db_ready_it_stops_at_the_database_step_with_the_setup_commands(tmp_path):
    result, calls = run_wizard(tmp_path, healthy=True)
    assert result.returncode != 0
    assert f"cloud-sql-proxy {SQL_INSTANCE}" in result.stdout
    assert "uv run vouch-db-setup" in result.stdout
    assert "--db-ready" in result.stderr
    assert "gcloud run deploy" not in calls


def test_a_missing_cloud_sql_instance_stops_before_the_deploy(tmp_path):
    result, calls = deployed(tmp_path, sql="")
    assert result.returncode != 0
    assert "vouch-db" in result.stderr
    assert "gcloud run deploy" not in calls


def test_a_failed_gcloud_step_stops_there_naming_the_step_and_the_rerun(tmp_path):
    result, calls = deployed(tmp_path, fail="gcloud secrets create")
    assert result.returncode != 0
    assert "Secret Manager" in result.stderr
    assert "scripts/deploy-wizard.sh again" in result.stderr
    assert "gcloud secrets versions add" not in calls
    assert "gcloud run deploy" not in calls


def test_a_first_deploy_accepts_sign_in_from_the_deterministic_url(tmp_path):
    result, calls = deployed(tmp_path, existing=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"CLERK_AUTHORIZED_PARTIES={URL}" in deploy_args(calls)["--set-env-vars"]


def test_a_service_url_other_than_the_one_set_says_to_run_again(tmp_path):
    hashed = "https://vouch-abc123xyz-ew.a.run.app"
    result, calls = deployed(tmp_path, existing=False, url=hashed)
    assert result.returncode != 0
    assert f"CLERK_AUTHORIZED_PARTIES={URL}" in deploy_args(calls)["--set-env-vars"]
    assert hashed in result.stderr
    assert "again" in result.stderr


def test_the_deploy_caps_the_service_not_just_the_revision_at_one_instance(tmp_path):
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    [deploy] = [line for line in calls.splitlines() if line.startswith("gcloud run deploy ")]
    args = deploy.split()
    assert "--max=1" in args  # the service-level limit, shared by every revision with traffic
    assert "--max-instances=1" in args and "--min-instances=0" in args


def test_the_project_id_is_asked_once(tmp_path):
    deployed(tmp_path)
    result, _ = deployed(tmp_path, project_id="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Project id:" not in result.stdout
    assert f"gcloud run deploy vouch --image={IMAGE}:v2" in result.stdout


def test_a_skipped_build_deploys_the_registry_s_highest_tag(tmp_path):
    result, calls = deployed(tmp_path, no=("Build the image",), tags="v1 v3 v2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "\ndocker " not in "\n" + calls
    assert f"gcloud run deploy vouch --image={IMAGE}:v3" in calls


def test_the_next_tag_is_one_past_the_registry_s_highest_with_no_wizard_env(tmp_path):
    result, calls = deployed(tmp_path, tags="v1 v2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker build -t {IMAGE}:v3 ." in calls
    assert f"gcloud run deploy vouch --image={IMAGE}:v3" in calls
    assert "TAG=" not in (tmp_path / "wizard.env").read_text()


def test_the_next_tag_counts_numbers_not_letters_and_ignores_other_tags(tmp_path):
    result, calls = deployed(tmp_path, tags="latest v2 v10 v9")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker push {IMAGE}:v11" in calls


def test_with_no_tags_in_the_registry_the_first_tag_is_v1(tmp_path):
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker push {IMAGE}:v1" in calls


def test_an_old_wizard_env_tag_is_ignored_and_dropped(tmp_path):
    saved(tmp_path, PROJECT_ID="vouch-demo-123", REGION="europe-west1", TAG="v1")
    result, calls = deployed(tmp_path, tags="v1 v2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker push {IMAGE}:v3" in calls
    assert "TAG=" not in (tmp_path / "wizard.env").read_text()


def test_the_dry_run_names_the_tag_it_would_use_from_the_registry(tmp_path):
    saved(tmp_path, PROJECT_ID="vouch-demo-123", REGION="europe-west1")
    result, _ = run_wizard(tmp_path, "--dry-run", tags="v1 v2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "as v3 (the registry's highest: v2)" in result.stdout
    assert f"docker push {IMAGE}:v3" in result.stdout


def test_a_registry_that_can_t_be_read_stops_before_the_build(tmp_path):
    result, calls = deployed(tmp_path, tags="v1", tags_error="(fake) PERMISSION_DENIED")
    assert result.returncode != 0
    assert "registry's tags" in result.stderr
    assert not calls_to(calls, "docker ")


def test_an_image_not_in_the_registry_yet_counts_as_no_tags(tmp_path):
    result, calls = deployed(tmp_path, tags_error="NOT_FOUND: Package not found")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker push {IMAGE}:v1" in calls


def test_a_skipped_first_build_stops_before_the_deploy(tmp_path):
    result, calls = deployed(tmp_path, no=("Build the image",))
    assert result.returncode != 0
    assert "nothing to deploy" in result.stderr
    assert "gcloud run deploy" not in calls
    assert "TAG=" not in (tmp_path / "wizard.env").read_text()


def test_the_answers_file_is_git_ignored():
    ignored = ["git", "check-ignore", "-q", "deploy/cloudrun/wizard.env"]
    assert subprocess.run(ignored, check=False, cwd=ROOT).returncode == 0


def test_not_signed_in_stops_with_the_sign_in_command(tmp_path):
    result, calls = deployed(tmp_path, signed_in=False)
    assert result.returncode != 0
    assert "gcloud auth login" in result.stderr
    assert "\ndocker " not in "\n" + calls


def test_a_failed_health_check_stops_and_prints_the_logs_command(tmp_path):
    result, calls = run_wizard(tmp_path, DB_READY)
    assert result.returncode != 0
    assert "gcloud run deploy vouch" in calls
    assert f"{URL}/api/health" in result.stderr
    assert "gcloud logging read" in result.stderr


def test_a_health_check_with_the_database_unavailable_stops_the_deploy(tmp_path):
    """From M3 the live app always has a database (#33): status ok alone isn't a working deploy."""
    result, _ = deployed(tmp_path, database="unavailable")
    assert result.returncode != 0
    assert "database" in result.stderr
    assert "gcloud logging read" in result.stderr


def test_a_health_check_saying_needs_setup_stops_naming_vouch_db_setup_and_the_proxy(tmp_path):
    """#123: the database answers but vouch-db-setup never ran, so every assessment would 503."""
    result, calls = deployed(tmp_path, database="needs-setup")
    assert result.returncode != 0
    assert "Done" not in result.stdout + result.stderr
    assert "vouch-db-setup" in result.stderr
    assert f"cloud-sql-proxy {SQL_INSTANCE} --port 5432" in result.stderr
    assert "uv run vouch-db-setup" in result.stderr
    assert len(calls_to(calls, "curl ")) == 1  # a clear answer: no retry


def test_answering_n_to_the_deploy_says_nothing_was_deployed_and_fails(tmp_path):
    """#123: health passing on the old revision isn't this run's deploy."""
    result, calls = deployed(tmp_path, no=("Deploy the service",))
    assert result.returncode != 0
    assert "Done" not in result.stdout + result.stderr
    assert "Nothing was deployed" in result.stderr
    assert "gcloud run deploy" not in calls
    assert "curl" not in calls
