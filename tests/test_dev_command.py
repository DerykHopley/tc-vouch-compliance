"""scripts/dev.sh: one command for the API and the Vite dev server, and Ctrl-C stops both.

The servers listen on spare loopback ports, never the owner's 8000 and 5173. This test process
opens no socket itself (pytest runs with --disable-socket): its child processes do the probing,
`curl` for HTTP and a short `python3` for a port's state, the way the script itself checks.
"""

import fcntl
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "scripts" / "dev.sh"
BASH = shutil.which("bash") or "/bin/bash"

# A child prints free loopback ports, or reports whether one is free (exit 0) or taken (exit 1).
FREE_PORTS = """
import socket, sys
socks = [socket.socket() for _ in range(int(sys.argv[1]))]
for s in socks: s.bind(("127.0.0.1", 0))
print(*(s.getsockname()[1] for s in socks))
"""
# The same probe as port_free() in scripts/dev.sh: taken when anything answers on either loopback.
PORT_FREE = """
import socket, sys
for host in ("127.0.0.1", "::1"):
    try: socket.create_connection((host, int(sys.argv[1])), timeout=1).close()
    except OSError: continue
    sys.exit(1)
"""
HOLD_PORT = """
import socket, sys, time
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
s.bind((host, port))
s.listen()
print("held", flush=True)
time.sleep(120)
"""
# A stand-in for `uv run uvicorn ...` or `npm run dev -- ...`: it records the environment it got
# in $FAKE_ENV_LOG/<tool>.json, then answers /api/health on its --port. CRASH_AFTER=<n> makes it
# exit 3 after n answers (a negative n: at once; 0: never); CRASH_AFTER=hang, never listen.
FAKE_SERVER = """
import http.server, json, os, sys, time
names = ("ASSESS_FAKE_SCRIPT", "OPENROUTER_API_KEY", "VOUCH_API", "VOUCH_AUTH_MODE")
env = {k: os.environ.get(k) for k in names}
log = os.path.join(os.environ["FAKE_ENV_LOG"], os.environ["FAKE_TOOL"] + ".json")
with open(log + ".tmp", "w") as f:
    json.dump(env, f)
os.replace(log + ".tmp", log)
if os.environ["CRASH_AFTER"] == "hang":
    time.sleep(600)
port = int(sys.argv[sys.argv.index("--port") + 1])
left = int(os.environ["CRASH_AFTER"]) or None
if left is not None and left < 0:
    sys.exit(3)
class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def log_message(self, *args):
        pass
server = http.server.HTTPServer(("127.0.0.1", port), Health)
while left is None or left > 0:
    server.handle_request()
    if left is not None:
        left -= 1
sys.exit(3)
"""
HAS_IPV6 = (
    subprocess.run(
        [sys.executable, "-c", "import socket; socket.socket(socket.AF_INET6).bind(('::1', 0))"],
        check=False,
        capture_output=True,
    ).returncode
    == 0
)


def free_ports(n: int) -> list[int]:
    out = subprocess.run(
        [sys.executable, "-c", FREE_PORTS, str(n)], capture_output=True, text=True, check=True
    )
    return [int(p) for p in out.stdout.split()]


def port_free(port: int) -> bool:
    return subprocess.run([sys.executable, "-c", PORT_FREE, str(port)], check=False).returncode == 0


def fetch(url: str) -> tuple[int, str]:
    out = subprocess.run(
        ["curl", "-sS", "--max-time", "10", "-w", "\n%{http_code}", url],
        check=False,
        capture_output=True,
        text=True,
    )
    body, _, code = out.stdout.rpartition("\n")
    return int(code or 0), body


def dev_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}


@pytest.mark.skipif(
    not (ROOT / "frontend" / "node_modules").is_dir() or shutil.which("curl") is None,
    reason="needs the front-end deps (npm ci in frontend/) and curl",
)
def test_starts_both_servers_and_ctrl_c_stops_both(tmp_path: Path) -> None:
    api_port, web_port = free_ports(2)
    with (tmp_path / "dev.log").open("w") as log:  # the child keeps its own copy of the fd
        proc = subprocess.Popen(
            [BASH, str(DEV), "--api-port", str(api_port), "--web-port", str(web_port)],
            cwd=ROOT,
            # A key in the environment, as in the owner's shell: the dev API still runs the fake.
            env=dev_env() | {"OPENROUTER_API_KEY": "sk-or-fake-not-a-key"},
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # its session holds every server, for the clean-up below
        )
    url = f"http://127.0.0.1:{web_port}/"
    try:
        deadline = time.monotonic() + 90  # past the script's own 60 s, so it diagnoses first
        while f"Vouch is running: {url}" not in (tmp_path / "dev.log").read_text():
            assert proc.poll() is None, (tmp_path / "dev.log").read_text()
            assert time.monotonic() < deadline, (tmp_path / "dev.log").read_text()
            time.sleep(0.5)

        code, body = fetch(url)
        assert code == 200 and '<div id="root">' in body
        code, body = fetch(url + "api/health")
        assert code == 200 and '"status":"ok"' in body.replace(" ", "")
        stream = subprocess.run(
            ["curl", "-sS", "-N", "--max-time", "30", "-X", "POST", url + "api/assessments"]
            + ["-H", "content-type: application/json", "-d", '{"control_id": "4.1.2"}'],
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        assert "event: result" in stream and "event: error" not in stream, stream

        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=30) == 130
        assert port_free(api_port), "the API still listens after Ctrl-C"
        assert port_free(web_port), "the Vite dev server still listens after Ctrl-C"
        assert not session_left(proc.pid), "a process outlived Ctrl-C"
    finally:
        kill_session(proc)


