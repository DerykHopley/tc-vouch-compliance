"""The M0 retro's checks: verdict tokens only where docs/design.md allows them, a contrast helper
that computes rather than estimates, and a pre-push hook that installs a missing front end (M3 retro, #127)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(script, *args, cwd=ROOT, env=None):
    return subprocess.run(
        [sys.executable, "-I", "-B", str(ROOT / "scripts" / script), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


# token-roles.py ---------------------------------------------------------------------------------


def test_verdict_tokens_on_verdict_chips_and_card_edges_pass(tmp_path):
    (tmp_path / "ok.css").write_text(
        ".v-evidenced { color: var(--evidenced); background: var(--evidenced-bg); }\n"
        ".card-head.ev { border-bottom: 4px solid var(--evidenced); }\n"
        ".readiness-bar .seg { background: var(--partly-evidenced); }\n"
    )
    out = run("token-roles.py", str(tmp_path))
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_verdict_token_used_as_decoration_is_flagged_with_its_selector(tmp_path):
    (tmp_path / "bad.css").write_text(
        ".pill { background: var(--not-applicable-bg); }\n"
        ".notice {\n  border-left: 3px solid var(--not-evidenced);\n}\n"
    )
    out = run("token-roles.py", str(tmp_path))
    assert out.returncode == 1
    assert "bad.css:1 .pill uses --not-applicable-bg" in out.stdout
    assert "bad.css:3 .notice uses --not-evidenced" in out.stdout


def test_a_verdict_token_in_an_inline_style_is_flagged(tmp_path):
    (tmp_path / "Card.tsx").write_text(
        "export const C = () => <i style={{ color: 'var(--evidenced)' }} />\n"
    )
    out = run("token-roles.py", str(tmp_path))
    assert out.returncode == 1
    assert "Card.tsx:1" in out.stdout


def test_the_token_declarations_themselves_pass(tmp_path):
    (tmp_path / "tokens.css").write_text(
        ":root { --evidenced: #3F7A55; --evidenced-bg: #E2EDE5; }\n"
    )
    assert run("token-roles.py", str(tmp_path)).returncode == 0


def test_the_real_front_end_passes():
    out = run("token-roles.py", str(ROOT / "frontend" / "src"))
    assert out.returncode == 0, out.stdout


# contrast.py ------------------------------------------------------------------------------------


def test_contrast_prints_the_wcag_ratio():
    out = run("contrast.py", "#1B1D21", "#1B1D21")
    assert out.returncode == 0
    assert out.stdout.strip().startswith("1.00:1")
    assert run("contrast.py", "#000", "#fff").stdout.strip().startswith("21.00:1")


def test_contrast_against_a_floor_exits_1_below_it():
    below = run("contrast.py", "#3F7A55", "#E2EDE5", "--min", "4.5")
    assert below.returncode == 1 and "4.24:1" in below.stdout and "below 4.5" in below.stdout
    assert run("contrast.py", "#3B7350", "#E2EDE5", "--min", "4.5").returncode == 0


def test_contrast_refuses_a_value_that_is_not_hex():
    out = run("contrast.py", "red", "#fff")
    assert out.returncode == 2 and "hex" in out.stderr


# pre-push hook ----------------------------------------------------------------------------------


def _repo_copy(tmp_path, with_node_modules):
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    shutil.copy(ROOT / ".githooks" / "pre-push", repo / ".githooks" / "pre-push")
    (repo / "scripts").mkdir()
    for script in ("design-lint.py", "token-roles.py"):
        shutil.copy(ROOT / "scripts" / script, repo / "scripts" / script)
    (repo / "frontend" / "src").mkdir(parents=True)
    (repo / "frontend" / "package.json").write_text("{}")
    if with_node_modules:
        (repo / "frontend" / "node_modules").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def _fake_npm(tmp_path, ci_exit=0):
    """An npm that logs each call, and whose `ci` makes node_modules (or fails with ci_exit)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text(f"""#!/bin/bash
echo "$*" >> "{tmp_path}/npm.log"
case " $* " in *" ci "*) [ {ci_exit} -eq 0 ] || exit {ci_exit}; mkdir -p frontend/node_modules;; esac
""")
    npm.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}


def test_the_hook_installs_a_missing_front_end_then_checks(tmp_path):
    repo = _repo_copy(tmp_path, with_node_modules=False)
    out = subprocess.run(
        ["bash", ".githooks/pre-push"],
        check=False,
        capture_output=True,
        text=True,
        cwd=repo,
        env=_fake_npm(tmp_path),
    )
    assert out.returncode == 0, out.stdout + out.stderr
    calls = (tmp_path / "npm.log").read_text().splitlines()
    assert calls[0].startswith("--prefix frontend ci")
    assert calls[1] == "--prefix frontend run lint"
    assert "node_modules is missing" in out.stderr


def test_the_hook_stops_when_the_install_fails(tmp_path):
    repo = _repo_copy(tmp_path, with_node_modules=False)
    out = subprocess.run(
        ["bash", ".githooks/pre-push"],
        check=False,
        capture_output=True,
        text=True,
        cwd=repo,
        env=_fake_npm(tmp_path, ci_exit=3),
    )
    assert out.returncode != 0
    assert (tmp_path / "npm.log").read_text().splitlines() == [
        "--prefix frontend ci --no-audit --no-fund --loglevel=error"
    ]


def test_the_hook_skips_the_install_when_node_modules_exists(tmp_path):
    repo = _repo_copy(tmp_path, with_node_modules=True)
    out = subprocess.run(
        ["bash", ".githooks/pre-push"],
        check=False,
        capture_output=True,
        text=True,
        cwd=repo,
        env=_fake_npm(tmp_path),
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert (tmp_path / "npm.log").read_text().splitlines() == ["--prefix frontend run lint"]
