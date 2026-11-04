"""scripts/dev.sh: one command for the API and the Vite dev server, and Ctrl-C stops both.

The servers listen on spare loopback ports, never the owner's 8000 and 5173. This test process
opens no socket itself (pytest runs with --disable-socket): its child processes do the probing,
`curl` for HTTP and a short `python3` for a port's state, the way the script itself checks.
"""

import os
import select
import shutil
import signal
import subprocess
import sys
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
# A stand-in for `uv run uvicorn ...` or `npm run dev -- ...`: it answers /api/health on its
# --port. CRASH_AFTER=<n> makes it exit 3 after n answers (a negative n: at once); 0, never.
FAKE_SERVER = """
import http.server, os, sys
port = int(sys.argv[sys.argv.index("--port") + 1])
left = int(os.environ.get("CRASH_AFTER", "0")) or None
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
            env=dev_env(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # its session holds every server, for the clean-up below
        )
    url = f"http://127.0.0.1:{web_port}/"
    try:
        deadline = time.monotonic() + 90
        while f"Vouch is running: {url}" not in (tmp_path / "dev.log").read_text():
            assert proc.poll() is None, (tmp_path / "dev.log").read_text()
            assert time.monotonic() < deadline, (tmp_path / "dev.log").read_text()
            time.sleep(0.5)

        code, body = fetch(url)
        assert code == 200 and '<div id="root">' in body
        code, body = fetch(url + "api/health")
        assert code == 200 and '"status":"ok"' in body.replace(" ", "")

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


def fake_checkout(tmp_path: Path, crash_after: dict[str, int]) -> tuple[Path, dict[str, str]]:
    """A copy of dev.sh in a tree with frontend/node_modules, and fake servers for uv and npm.

    The run gets past its deps check without a real npm ci, so these tests run in the fast lane.
    """
    (tmp_path / "scripts").mkdir()
    shutil.copy(DEV, tmp_path / "scripts" / "dev.sh")
    (tmp_path / "frontend" / "node_modules").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "fake_server.py").write_text(FAKE_SERVER)
    for tool in ("uv", "npm"):
        (bin_dir / tool).write_text(
            f"#!/bin/sh\nCRASH_AFTER={crash_after.get(tool, 0)} "
            f'exec "{sys.executable}" "{tmp_path / "fake_server.py"}" "$@"\n'
        )
        (bin_dir / tool).chmod(0o755)
    return tmp_path / "scripts" / "dev.sh", dev_env() | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def run_fake(tmp_path: Path, crash_after: dict[str, int]) -> tuple[int, str, str]:
    """Run the copied dev.sh to its end: its exit code, its output, and what it left running."""
    script, env = fake_checkout(tmp_path, crash_after)
    api_port, web_port = free_ports(2)
    proc = subprocess.Popen(
        [BASH, str(script), "--api-port", str(api_port), "--web-port", str(web_port)],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=60)
        return proc.returncode, out, session_left(proc.pid)
    finally:
        kill_session(proc)


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
