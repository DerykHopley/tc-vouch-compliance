"""Every console script starts with only the environment it documents (#237).

Two tools refused to start because they loaded the app's full settings, sign-in included:
`vouch-db-setup` (#190) and the eval CLI (#173). Each tool here runs in-process with only its
documented variables (dummy values), plus the Clerk keys set and no VOUCH_AUTH_MODE (#151's trap),
and must reach its first real action past settings, which is stubbed: nothing connects, nothing
is paid for, and sockets stay off as in the fast lane.
"""

import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from vouch import cli, database, evals

ROOT = Path(__file__).resolve().parents[1]
CLERK_KEYS = {
    "CLERK_SECRET_KEY": "sk_test_dummy",
    "CLERK_PUBLISHABLE_KEY": "pk_test_dummy",
    "CLERK_JWT_KEY": "-----BEGIN PUBLIC KEY-----\ndummy\n-----END PUBLIC KEY-----",
}


class Reached(BaseException):
    """The tool reached its first real action, past settings. A BaseException, so a tool's
    catch-all `except Exception` (assess has one) doesn't swallow it."""


def reached(*args, **kwargs):
    raise Reached


@dataclass(frozen=True)
class Tool:
    main: Callable[[list[str]], int]
    argv: list[str]
    documented: dict[str, str]  # the variables it reads, with dummy values
    first_action: tuple[object, str]  # what it calls once past settings: stubbed


TOOLS = {
    "assess": Tool(
        cli.main,
        ["4.1.2"],
        {"OPENROUTER_API_KEY": "sk-or-dummy", "ASSESS_MODEL": "dummy/model"},
        (cli, "load_named_profile"),
    ),
    "vouch-db-setup": Tool(
        database.main,
        [],
        {"VOUCH_DATABASE_URL": "postgresql://dummy:dummy@localhost:1/dummy"},
        (database, "Database"),
    ),
    "python -m vouch.evals": Tool(
        evals.main,
        ["--model", "fake"],
        {"OPENROUTER_API_KEY": "sk-or-dummy"},
        (evals, "load_named_profile"),
    ),
}


def project_scripts() -> set[str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return set(pyproject["project"]["scripts"])


def test_every_console_script_has_a_row():
    missing = project_scripts() - set(TOOLS)
    assert not missing, f"no row in TOOLS for: {', '.join(sorted(missing))}"


KNOWN_TRAPS: dict[str, str] = {}
"""Tools that still fall into the trap, each with its Fix: strict, so the row fails once fixed."""


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(n, marks=pytest.mark.xfail(strict=True, reason=KNOWN_TRAPS[n]))
        if n in KNOWN_TRAPS
        else n
        for n in sorted(TOOLS)
    ],
)
def test_the_tool_gets_past_settings_with_only_its_documented_environment(
    name, monkeypatch, capsys
):
    tool = TOOLS[name]
    for key in list(os.environ):
        if key not in {"PATH", "HOME"}:
            monkeypatch.delenv(key)
    for key, value in {**tool.documented, **CLERK_KEYS}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(*tool.first_action, reached)

    with pytest.raises(Reached):
        exit_code = tool.main(tool.argv)
        pytest.fail(f"{name} exited {exit_code} before its first action: {capsys.readouterr().err}")
