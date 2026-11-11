"""The deploy wizard, with fake gcloud, docker, curl, secret-tool, cloud-sql-proxy and uv that log
each call.

Nothing reaches Google or the real keyring: the fake secret-tool reads planted values from a
folder, and the fake gcloud keeps its secrets' versions in another, as they came on standard input.
The fake cloud-sql-proxy makes its Unix socket in the folder it's given; the fake uv runs the real
URL helper, whose command is the fake `docker run`: it stands in for vouch-db-setup in the image and
keeps the database URL it got in a file, never in the call log.
The tests answer the wizard's prompts by their text, as the owner would, not by counting them.
"""

import os
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg import conninfo

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "scripts" / "deploy-wizard.sh"
BASH = shutil.which("bash") or "/bin/bash"
URL = "https://vouch-123.europe-west1.run.app"  # Cloud Run's fixed address, for project number 123
HASHED = "https://vouch-abc123xyz-ew.a.run.app"  # status.url on the live service
IMAGE = "europe-west1-docker.pkg.dev/vouch-demo-123/vouch/vouch"
SQL_INSTANCE = "vouch-demo-123:europe-west1:vouch-db"
DEFAULT_ACCOUNT = "123-compute@developer.gserviceaccount.com"
COMMIT = subprocess.run(
    ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
).stdout.strip()
"""This checkout's short commit, which the wizard bakes into the image it builds (#580)."""
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
  "gcloud auth application-default print-access-token"*) [ -n "$ADC" ] || exit 1
    echo ya29.plantedAccessToken ;;
  "gcloud services list"*) printf '%s\n' $APIS_ON ;;
  "gcloud artifacts docker tags list"*) [ -z "$TAGS_ERROR" ] || { echo "ERROR: $TAGS_ERROR" >&2; exit 1; }
    for t in $TAGS $(cat "$STATE/tags" 2>/dev/null); do
      echo "projects/vouch-demo-123/locations/europe-west1/repositories/vouch/packages/vouch/tags/$t"
    done ;;
  "docker push"*) echo "${2##*:}" >> "$STATE/tags" ;;
  "docker run"*) printf '%s' "$VOUCH_DATABASE_URL" > "$STATE/setup_url"
    mount=""; prev=""
    for a in "$@"; do [ "$prev" = "-v" ] && mount="${a%%:*}"; prev="$a"; done
    if [ -n "$mount" ] && [ -n "$(find "$mount" -name .s.PGSQL.5432 -type s 2>/dev/null)" ]
    then echo yes; else echo no; fi > "$STATE/setup_saw_proxy"
    if [ -n "$SETUP_WAITS" ]; then
      touch "$STATE/setup_started"; i=0
      while [ ! -e "$STATE/release" ] && [ $i -lt 400 ]; do sleep 0.05; i=$((i + 1)); done
    fi
    [ -z "$SETUP_FAILS" ] || { echo "vouch-db-setup: (fake) couldn't connect" >&2; exit 69; }
    echo "vouch-db-setup: LangGraph's tables are ready (checkpointer and store)" ;;
  "gcloud projects describe"*"projectNumber"*) echo "$PROJECT_NUMBER" ;;
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
  "gcloud secrets versions list"*) n=$(newest "$4")
    while [ "$n" -gt 0 ] && [ -e "$STATE/$4.v$n.disabled" ]; do n=$((n - 1)); done
    [ "$n" -gt 0 ] && echo "projects/123/secrets/$4/versions/$n" ;;
  "gcloud secrets versions access"*) name=$(secret_flag "$@")
    [ -f "$STATE/$name.v$4" ] && [ ! -e "$STATE/$name.v$4.disabled" ] || exit 1; cat "$STATE/$name.v$4" ;;
  "gcloud secrets versions disable"*) name=$(secret_flag "$@"); touch "$STATE/$name.v$4.disabled" ;;
  "curl"*) [ -n "$HEALTHY" ] || exit 22
    echo '{"status": "ok", "version": "0.1.0", "database": "'"$DATABASE"'"}' ;;
