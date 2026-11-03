"""Hidden acceptance suite for the sprint3 process trial (docs/trial-spec.md).

Black-box only: it uses just the interfaces the tickets name (the `assess` CLI,
ASSESS_FAKE_SCRIPT, ASSESS_POLICY_DIR, `from audit_agent import search_policies`)
and tests just the behaviour they state. Run it from a trial repo's root; see README.md.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(os.environ.get("TRIAL_REPO", os.getcwd())).resolve()
POLICY_DOCS = {"encryption-policy", "access-control-policy", "acceptable-use-policy", "backup-policy"}
MISSING_DIR = "/nonexistent/sprint3-trial/policies"


def _env(**extra):
    """No API key, no inherited trial switches, and any HTTP call goes to a dead proxy."""
    drop = {"OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ASSESS_FAKE_SCRIPT", "ASSESS_POLICY_DIR"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    dead = "http://127.0.0.1:9"
    env.update(HTTP_PROXY=dead, HTTPS_PROXY=dead, http_proxy=dead, https_proxy=dead, NO_PROXY="", no_proxy="")
    env.update(extra)
    return env


def _assess_cmd():
    for exe in (Path(sys.executable).parent / "assess", REPO / ".venv" / "bin" / "assess"):
        if exe.exists():
            return [str(exe)]
    return ["uv", "run", "--project", str(REPO), "assess"]


@pytest.fixture
def assess(tmp_path):
    """Run `assess <control_id>`, optionally with a fake script; returns the CompletedProcess."""
    def run(control_id, script=None, **env):
        if script is not None:
            path = tmp_path / "fake-script.json"
            path.write_text(json.dumps(script))
            env["ASSESS_FAKE_SCRIPT"] = str(path)
        return subprocess.run(_assess_cmd() + [control_id], cwd=REPO, env=_env(**env),
                              capture_output=True, text=True, timeout=180)
    return run


def assessment(proc):
    assert proc.returncode == 0, f"exit {proc.returncode}\n--- stderr ---\n{proc.stderr[-3000:]}"
    return json.loads(proc.stdout)


_SEARCH = r"""
import dataclasses, json, sys
from audit_agent import search_policies

def plain(x):
    if hasattr(x, "model_dump"):
        return x.model_dump()
    if dataclasses.is_dataclass(x):
        return dataclasses.asdict(x)
    if isinstance(x, dict):
        return {k: plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [plain(v) for v in x]
    return x

args = json.loads(sys.argv[1])
result = search_policies(*args["args"], **args["kwargs"])
print(json.dumps(plain(result)))
"""


def search(*args, policy_dir=None, **kwargs):
    """Call search_policies in a fresh interpreter, so ASSESS_POLICY_DIR is read however the code reads it."""
    env = _env(**({"ASSESS_POLICY_DIR": policy_dir} if policy_dir else {}))
    proc = subprocess.run([sys.executable, "-c", _SEARCH, json.dumps({"args": args, "kwargs": kwargs})],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"search_policies raised:\n{proc.stderr[-3000:]}"
    return json.loads(proc.stdout)


def is_error(result, code):
    return isinstance(result, dict) and result.get("error") == code


def doc_ids(sources):
    return {s["doc_id"] for s in sources}
