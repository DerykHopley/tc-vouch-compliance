"""Settings from the environment, and the user every request has (#31)."""

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient

from vouch.api import create_app, current_user
from vouch.settings import DEFAULT_USER, ENV_NAMES, SettingsError, User, load_settings

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"


def client_seeing_the_user(env: dict[str, str]) -> TestClient:
    """The app on `env`'s settings, plus a route that answers with the request's user."""
    app = create_app(settings=load_settings(env))

    @app.get("/whoami")
    def whoami(user: Annotated[User, Depends(current_user)]) -> dict[str, str]:
        return {"id": user.id}

    return TestClient(app)


def test_with_nothing_set_a_request_sees_the_default_user():
    response = client_seeing_the_user({}).get("/whoami")

    assert response.status_code == 200
    assert response.json() == {"id": DEFAULT_USER.id}
    assert DEFAULT_USER.id == "local-user"


def _public_key() -> str:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return pem.decode()


CLERK_KEYS = {
    "CLERK_SECRET_KEY": "sk_test_marker",
    "CLERK_PUBLISHABLE_KEY": "pk_test_marker",
    "CLERK_JWT_KEY": _public_key(),
    "CLERK_AUTHORIZED_PARTIES": "http://localhost:5173",
}
"""Everything clerk mode needs: the three keys, and the origins (not a secret)."""


@pytest.mark.parametrize("missing", sorted(CLERK_KEYS))
def test_clerk_mode_without_a_key_refuses_naming_it(missing):
    env = {"VOUCH_AUTH_MODE": "clerk"} | {k: v for k, v in CLERK_KEYS.items() if k != missing}

    with pytest.raises(SettingsError) as refused:
        load_settings(env)

    assert [name for name in CLERK_KEYS if name in str(refused.value)] == [missing]


def test_clerk_mode_with_its_keys_starts_and_refuses_a_request_without_sign_in():
    client = client_seeing_the_user({"VOUCH_AUTH_MODE": "clerk"} | CLERK_KEYS)

    assert client.get("/whoami").status_code == 401
    assert client.post("/api/assessments", json={"control_id": "NK-ENC-01"}).status_code == 401
    assert client.get("/api/health").status_code == 200


def test_the_authorised_origins_are_read_as_a_list():
    env = {"VOUCH_AUTH_MODE": "clerk"} | CLERK_KEYS
    env["CLERK_AUTHORIZED_PARTIES"] = " https://vouch.example.com , http://localhost:8000,"

    clerk = load_settings(env).clerk

    assert clerk is not None
    assert clerk.authorized_parties == ("https://vouch.example.com", "http://localhost:8000")


@pytest.mark.parametrize(
    "origins",
    [",", "vouch.example.com", "https://vouch.example.com/", "https://x.example/app", "*"],
    ids=["only-commas", "no-scheme", "trailing-slash", "a-path", "wildcard"],
)
def test_origins_that_are_not_origins_refuse_naming_the_setting(origins):
    env = {"VOUCH_AUTH_MODE": "clerk"} | CLERK_KEYS | {"CLERK_AUTHORIZED_PARTIES": origins}

    with pytest.raises(SettingsError, match="CLERK_AUTHORIZED_PARTIES"):
        load_settings(env)


def test_an_unknown_auth_mode_refuses_naming_the_setting():
    with pytest.raises(SettingsError, match="VOUCH_AUTH_MODE"):
        load_settings({"VOUCH_AUTH_MODE": "none"})


def names_in(path: Path) -> set[str]:
    """The NAME of each `NAME=` or `export NAME=` line, comments left out."""
    found = re.findall(r"^(?:export )?([A-Z][A-Z0-9_]*)=", path.read_text(), flags=re.MULTILINE)
    return set(found)


def test_env_example_lists_every_setting_the_module_reads():
    assert names_in(ROOT / ".env.example") == ENV_NAMES


def test_envrc_holds_only_keyring_lookups():
    lines = [
        line
        for line in (ROOT / ".envrc").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert lines
    for line in lines:
        assert re.fullmatch(r"export [A-Z_]+=\$\(secret-tool lookup service [a-z0-9-]+\)", line)
    assert names_in(ROOT / ".envrc") <= ENV_NAMES


def test_a_secret_value_never_reaches_a_log_or_an_error(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    marker = "s3cr3t-marker-31"
    # The publishable key is public: Clerk's front end sends it to every browser.
    secrets = {
        name: f"{marker}-{name}"
        for name in ("CLERK_SECRET_KEY", "CLERK_JWT_KEY", "OPENROUTER_API_KEY")
    }
    env = {
        "VOUCH_AUTH_MODE": "clerk",
        "CLERK_PUBLISHABLE_KEY": "pk_test_public",
        "CLERK_AUTHORIZED_PARTIES": "http://localhost:5173",
    } | secrets
    seen = []

    settings = load_settings(env)
    seen += [repr(settings), str(settings)]
    for missing in CLERK_KEYS:  # every refusal, with the other keys set
        with pytest.raises(SettingsError) as refused:
            load_settings({k: v for k, v in env.items() if k != missing})
        seen.append(str(refused.value))
    # A failing assessment logs its exception; the secrets are in the environment meanwhile.
    # Local mode, as clerk mode refuses the request before it runs (the Clerk keys' refusals
    # and printed forms are covered above).
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ASSESS_FAKE_SCRIPT", str(ROOT / "tests" / "scripts" / "broken.json"))
    client = TestClient(create_app(settings=load_settings()), raise_server_exceptions=False)
    seen.append(client.post("/api/assessments", json={"control_id": "NK-ENC-01"}).text)
    seen.append(caplog.text)

    assert "assessment of NK-ENC-01 failed" in caplog.text  # the log was written
    assert all(marker not in text for text in seen)


def test_the_image_runs_in_clerk_mode():
    assert re.search(r"^ENV .*\bVOUCH_AUTH_MODE=clerk\b", DOCKERFILE.read_text(), re.MULTILINE)


def test_started_in_clerk_mode_without_its_keys_the_app_exits_naming_them():
    env = {k: v for k, v in os.environ.items() if k not in ENV_NAMES} | {"VOUCH_AUTH_MODE": "clerk"}

    started = subprocess.run(
        [sys.executable, "-c", "import vouch.api"],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert started.returncode != 0
    assert "Vouch cannot start" in started.stderr
    assert all(name in started.stderr for name in CLERK_KEYS)
    assert "Traceback" not in started.stderr


def test_a_blank_clerk_key_counts_as_missing():
    env = {"VOUCH_AUTH_MODE": "clerk"} | CLERK_KEYS | {"CLERK_JWT_KEY": "   "}

    with pytest.raises(SettingsError, match="CLERK_JWT_KEY"):
        load_settings(env)


def test_the_tests_start_from_no_vouch_settings():
    """tests/conftest.py: a developer's exported settings can't change the tests' results."""
    assert not ENV_NAMES & set(os.environ)