esac
exit 0
"""


# The two fakes that run as programs of their own (Python, the test's interpreter). The proxy
# logs its arguments and the mode of its --unix-socket folder (in $STATE/socket_dir_mode), waits
# $PROXY_DELAY seconds, then makes <folder>/<instance>/.s.PGSQL.5432, as cloud-sql-proxy v2 does for
# Postgres, and keeps it until a TERM or INT, which it logs as "stopped". $PROXY_FAILS makes it fail
# at start. It gives up by itself after 60 s, so a wizard that never stops it can't leave it
# running. The uv fake logs its arguments and runs what follows "python" with the test's own
# interpreter: the real URL helper, which then runs the fake `docker run`.
FAKE_PROXY = r"""
import os, signal, socket, stat, sys, time
from pathlib import Path
def log(line):
    with open(os.environ["CALLS"], "a") as calls:
        calls.write(line + "\n")
log("cloud-sql-proxy " + " ".join(sys.argv[1:]))
folder = Path(sys.argv[sys.argv.index("--unix-socket") + 1])
mode = oct(stat.S_IMODE(folder.stat().st_mode))[2:]
(Path(os.environ["STATE"]) / "socket_dir_mode").write_text(mode)
if os.environ.get("PROXY_FAILS"):
    print("(fake) cloud-sql-proxy: couldn't start", file=sys.stderr)
    sys.exit(1)
def stopped(signum, frame):
    log("cloud-sql-proxy stopped")
    sys.exit(143)
signal.signal(signal.SIGTERM, stopped)
signal.signal(signal.SIGINT, stopped)
time.sleep(float(os.environ.get("PROXY_DELAY") or 0))
instance = folder / sys.argv[1]
instance.mkdir()
server = socket.socket(socket.AF_UNIX)
server.bind(str(instance / ".s.PGSQL.5432"))
server.listen()
time.sleep(60)
log("cloud-sql-proxy gave up: nobody stopped it")
"""
FAKE_UV = r"""
import os, sys
with open(os.environ["CALLS"], "a") as calls:
    calls.write("uv " + " ".join(sys.argv[1:]) + "\n")
rest = sys.argv[sys.argv.index("python") + 1:]
os.execv(sys.executable, [sys.executable, *rest])
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
    project_number: str = "123",
    proxy_fails: bool = False,
    proxy_delay: float = 0,
    setup_fails: bool = False,
    interrupt: signal.Signals | None = None,
    missing_tool: str = "",
    adc: bool = True,
    bash_x: bool = False,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    # Every tool is faked, secret-tool too: the real one would read the owner's keyring.
    fakes = {tool: FAKE for tool in ("gcloud", "docker", "curl", "secret-tool")}
    fakes |= {"cloud-sql-proxy": FAKE_PROXY, "uv": FAKE_UV}
    for tool, fake in fakes.items():
        program = fake if fake is FAKE else f"#!{sys.executable}\n{fake}"
        (bin_dir / tool).write_text(program)
        (bin_dir / tool).chmod(0o755)
    if missing_tool:
        (bin_dir / missing_tool).unlink()
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
        "PROJECT_NUMBER": project_number,
        "WIZARD_ENV": str(tmp_path / "wizard.env"),
        "PROXY_FAILS": "yes" if proxy_fails else "",
        "PROXY_DELAY": str(proxy_delay),
        "SETUP_FAILS": "yes" if setup_fails else "",
        "SETUP_WAITS": "yes" if interrupt else "",
        "ADC": "yes" if adc else "",
    }
    proc = subprocess.Popen(
        [BASH, *(["-x"] if bash_x else []), str(WIZARD), *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=ROOT,
    )
    errors: list[bytes] = []
    reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()))  # type: ignore[union-attr]
    reader.start()
    if interrupt:
        # Once setup is running: the signal to the wizard alone, then setup may finish.
        def interrupt_setup() -> None:
            started = tmp_path / "state" / "setup_started"
            end = time.monotonic() + 20
            while not started.exists() and time.monotonic() < end:
                time.sleep(0.05)
            proc.send_signal(interrupt)
            time.sleep(0.2)
            (tmp_path / "state" / "release").touch()

        threading.Thread(target=interrupt_setup, daemon=True).start()
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


def env_vars(calls: str) -> dict[str, str]:
    """The deploy's --set-env-vars, as NAME: value."""
    return dict(item.split("=", 1) for item in deploy_args(calls)["--set-env-vars"].split(","))


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


def deployed(tmp_path: Path, *args: str, **kwargs):
    """A full run, every question answered yes, the service healthy."""
    return run_wizard(tmp_path, *args, healthy=True, **kwargs)


