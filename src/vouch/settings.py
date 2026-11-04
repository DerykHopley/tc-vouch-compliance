"""Every setting Vouch reads from the environment; nothing else reads the environment.

Locally the committed `.envrc` fills the secrets from the keyring; on Cloud Run, Secret Manager
does. `.env.example` lists every name in ENV_NAMES (checked by tests/test_settings.py).

Auth mode `local`, the default, is for a developer's machine: no sign-in, one default user. The
container sets `clerk` (Dockerfile), and then the Clerk keys are required.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args, overload

from pydantic import SecretStr

if TYPE_CHECKING:  # imported when called: the model's modules read settings themselves
    from vouch.fake_model import ScriptedChatModel

AuthMode = Literal["local", "clerk"]

AUTH_MODE_ENV = "VOUCH_AUTH_MODE"
CLERK_SECRET_KEY_ENV = "CLERK_SECRET_KEY"
CLERK_PUBLISHABLE_KEY_ENV = "CLERK_PUBLISHABLE_KEY"
CLERK_JWT_KEY_ENV = "CLERK_JWT_KEY"
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
MODEL_ENV = "ASSESS_MODEL"
FAKE_SCRIPT_ENV = "ASSESS_FAKE_SCRIPT"
POLICY_DIR_ENV = "ASSESS_POLICY_DIR"
FRONTEND_ENV = "VOUCH_FRONTEND"

CLERK_KEY_ENVS = (CLERK_SECRET_KEY_ENV, CLERK_PUBLISHABLE_KEY_ENV, CLERK_JWT_KEY_ENV)
ENV_NAMES = frozenset(
    {AUTH_MODE_ENV, *CLERK_KEY_ENVS, OPENROUTER_API_KEY_ENV, MODEL_ENV, FAKE_SCRIPT_ENV}
    | {POLICY_DIR_ENV, FRONTEND_ENV}
)

DEMO_SCRIPT = Path(__file__).parent / "demo" / "demo.json"


class SettingsError(RuntimeError):
    """A setting is missing or wrong. The message names settings, never their values."""


@dataclass(frozen=True)
class User:
    """Who a request is for; their id scopes what they store."""

    id: str


DEFAULT_USER = User(id="local-user")
"""Local mode's one user: no sign-in on a developer's machine."""


@dataclass(frozen=True)
class ClerkKeys:
    secret_key: SecretStr
    publishable_key: str
    jwt_key: SecretStr


@dataclass(frozen=True)
class Settings:
    """Secrets are SecretStr: printed or logged, they show as '**********'."""

    auth_mode: AuthMode = "local"
    clerk: ClerkKeys | None = None
    openrouter_api_key: SecretStr | None = None
    model: str | None = None
    fake_script: Path | None = None
    policy_dir: Path | None = None
    frontend: Path | None = None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """The settings in `env` (the process environment by default). Raises SettingsError."""
    env = os.environ if env is None else env

    def read(name: str) -> str | None:
        return env.get(name) or None  # set but empty is unset, as `.envrc` leaves a missing key

    auth_mode = read(AUTH_MODE_ENV) or "local"
    if auth_mode not in get_args(AuthMode):
        raise SettingsError(f"{AUTH_MODE_ENV} must be one of: {', '.join(get_args(AuthMode))}")
    clerk = None
    if auth_mode == "clerk":
        missing = [name for name in CLERK_KEY_ENVS if not read(name)]
        if missing:
            raise SettingsError(f"{AUTH_MODE_ENV}=clerk needs {', '.join(missing)}, not set")
        clerk = ClerkKeys(
            secret_key=SecretStr(env[CLERK_SECRET_KEY_ENV]),
            publishable_key=env[CLERK_PUBLISHABLE_KEY_ENV],
            jwt_key=SecretStr(env[CLERK_JWT_KEY_ENV]),
        )
    api_key = read(OPENROUTER_API_KEY_ENV)
    fake_script, policy_dir, frontend = (
        read(n) for n in (FAKE_SCRIPT_ENV, POLICY_DIR_ENV, FRONTEND_ENV)
    )
    return Settings(
        auth_mode=auth_mode,  # type: ignore[arg-type]  # checked against AuthMode above
        clerk=clerk,
        openrouter_api_key=SecretStr(api_key) if api_key else None,
        model=read(MODEL_ENV),
        fake_script=Path(fake_script) if fake_script else None,
        policy_dir=Path(policy_dir) if policy_dir else None,
        frontend=Path(frontend) if frontend else None,
    )


@overload
def fake_model_from_env(default: Path) -> "ScriptedChatModel": ...
@overload
def fake_model_from_env(default: None = None) -> "ScriptedChatModel | None": ...
def fake_model_from_env(default: Path | None = None) -> "ScriptedChatModel | None":
    """The scripted fake model playing ASSESS_FAKE_SCRIPT's script, else `default`'s.

    None when neither names a script. A bad script raises ScriptError.
    """
    from vouch.fake_model import ScriptedChatModel

    script = load_settings().fake_script or default
    return ScriptedChatModel.from_file(script) if script else None
