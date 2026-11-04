"""scripts/dev.sh: one command for the API and the Vite dev server, and Ctrl-C stops both.

The servers listen on spare loopback ports, never the owner's 8000 and 5173. This test process
opens no socket itself (pytest runs with --disable-socket): its child processes do the probing,
`curl` for HTTP and a short `python3` for a port's state, the way the script itself checks.
"""

import os
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
PORT_FREE = """
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try: s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError: sys.exit(1)
"""
HOLD_PORT = """
import socket, sys, time
s = socket.socket()
s.bind(("127.0.0.1", int(sys.argv[1])))
s.listen()
print("held", flush=True)
time.sleep(120)
"""


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
    log = (tmp_path / "dev.log").open("w")
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
    finally:
        # Whatever failed above, leave no server behind.
        subprocess.run(["pkill", "-KILL", "-s", str(proc.pid)], check=False)
        if proc.poll() is None:
            proc.kill()
            proc.wait()


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


@pytest.mark.parametrize("flag", ["--api-port", "--web-port"])
def test_a_port_in_use_stops_with_a_clear_message(tmp_path: Path, flag: str) -> None:
    held, other = free_ports(2)
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_PORT, str(held)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
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