def socket_folder(calls: str) -> Path:
    """The --unix-socket folder the wizard gave the one cloud-sql-proxy it started."""
    [start] = [c for c in calls_to(calls, "cloud-sql-proxy ") if c[1] != "stopped"]
    return Path(start[start.index("--unix-socket") + 1])


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
        f"docker build --build-arg VOUCH_COMMIT={COMMIT} -t {IMAGE}:v1 .",
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
    assert f"docker build --build-arg VOUCH_COMMIT=dev -t {IMAGE}:v1 ." in result.stdout  # no git
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
    assert gcloud == [
        "gcloud auth list --filter=status:ACTIVE --format=value(account)",
        "gcloud auth application-default print-access-token",
    ]
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
        "gcloud auth list --filter=status:ACTIVE --format=value(account)",
        "gcloud auth application-default print-access-token",
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
    assert env_vars(calls) == {
        "CLERK_PUBLISHABLE_KEY": PUBLISHABLE,
        "CLERK_AUTHORIZED_PARTIES": URL,
    }
    assert args["--add-cloudsql-instances"] == SQL_INSTANCE
    assert args["--max-instances"] == "1" and args["--max"] == "1"
    assert args["--min-instances"] == "0"


def test_cloud_runs_request_timeout_outlasts_a_profile_pass_within_its_maximum(tmp_path):
    from vouch.model_choice import PROFILE_PASS_TIMEOUT_SECONDS

    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr

    timeout = int(deploy_args(calls)["--timeout"])  # seconds; Cloud Run's default is 300
    assert PROFILE_PASS_TIMEOUT_SECONDS + 60 <= timeout < 3600  # Cloud Run: under 60 minutes


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


# --- The database step (#594): vouch-db-setup from the deployed image, through a proxy ----------

RUN_URL = SECRETS["cloudsql-vouch-run-url"][1]
IN_THE_CONTAINER = f"/cloudsql-proxy/{SQL_INSTANCE}"  # where setup finds the proxy's socket


def setup_steps(calls: str) -> list[str]:
    """The proxy, the URL helper, setup and the deploy, as called, in order."""
    starts = ("cloud-sql-proxy", "uv ", "docker run", "gcloud run deploy")
    return [line for line in calls.splitlines() if line.startswith(starts)]


def test_setup_runs_the_deployed_image_through_a_proxy_socket_then_the_proxy_stops_then_the_deploy(
    tmp_path,
):
    result, calls = deployed(tmp_path, proxy_delay=0.5)
    assert result.returncode == 0, result.stdout + result.stderr
    folder = socket_folder(calls)
    image = f"{IMAGE}:v1"
    steps = setup_steps(calls)
    setup = (
        f"docker run --rm --network none --user {os.getuid()}:{os.getgid()} "
        f"-v {folder}:/cloudsql-proxy:z -e VOUCH_DATABASE_URL {image} vouch-db-setup"
    )
    helper = f"uv run --frozen python -I scripts/proxy-database-url.py {IN_THE_CONTAINER} --"
    assert steps[:4] == [
        f"cloud-sql-proxy {SQL_INSTANCE} --unix-socket {folder} --quiet",
        f"{helper} {setup}",
        setup,
        "cloud-sql-proxy stopped",
    ]
    assert steps[4].startswith(f"gcloud run deploy vouch --image={image} ") and len(steps) == 5
    # The URL is the version the deploy pins, pointed at the socket as the container sees it...
    access = "gcloud secrets versions access 1 --secret=VOUCH_DATABASE_URL --project=vouch-demo-123"
    assert access in calls
    url = (tmp_path / "state" / "setup_url").read_text()
    assert conninfo.conninfo_to_dict(url)["host"] == IN_THE_CONTAINER
    # ...the socket was there when setup started, though the proxy took half a second...
    assert (tmp_path / "state" / "setup_saw_proxy").read_text().strip() == "yes"
    # ...and its folder was private, and is gone.
    assert (tmp_path / "state" / "socket_dir_mode").read_text() == "700"
    assert not folder.exists()


def test_the_database_url_is_never_printed_or_an_argument(tmp_path):
    special = f"postgresql://vouch:pl%40nted:p?w#d&x=y@/vouch?host=/cloudsql/{SQL_INSTANCE}"
    plant(tmp_path, cloudsql_vouch_run_url=special)
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    url = (tmp_path / "state" / "setup_url").read_text()
    assert conninfo.conninfo_to_dict(url)["password"] == "pl@nted:p?w#d&x=y"
    seen = calls + result.stdout + result.stderr
    assert "planted" not in seen and "pl@nted" not in seen and "pl%40nted" not in seen
    assert special not in seen and url not in seen


