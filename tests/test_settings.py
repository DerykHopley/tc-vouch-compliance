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
from vouch.settings import DEFAULT_USER, ENV_NAMES, Build, SettingsError, User, load_settings

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


@pytest.mark.parametrize("key", ["CLERK_SECRET_KEY", "CLERK_PUBLISHABLE_KEY", "CLERK_JWT_KEY"])
@pytest.mark.parametrize("mode", [None, "", "  "])
def test_a_clerk_key_with_a_blank_auth_mode_refuses_naming_the_auth_mode(key, mode):
    """A key set but the mode forgotten is a deploy gone wrong, not local mode (#151)."""
    env = {key: CLERK_KEYS[key]} | ({} if mode is None else {"VOUCH_AUTH_MODE": mode})

    with pytest.raises(SettingsError, match="VOUCH_AUTH_MODE") as refused:
        load_settings(env)

    assert key in str(refused.value)
    assert CLERK_KEYS[key][:12] not in str(refused.value)  # names the setting, never its value


def test_clerk_keys_with_auth_mode_local_start_in_local_mode():
    """Explicit local with keys set is a developer's choice (#151)."""
    settings = load_settings({"VOUCH_AUTH_MODE": "local"} | CLERK_KEYS)

    assert settings.auth_mode == "local"
    assert settings.clerk is None


def test_clerk_mode_with_its_keys_starts_and_refuses_a_request_without_sign_in():
    client = client_seeing_the_user({"VOUCH_AUTH_MODE": "clerk"} | CLERK_KEYS)

    assert client.get("/whoami").status_code == 401
    assert client.post("/api/assessments", json={"control_id": "4.1.2"}).status_code == 401
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
    monkeypatch.setenv("VOUCH_AUTH_MODE", "local")  # keys and a blank mode refuse to start (#151)
    monkeypatch.setenv("ASSESS_FAKE_SCRIPT", str(ROOT / "tests" / "scripts" / "broken.json"))
    client = TestClient(create_app(settings=load_settings()), raise_server_exceptions=False)
    seen.append(client.post("/api/assessments", json={"control_id": "4.1.2"}).text)
    seen.append(caplog.text)

    assert "assessment of 4.1.2 failed" in caplog.text  # the log was written
    assert all(marker not in text for text in seen)


def test_the_image_runs_in_clerk_mode():
    assert re.search(r"^ENV .*\bVOUCH_AUTH_MODE=clerk\b", DOCKERFILE.read_text(), re.MULTILINE)


