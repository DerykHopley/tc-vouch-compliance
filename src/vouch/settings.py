"""Every setting Vouch reads from the environment; nothing else reads the environment.

Locally the committed `.envrc` fills the secrets from the keyring; on Cloud Run, Secret Manager
does. `.env.example` lists every name in ENV_NAMES (checked by tests/test_settings.py).

Auth mode `local`, the default, is for a developer's machine: no sign-in, one default user. The
container sets `clerk` (Dockerfile), and then the Clerk keys and CLERK_AUTHORIZED_PARTIES (the
origins Vouch's pages are served from; not secret, so not in `.envrc`) are required.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast, get_args, overload

from pydantic import SecretStr

from vouch.limits import RATE_REQUESTS

if TYPE_CHECKING:  # imported when called: the model's modules read settings themselves
    from vouch.fake_model import ScriptedChatModel

AuthMode = Literal["local", "clerk"]

AUTH_MODE_ENV = "VOUCH_AUTH_MODE"
CLERK_SECRET_KEY_ENV = "CLERK_SECRET_KEY"
CLERK_PUBLISHABLE_KEY_ENV = "CLERK_PUBLISHABLE_KEY"
CLERK_JWT_KEY_ENV = "CLERK_JWT_KEY"
CLERK_AUTHORIZED_PARTIES_ENV = "CLERK_AUTHORIZED_PARTIES"
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
MODEL_ENV = "ASSESS_MODEL"
FAKE_SCRIPT_ENV = "ASSESS_FAKE_SCRIPT"
FRONTEND_ENV = "VOUCH_FRONTEND"
DATABASE_URL_ENV = "VOUCH_DATABASE_URL"
RATE_REQUESTS_ENV = "VOUCH_RATE_REQUESTS"

CLERK_KEY_ENVS = (CLERK_SECRET_KEY_ENV, CLERK_PUBLISHABLE_KEY_ENV, CLERK_JWT_KEY_ENV)
CLERK_ENVS = (*CLERK_KEY_ENVS, CLERK_AUTHORIZED_PARTIES_ENV)
"""What clerk mode needs: the three keys, and the origins its pages are served from (not secret)."""
ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?")
ENV_NAMES = frozenset(
    {AUTH_MODE_ENV, *CLERK_ENVS, OPENROUTER_API_KEY_ENV, MODEL_ENV, FAKE_SCRIPT_ENV}
    | {FRONTEND_ENV}
    | {DATABASE_URL_ENV}
    | {RATE_REQUESTS_ENV}
)

DEMO_SCRIPT = Path(__file__).parent / "demo" / "demo.json"


class SettingsError(RuntimeError):
    """A setting is missing or wrong. The message names settings, never their values."""


@dataclass(frozen=True)
class User:
    """Who a request is for; their id scopes what they store (GLOSSARY.md, "User")."""

    id: str


DEFAULT_USER = User(id="local-user")
"""Local mode's one user: no sign-in on a developer's machine."""


@dataclass(frozen=True)
class ClerkKeys:
    """Clerk mode's settings: the keys, and the origins a session token may come from (`azp`)."""

    secret_key: SecretStr
    publishable_key: str
    jwt_key: SecretStr
    authorized_parties: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    """Secrets are SecretStr: printed or logged, they show as '**********'."""

    auth_mode: AuthMode = "local"
    clerk: ClerkKeys | None = None
    openrouter_api_key: SecretStr | None = None
    model: str | None = None
    fake_script: Path | None = None
    frontend: Path | None = None
    database_url: SecretStr | None = None
    """Postgres; it holds the password. None: no database, and its routes answer 503."""
    rate_requests: int = RATE_REQUESTS
    """A user's requests a minute before 429: VOUCH_RATE_REQUESTS, else vouch.limits's 60."""


def _read(env: Mapping[str, str], name: str) -> str | None:
    """`name`'s value; set but blank is unset, as `.envrc` leaves a key missing from the keyring."""
    return (env.get(name) or "").strip() or None


def _read_path(env: Mapping[str, str], name: str) -> Path | None:
    value = _read(env, name)
    return Path(value) if value else None


def _origins(value: str) -> tuple[str, ...]:
    """Comma-separated origins (scheme and host, a port if any; no path). Raises SettingsError."""
    origins = tuple(part.strip() for part in value.split(",") if part.strip())
    if not origins or not all(ORIGIN.fullmatch(origin) for origin in origins):
        raise SettingsError(
            f"{CLERK_AUTHORIZED_PARTIES_ENV} must be comma-separated origins, such as "
            "https://vouch.example.com,http://localhost:5173 (no path, no trailing /)"
        )
    return origins


def _rate_requests(value: str | None) -> int:
    """Requests a minute: a whole number above 0, else RATE_REQUESTS. Raises SettingsError."""
    if value is None:
        return RATE_REQUESTS
    if not (value.isascii() and value.isdigit()) or int(value) < 1:
        raise SettingsError(f"{RATE_REQUESTS_ENV} must be a whole number of requests above 0")
    return int(value)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """The settings in `env` (the process environment by default). Raises SettingsError."""
    env = os.environ if env is None else env

    def read(name: str) -> str | None:
        return _read(env, name)

    def read_path(name: str) -> Path | None:
        return _read_path(env, name)

    auth_mode = read(AUTH_MODE_ENV) or "local"
    if auth_mode not in get_args(AuthMode):
        raise SettingsError(f"{AUTH_MODE_ENV} must be one of: {', '.join(get_args(AuthMode))}")
    clerk = None
    if auth_mode == "clerk":
        missing = [name for name in CLERK_ENVS if not read(name)]
        if missing:
            raise SettingsError(f"{AUTH_MODE_ENV}=clerk needs {', '.join(missing)}, not set")
        clerk = ClerkKeys(
            secret_key=SecretStr(env[CLERK_SECRET_KEY_ENV].strip()),
            publishable_key=env[CLERK_PUBLISHABLE_KEY_ENV].strip(),
            jwt_key=SecretStr(env[CLERK_JWT_KEY_ENV].strip()),
            authorized_parties=_origins(env[CLERK_AUTHORIZED_PARTIES_ENV]),
        )
    api_key = read(OPENROUTER_API_KEY_ENV)
    database_url = read(DATABASE_URL_ENV)
    return Settings(
        auth_mode=cast(AuthMode, auth_mode),  # checked against AuthMode above
        clerk=clerk,
        openrouter_api_key=SecretStr(api_key) if api_key else None,
        model=read(MODEL_ENV),
        fake_script=read_path(FAKE_SCRIPT_ENV),
        frontend=read_path(FRONTEND_ENV),
        database_url=SecretStr(database_url) if database_url else None,
        rate_requests=_rate_requests(read(RATE_REQUESTS_ENV)),
    )


@overload
def fake_model(settings: Settings, default: Path) -> "ScriptedChatModel": ...
@overload
def fake_model(settings: Settings, default: None = None) -> "ScriptedChatModel | None": ...
def fake_model(settings: Settings, default: Path | None = None) -> "ScriptedChatModel | None":
    """The scripted fake model playing `settings.fake_script`, else `default`'s script.

    None when neither names a script. A bad script raises ScriptError.
    """
    from vouch.fake_model import ScriptedChatModel

    script = settings.fake_script or default
    return ScriptedChatModel.from_file(script) if script else None