def test_the_setup_step_never_runs_traced_even_under_bash_x(tmp_path):
    result, calls = run_wizard(tmp_path, healthy=True, bash_x=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "planted" not in result.stdout + result.stderr + calls


def test_a_stored_url_libpq_can_t_read_stops_before_setup_unshown(tmp_path):
    plant(tmp_path, cloudsql_vouch_run_url="postgresql://vouch:planted@/vouch?nonsense=planted")
    result, calls = deployed(tmp_path)
    assert result.returncode != 0
    assert "VOUCH_DATABASE_URL" in result.stderr and "Nothing was deployed" in result.stderr
    assert "planted" not in calls + result.stdout + result.stderr
    assert not calls_to(calls, "docker run ") and "gcloud run deploy" not in calls
    assert calls.splitlines()[-1] == "cloud-sql-proxy stopped"


def test_a_failed_setup_stops_the_proxy_and_deploys_nothing(tmp_path):
    result, calls = deployed(tmp_path, setup_fails=True)
    assert result.returncode != 0
    assert "vouch-db-setup" in result.stderr and "Nothing was deployed" in result.stderr
    assert calls.splitlines()[-1] == "cloud-sql-proxy stopped"
    assert "gcloud run deploy" not in calls
    assert not socket_folder(calls).exists()
    assert "planted" not in calls + result.stdout + result.stderr


def test_a_proxy_that_exits_before_its_socket_stops_before_setup_and_the_deploy(tmp_path):
    result, calls = deployed(tmp_path, proxy_fails=True)
    assert result.returncode != 0
    assert "proxy" in result.stderr and "Nothing was deployed" in result.stderr
    assert not calls_to(calls, "docker run ") and "gcloud run deploy" not in calls
    assert not socket_folder(calls).exists()


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM], ids=["interrupt", "terminate"])
def test_an_interrupt_during_setup_stops_the_proxy_and_deploys_nothing(tmp_path, sig):
    result, calls = deployed(tmp_path, interrupt=sig)
    assert result.returncode != 0
    assert "cloud-sql-proxy stopped" in calls
    assert "gcloud run deploy" not in calls
    assert "Done" not in result.stdout
    assert not socket_folder(calls).exists()


def test_answering_n_to_the_setup_stops_with_nothing_deployed_and_no_proxy(tmp_path):
    result, calls = deployed(tmp_path, no=("vouch-db-setup",))
    assert result.returncode != 0
    assert "Nothing was deployed" in result.stderr
    assert "cloud-sql-proxy" not in calls and "gcloud run deploy" not in calls


def test_a_skipped_build_sets_up_with_the_registry_image_the_deploy_uses(tmp_path):
    result, calls = deployed(tmp_path, no=("Build the image",), tags="v1 v3 v2")
    assert result.returncode == 0, result.stdout + result.stderr
    [setup] = calls_to(calls, "docker run ")
    assert f"{IMAGE}:v3" in setup
    assert f"gcloud run deploy vouch --image={IMAGE}:v3" in calls


def test_db_ready_still_parses_does_nothing_and_says_so(tmp_path):
    result, calls = deployed(tmp_path, DB_READY)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--db-ready does nothing" in result.stdout
    assert "docker run" in calls  # setup runs all the same
    assert "gcloud run deploy" in calls


@pytest.mark.parametrize("tool", ["cloud-sql-proxy", "uv"])
def test_a_missing_proxy_or_uv_stops_before_anything_changes(tmp_path, tool):
    result, calls = deployed(tmp_path, missing_tool=tool)
    assert result.returncode != 0
    assert f"{tool} is missing" in result.stderr
    assert calls == ""


def test_no_application_default_credentials_stops_at_sign_in_before_anything_is_built(tmp_path):
    result, calls = deployed(tmp_path, adc=False)
    assert result.returncode != 0
    assert "gcloud auth application-default login" in result.stderr
    assert "docker " not in calls and "secret-tool" not in calls
    assert "planted" not in calls + result.stdout + result.stderr


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


@pytest.mark.parametrize("existing", [True, False], ids=["redeploy", "first-deploy"])
def test_a_service_whose_status_url_is_hashed_authorizes_and_checks_only_the_fixed_one(
    tmp_path, existing
):
    """#178: Cloud Run's status.url may be the hashed address; sign-in, the health check and the
    address to share are the fixed one, whatever status.url says."""
    result, calls = deployed(tmp_path, existing=existing, url=HASHED)
    assert result.returncode == 0, result.stdout + result.stderr
    assert env_vars(calls)["CLERK_AUTHORIZED_PARTIES"] == URL
    assert f"Vouch is at {URL}" in result.stdout
    assert calls_to(calls, "curl ")[-1][-1] == f"{URL}/api/health"
    assert HASHED not in result.stdout + result.stderr


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
    assert not calls_to(calls, "docker build") and not calls_to(calls, "docker push")
    assert f"gcloud run deploy vouch --image={IMAGE}:v3" in calls