def refused_start(settings: dict[str, str]) -> str:
    """The app started in a fresh process with only `settings` set: its stderr, once it refused."""
    env = {k: v for k, v in os.environ.items() if k not in ENV_NAMES} | settings
    started = subprocess.run(
        [sys.executable, "-c", "import vouch.api"],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    assert started.returncode != 0
    assert "Vouch cannot start" in started.stderr
    assert "Traceback" not in started.stderr
    return started.stderr


def test_started_in_clerk_mode_without_its_keys_the_app_exits_naming_them():
    stderr = refused_start({"VOUCH_AUTH_MODE": "clerk"})

    assert all(name in stderr for name in CLERK_KEYS)


def test_a_blank_clerk_key_counts_as_missing():
    env = {"VOUCH_AUTH_MODE": "clerk"} | CLERK_KEYS | {"CLERK_JWT_KEY": "   "}

    with pytest.raises(SettingsError, match="CLERK_JWT_KEY"):
        load_settings(env)


def test_the_tests_start_from_no_vouch_settings():
    """tests/conftest.py: a developer's exported settings can't change the tests' results."""
    assert not ENV_NAMES & set(os.environ)


def test_local_mode_names_its_user_local_user_unless_a_setting_names_another():
    assert load_settings({}).local_user_name == "Local user"
    assert load_settings({"VOUCH_LOCAL_USER_NAME": "  "}).local_user_name == "Local user"
    named = load_settings({"VOUCH_LOCAL_USER_NAME": " Dana Lead "})
    assert named.local_user_name == "Dana Lead"


def test_with_no_rate_setting_the_rate_is_60_requests_a_minute():
    assert load_settings({}).rate_requests == 60


def test_the_rate_setting_is_read_as_requests_a_minute():
    assert load_settings({"VOUCH_RATE_REQUESTS": " 5000 "}).rate_requests == 5000


@pytest.mark.parametrize("value", ["lots", "0", "-3", "1.5", "60/min"])
def test_a_rate_setting_that_isnt_a_whole_number_above_zero_refuses_naming_it(value):
    with pytest.raises(SettingsError, match="VOUCH_RATE_REQUESTS"):
        load_settings({"VOUCH_RATE_REQUESTS": value})


def test_started_with_a_bad_rate_setting_the_app_exits_naming_it():
    assert "VOUCH_RATE_REQUESTS" in refused_start({"VOUCH_RATE_REQUESTS": "x"})


def test_the_profile_pass_threshold_is_100000_tokens_unless_a_setting_names_another():
    assert load_settings({}).profile_pass_tokens == 100_000
    assert load_settings({"VOUCH_PROFILE_PASS_TOKENS": " 12000 "}).profile_pass_tokens == 12_000


@pytest.mark.parametrize("value", ["0", "lots", "-1", "1.5", "50k"])
def test_a_profile_pass_threshold_that_isnt_a_whole_number_above_zero_refuses_naming_it(value):
    with pytest.raises(SettingsError, match="VOUCH_PROFILE_PASS_TOKENS must be a whole number"):
        load_settings({"VOUCH_PROFILE_PASS_TOKENS": value})


def test_in_clerk_mode_the_discord_server_id_is_read_as_a_plain_setting():
    env = {"VOUCH_AUTH_MODE": "clerk"} | CLERK_KEYS
    gated = load_settings(env | {"DISCORD_SERVER_ID": " 111111111111111111 "})

    assert gated.clerk is not None and gated.clerk.discord_server_id == "111111111111111111"
    assert load_settings(env).clerk.discord_server_id is None  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "server", ["course", "12", "1" * 21, "11111111111111111a", "-1111111111111111"]
)
def test_a_discord_server_id_that_isnt_one_refuses_naming_it(server):
    env = {"VOUCH_AUTH_MODE": "clerk", "DISCORD_SERVER_ID": server} | CLERK_KEYS

    with pytest.raises(SettingsError, match="DISCORD_SERVER_ID"):
        load_settings(env)


# --- The running build (#580) ------------------------------------------------------------------


def test_the_build_is_dev_with_no_revision_when_nothing_is_baked_or_set():
    assert load_settings({}).build == Build(commit="dev", revision=None)
    assert load_settings({"VOUCH_COMMIT": " ", "K_REVISION": ""}).build == Build("dev", None)


def test_the_build_names_the_baked_commit_and_cloud_run_s_revision_each_when_set():
    assert load_settings({"VOUCH_COMMIT": "1a2b3c4"}).build == Build("1a2b3c4", None)
    assert load_settings({"K_REVISION": "vouch-00029"}).build == Build("dev", "vouch-00029")
    both = load_settings({"VOUCH_COMMIT": " 1a2b3c4 ", "K_REVISION": "vouch-00029"})
    assert both.build == Build("1a2b3c4", "vouch-00029")


def test_the_image_bakes_the_commit_it_was_built_from_into_vouch_commit():
    """The build arg becomes the env var load_settings reads, in the stage that runs."""
    final_stage = DOCKERFILE.read_text().rsplit("\nFROM ", 1)[1]
    assert re.search(r"^ARG VOUCH_COMMIT=dev$", final_stage, re.MULTILINE)
    assert re.search(r"^ENV .*\bVOUCH_COMMIT=\$VOUCH_COMMIT\b", final_stage, re.MULTILINE)