def session_left(sid: int) -> str:
    """What still runs in the script's session (it was started with start_new_session)."""
    out = subprocess.run(["pgrep", "-a", "-s", str(sid)], check=False, capture_output=True)
    return out.stdout.decode()


def kill_session(proc: subprocess.Popen) -> None:
    """Whatever failed, leave no server behind."""
    subprocess.run(["pkill", "-KILL", "-s", str(proc.pid)], check=False)
    if proc.poll() is None:
        proc.kill()
        proc.wait()


class PtyPopen(subprocess.Popen[str]):
    """A started dev.sh; with tty, `pty` is the other end of its terminal."""

    pty: int


def start_fake(
    tmp_path: Path,
    crash_after: dict[str, int | str],
    cwd: Path | None = None,
    *,
    tty: bool = False,
    env: dict[str, str] | None = None,
) -> PtyPopen:
    """dev.sh copied into a tree with frontend/node_modules, with fake servers for uv and npm.

    The run gets past its deps check without a real npm ci, so these tests run in the fast lane.
    It leads a new session, which holds every server it starts, for session_left and kill_session.
    With tty, that session's terminal is a pty, whose other end is the returned process's `pty`.
    """
    if tty:
        master, slave = os.openpty()
        streams: dict = {"stdin": slave, "stdout": slave, "stderr": slave}
        # After the setsid of start_new_session: the pty becomes the controlling terminal.
        streams["preexec_fn"] = lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    else:
        streams = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE}
        streams["stderr"] = subprocess.STDOUT
    (tmp_path / "scripts").mkdir()
    shutil.copy(DEV, tmp_path / "scripts" / "dev.sh")
    (tmp_path / "frontend" / "node_modules").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "fake_server.py").write_text(FAKE_SERVER)
    for tool in ("uv", "npm"):
        (bin_dir / tool).write_text(
            f"#!/bin/sh\nCRASH_AFTER={crash_after.get(tool, 0)} FAKE_TOOL={tool} "
            f'exec "{sys.executable}" "{tmp_path / "fake_server.py"}" "$@"\n'
        )
        (bin_dir / tool).chmod(0o755)
    api_port, web_port = free_ports(2)
    proc = PtyPopen(
        [BASH, str(tmp_path / "scripts" / "dev.sh"), "--api-port", str(api_port)]
        + ["--web-port", str(web_port)],
        cwd=cwd or tmp_path,
        env=dev_env()
        | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_ENV_LOG": str(tmp_path)}
        | (env or {}),
        text=True,
        start_new_session=True,
        **streams,
    )
    if tty:
        os.close(slave)
        proc.pty = master
    return proc


def run_fake(
    tmp_path: Path, crash_after: dict[str, int | str], cwd: Path | None = None, **env: str
) -> tuple[int, str, str]:
    """Run the copied dev.sh to its end: its exit code, its output, and what it left running."""
    proc = start_fake(tmp_path, crash_after, cwd, env=env)
    try:
        out, _ = proc.communicate(timeout=60)
        return proc.returncode, out, session_left(proc.pid)
    finally:
        kill_session(proc)


def seen_env(tmp_path: Path, tool: str) -> dict[str, str | None]:
    return json.loads((tmp_path / f"{tool}.json").read_text())


def test_ctrl_c_in_a_terminal_during_startup_stops_both(tmp_path: Path) -> None:
    # The API never answers, so the script is still waiting for it. Ctrl-C is typed into a real
    # terminal (a pty): the terminal signals its foreground process group, which must hold the
    # script, not only the curl or sleep it is running at that moment.
    proc = start_fake(tmp_path, {"uv": "hang"}, tty=True)
    try:
        deadline = time.monotonic() + 30
        while not all((tmp_path / f"{t}.json").exists() for t in ("uv", "npm")):
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(0.1)
        time.sleep(0.5)  # into the readiness loop
        os.write(proc.pty, b"\x03")
        assert proc.wait(timeout=30) == 130
        assert not session_left(proc.pid), "a process outlived Ctrl-C"
    finally:
        kill_session(proc)
        os.close(proc.pty)