def test_the_next_tag_is_one_past_the_registry_s_highest_with_no_wizard_env(tmp_path):
    result, calls = deployed(tmp_path, tags="v1 v2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker build --build-arg VOUCH_COMMIT={COMMIT} -t {IMAGE}:v3 ." in calls
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
    result, calls = deployed(tmp_path, tags_error="(fake) PERMISSION_DENIED")
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
    result, calls = run_wizard(tmp_path)
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


def test_a_health_check_saying_needs_setup_stops_naming_vouch_db_setup(tmp_path):
    """#123: the database answers but lacks vouch-db-setup's tables, so every assessment would 503.
    The wizard ran setup just before (#594), so it names that and the database the service uses."""
    result, calls = deployed(tmp_path, database="needs-setup")
    assert result.returncode != 0
    assert "Done" not in result.stdout + result.stderr
    assert "vouch-db-setup" in result.stderr and "VOUCH_DATABASE_URL" in result.stderr
    assert "5432" not in result.stdout + result.stderr  # the wizard never sends you to it
    assert len(calls_to(calls, "curl ")) == 1  # a clear answer: no retry


def test_answering_n_to_the_deploy_says_nothing_was_deployed_and_fails(tmp_path):
    """#123: health passing on the old revision isn't this run's deploy."""
    result, calls = deployed(tmp_path, no=("Deploy the service",))
    assert result.returncode != 0
    assert "Done" not in result.stdout + result.stderr
    assert "Nothing was deployed" in result.stderr
    assert "gcloud run deploy" not in calls
    assert "curl" not in calls


def test_a_discord_server_id_in_wizard_env_reaches_the_service(tmp_path):
    """#152: the course's Discord server gates sign-in; #153 puts its id in wizard.env."""
    server = "111111111111111111"
    saved(tmp_path, PROJECT_ID="vouch-demo-123", REGION="europe-west1", DISCORD_SERVER_ID=server)
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert env_vars(calls)["DISCORD_SERVER_ID"] == server
    assert "members of Discord server 111111111111111111" in result.stdout


def test_a_discord_server_id_that_isnt_one_stops_before_anything_changes(tmp_path):
    saved(tmp_path, PROJECT_ID="vouch-demo-123", REGION="europe-west1", DISCORD_SERVER_ID="course")
    result, calls = deployed(tmp_path)
    assert result.returncode != 0
    assert "DISCORD_SERVER_ID" in result.stderr
    assert "gcloud run deploy" not in calls and "docker " not in calls


@pytest.mark.parametrize(
    "region",
    ["europe-west1,https://other.example", "europe-west1.evil", "Europe-West1", "west1"],
)
def test_a_region_that_isnt_a_cloud_run_region_stops_before_anything_changes(tmp_path, region):
    """#192: REGION goes into the sign-in address, so a comma would add a second origin."""
    saved(tmp_path, PROJECT_ID="vouch-demo-123", REGION=region)
    result, calls = deployed(tmp_path)
    assert result.returncode != 0
    assert "REGION" in result.stderr
    assert region in result.stderr  # the value is named
    assert "gcloud run deploy" not in calls and "docker " not in calls


@pytest.mark.parametrize("number", ["12a3", "123,https://other.example", ""])
def test_a_project_number_that_isnt_digits_stops_before_the_deploy(tmp_path, number):
    """#192: the project number goes into the sign-in address too."""
    result, calls = deployed(tmp_path, project_number=number)
    assert result.returncode != 0
    assert "project" in result.stderr and "number" in result.stderr
    assert "gcloud run deploy" not in calls


def test_a_dry_run_with_today_s_wizard_env_passes(tmp_path):
    """#192: the live service's project and region pass the new checks."""
    saved(tmp_path, PROJECT_ID="tc-vouch-compliance", REGION="europe-west1")
    result, _ = run_wizard(tmp_path, "--dry-run", project_id="tc-vouch-compliance")
    assert result.returncode == 0, result.stdout + result.stderr


def test_without_a_discord_server_id_it_says_clerk_alone_decides_who_signs_in(tmp_path):
    """#152: with Clerk's sign-up open (#153), a deploy without the id would let anyone in."""
    result, _ = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No DISCORD_SERVER_ID" in result.stdout


# --- Tracing (#460): the Langfuse keys reach the service only when Secret Manager has both ----

LANGFUSE = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


def in_secret_manager(tmp_path: Path, secret: str, versions: int = 1) -> None:
    """A secret the owner made by hand, with `versions` versions (the fake gcloud's state)."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / f"{secret}.created").touch()
    for n in range(1, versions + 1):
        (state / f"{secret}.v{n}").write_text(f"planted-{secret}-{n}")


def test_without_the_langfuse_secrets_the_deploy_goes_on_untraced_and_makes_none(tmp_path):
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not set(LANGFUSE) & set(pinned(calls))
    writes = ("gcloud secrets create", "gcloud secrets versions add", "gcloud secrets add-iam")
    assert not [c for c in calls.splitlines() if c.startswith(writes) and "LANGFUSE" in c]
    assert "Tracing stays off" in result.stdout


def test_with_both_langfuse_secrets_the_deploy_pins_their_latest_versions(tmp_path):
    in_secret_manager(tmp_path, "LANGFUSE_PUBLIC_KEY", versions=2)
    in_secret_manager(tmp_path, "LANGFUSE_SECRET_KEY")
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert pinned(calls)["LANGFUSE_PUBLIC_KEY"] == "LANGFUSE_PUBLIC_KEY:2"
    assert pinned(calls)["LANGFUSE_SECRET_KEY"] == "LANGFUSE_SECRET_KEY:1"
    granted = {call[3] for call in calls_to(calls, "gcloud secrets add-iam-policy-binding ")}
    assert set(LANGFUSE) <= granted
    assert not calls_to(calls, "gcloud secrets versions add LANGFUSE")  # never written
    assert "planted-LANGFUSE" not in result.stdout + result.stderr + calls
    assert "LANGFUSE_BASE_URL" not in env_vars(calls)  # Langfuse Cloud's EU region: the default


def test_with_one_langfuse_secret_alone_neither_reaches_the_service(tmp_path):
    in_secret_manager(tmp_path, "LANGFUSE_SECRET_KEY")
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not set(LANGFUSE) & set(pinned(calls))
    assert "Tracing stays off" in result.stdout and "LANGFUSE_PUBLIC_KEY" in result.stdout


def test_a_langfuse_secret_whose_versions_are_all_disabled_leaves_tracing_off(tmp_path):
    for secret in LANGFUSE:
        in_secret_manager(tmp_path, secret)
    (tmp_path / "state" / "LANGFUSE_SECRET_KEY.v1.disabled").touch()
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not set(LANGFUSE) & set(pinned(calls))
    assert "Tracing stays off" in result.stdout


def test_an_enabled_older_version_is_pinned_past_a_disabled_newest(tmp_path):
    for secret in LANGFUSE:
        in_secret_manager(tmp_path, secret, versions=2)
    (tmp_path / "state" / "LANGFUSE_PUBLIC_KEY.v2.disabled").touch()
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert pinned(calls)["LANGFUSE_PUBLIC_KEY"] == "LANGFUSE_PUBLIC_KEY:1"


def test_a_dry_run_says_it_did_not_read_the_langfuse_secrets(tmp_path):
    result, _ = run_wizard(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Tracing stays off" not in result.stdout
    assert "LANGFUSE_SECRET_KEY" in result.stdout


def test_a_langfuse_base_url_in_wizard_env_reaches_a_traced_service(tmp_path):
    for secret in LANGFUSE:
        in_secret_manager(tmp_path, secret)
    us = "https://us.cloud.langfuse.com"
    saved(tmp_path, PROJECT_ID="vouch-demo-123", REGION="europe-west1", LANGFUSE_BASE_URL=us)
    result, calls = deployed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert env_vars(calls)["LANGFUSE_BASE_URL"] == us


def test_a_langfuse_base_url_that_isnt_an_origin_stops_before_anything_changes(tmp_path):
    saved(tmp_path, PROJECT_ID="vouch-demo-123", LANGFUSE_BASE_URL="us.cloud.langfuse.com/x")
    result, calls = deployed(tmp_path)
    assert result.returncode == 1
    assert "LANGFUSE_BASE_URL" in result.stderr
    assert "gcloud run deploy" not in calls and "docker build" not in calls