def test_the_api_gets_no_key_and_an_absolute_fake_script(tmp_path: Path) -> None:
    # Run from a subdirectory with a relative script path; the API starts in the repo root.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _, output, _ = run_fake(
        tmp_path,
        {"npm": 1},
        cwd=elsewhere,
        ASSESS_FAKE_SCRIPT="scripts/demo.json",
        OPENROUTER_API_KEY="sk-or-fake-not-a-key",
    )
    assert "Vouch is running" in output
    api = seen_env(tmp_path, "uv")
    assert api["ASSESS_FAKE_SCRIPT"] == str(elsewhere / "scripts" / "demo.json")
    assert api["OPENROUTER_API_KEY"] is None
    vouch_api = seen_env(tmp_path, "npm")["VOUCH_API"]
    assert vouch_api is not None and vouch_api.startswith("http://127.0.0.1:")


def test_with_the_clerk_keys_loaded_and_no_auth_mode_the_api_runs_in_local_mode(
    tmp_path: Path,
) -> None:
    """.envrc loads the Clerk keys; a blank mode with keys set refuses to start (#151)."""
    _, output, _ = run_fake(tmp_path, {"npm": 1}, CLERK_SECRET_KEY="sk_test_fake_not_a_key")
    assert "Vouch is running" in output
    assert seen_env(tmp_path, "uv")["VOUCH_AUTH_MODE"] == "local"


def test_an_auth_mode_set_by_the_developer_is_kept(tmp_path: Path) -> None:
    run_fake(tmp_path, {"npm": 1}, VOUCH_AUTH_MODE="clerk")
    assert seen_env(tmp_path, "uv")["VOUCH_AUTH_MODE"] == "clerk"


def test_a_missing_tool_stops_with_a_clear_message(tmp_path: Path) -> None:
    # Only what the script needs before its tool check, and python3: no curl.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("dirname", "python3"):
        (bin_dir / tool).symlink_to(shutil.which(tool) or tool)
    result = run_dev("--api-port", "18001", "--web-port", "18002", path=str(bin_dir))
    assert result.returncode == 1
    assert "scripts/dev.sh needs curl on PATH." in result.stderr


def test_a_server_that_stops_stops_the_other(tmp_path: Path) -> None:
    # The front end answers the readiness probe once, then exits 3: the API must stop with it.
    code, output, left = run_fake(tmp_path, {"npm": 1})
    assert "Vouch is running" in output
    assert "A server stopped; stopping the other." in output
    assert code == 1
    assert not left, f"still running: {left}"


def test_a_server_that_fails_to_start_hints_the_port_was_just_taken(tmp_path: Path) -> None:
    # The API exits at once, as uvicorn does when its port was taken after the check.
    code, output, left = run_fake(tmp_path, {"uv": -1})
    assert "A server stopped while starting" in output
    assert "the port may have been taken just now" in output
    assert code == 1
    assert not left, f"still running: {left}"


def run_dev(*args: str, path: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dev_env()
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        [BASH, str(DEV), *args],
        check=False,
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )


def fake_servers(tmp_path: Path) -> tuple[str, Path]:
    """uv and npm that only log they were called: the run must stop before either starts."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    for tool in ("uv", "npm"):
        (bin_dir / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\n')
        (bin_dir / tool).chmod(0o755)
    return f"{bin_dir}:{os.environ['PATH']}", calls


@pytest.mark.parametrize(
    ("flag", "host"),
    [
        ("--api-port", "127.0.0.1"),
        ("--web-port", "127.0.0.1"),
        pytest.param(
            "--web-port",
            "::1",  # a Vite on `localhost` may listen on the IPv6 loopback only
            marks=pytest.mark.skipif(not HAS_IPV6, reason="no IPv6 loopback"),
        ),
    ],
)
def test_a_port_in_use_stops_with_a_clear_message(tmp_path: Path, flag: str, host: str) -> None:
    held, other = free_ports(2)
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_PORT, host, str(held)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout is not None
        ready, _, _ = select.select([holder.stdout], [], [], 10)
        assert ready, "the port holder didn't start within 10 s"
        assert holder.stdout.readline().strip() == "held"
        path, calls = fake_servers(tmp_path)
        other_flag = "--web-port" if flag == "--api-port" else "--api-port"
        result = run_dev(flag, str(held), other_flag, str(other), path=path)
    finally:
        holder.kill()
        holder.wait()

    assert result.returncode != 0
    assert f"Port {held} is already in use" in result.stderr
    assert f"{flag} <another port>" in result.stderr
    assert not calls.exists(), "a server started although a port was taken"


def test_a_bad_port_argument_stops_with_usage(tmp_path: Path) -> None:
    path, calls = fake_servers(tmp_path)
    result = run_dev("--api-port", "eighty", path=path)
    assert result.returncode == 2
    assert "Usage: scripts/dev.sh" in result.stderr
    assert not calls.exists()


def test_the_two_ports_must_differ(tmp_path: Path) -> None:
    (port,) = free_ports(1)
    path, calls = fake_servers(tmp_path)
    result = run_dev("--api-port", str(port), "--web-port", str(port), path=path)
    assert result.returncode == 2
    assert "must differ" in result.stderr
    assert not calls.exists()


# --- An owner preview that outlives its shell (#309) -------------------------------------------
# systemd-run, systemctl and curl are stand-ins that log their arguments; the real run (a shell
# that exits, both ports answering, the stop freeing them) is checked by hand: see the PR.


def detach_tree(tmp_path: Path, *, active: bool = True) -> tuple[Path, str, Path]:
    """dev.sh copied into a tree with frontend/node_modules, and stand-ins logging to calls.log."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(DEV, tmp_path / "scripts" / "dev.sh")
    (tmp_path / "frontend" / "node_modules").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    stand_ins = {
        "systemd-run": 'echo "systemd-run $*" >> "{calls}"; env > "{env}"',
        "systemctl": 'echo "systemctl $*" >> "{calls}"; exit {state}',
        "curl": "exit 0",
        "uv": 'echo "uv $*" >> "{calls}"',
        "npm": 'echo "npm $*" >> "{calls}"',
    }
    for tool, body in stand_ins.items():
        text = body.format(calls=calls, env=tmp_path / "env.log", state=0 if active else 3)
        (bin_dir / tool).write_text(f"#!/bin/sh\n{text}\n")
        (bin_dir / tool).chmod(0o755)
    return tmp_path / "scripts" / "dev.sh", f"{bin_dir}:{os.environ['PATH']}", calls


def run_script(script: Path, path: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(script), *args],
        check=False,
        cwd=script.parent.parent,
        env=dev_env() | {"PATH": path, "HOME": str(script.parent.parent)} | env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_detach_runs_the_preview_as_its_own_user_unit_with_no_keys(tmp_path: Path) -> None:
    script, path, calls = detach_tree(tmp_path)
    api, web = free_ports(2)

    done = run_script(
        script,
        path,
        "--api-port",
        str(api),
        "--web-port",
        str(web),
        "--detach",
        "m7",
        OPENROUTER_API_KEY="sk-or-v1-secret",
        VOUCH_DATABASE_URL="postgresql://live",
        ASSESS_FAKE_SCRIPT="demo.json",
    )

    assert done.returncode == 0, done.stderr
    [run] = [line for line in calls.read_text().splitlines() if line.startswith("systemd-run")]
    assert "--user" in run and "--collect" in run and "--unit=vouch-preview-m7" in run
    assert f"scripts/dev.sh --api-port {api} --web-port {web}" in run
    assert "--detach" not in run.split("bash", 1)[1]  # the unit runs the servers themselves
    assert "-u VOUCH_DATABASE_URL -u OPENROUTER_API_KEY" in run
    assert "sk-or-v1-secret" not in run and "postgresql://live" not in run
    assert f"--setenv=ASSESS_FAKE_SCRIPT={tmp_path}/demo.json" in run
    assert "uv " not in calls.read_text()  # nothing started in this shell's own group
    assert "scripts/dev.sh --stop m7" in done.stdout


def test_stop_stops_the_named_unit(tmp_path: Path) -> None:
    script, path, calls = detach_tree(tmp_path)

    done = run_script(script, path, "--stop", "m7")

    assert done.returncode == 0, done.stderr
    assert calls.read_text().splitlines() == ["systemctl --user stop vouch-preview-m7"]


def test_a_preview_that_dies_while_starting_says_so_and_is_stopped(tmp_path: Path) -> None:
    script, path, calls = detach_tree(tmp_path, active=False)
    (tmp_path / "bin" / "curl").write_text("#!/bin/sh\nexit 7\n")  # never answers
    api, web = free_ports(2)

    done = run_script(script, path, "--api-port", str(api), "--web-port", str(web), "--detach", "x")

    assert done.returncode == 1
    assert "didn't start" in done.stderr
    assert "systemctl --user stop vouch-preview-x" in calls.read_text()


@pytest.mark.parametrize("args", [["--detach", "a b"], ["--detach"], ["--stop", "../x"]])
def test_a_bad_preview_name_stops_with_usage(tmp_path: Path, args: list[str]) -> None:
    script, path, calls = detach_tree(tmp_path)

    done = run_script(script, path, *args)

    assert done.returncode == 2
    assert "Usage: scripts/dev.sh" in done.stderr
    assert not calls.exists()
