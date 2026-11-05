"""Sign-in in clerk mode: who a request is for, and their role.

The request must carry a Clerk session token in `Authorization: Bearer ...` (the front end gets
a fresh one per call; they live 60 s). clerk-backend-api's `authenticate_request` checks it with
the instance's JWT public key (CLERK_JWT_KEY), so no network call: the RS256 signature,
`exp`/`nbf`/`iat` with 5 s of skew, and `azp` (the page's origin) against
CLERK_AUTHORIZED_PARTIES, which stops another site's page from using a visitor's session. Only
the header counts, never the `__session` cookie, which a browser also sends with another site's
form post (CSRF). A session token has `sid` and `exp`; a JWT-template token, or one that never
expires, is refused. Only `sub` is used.

The role is asked for only where a route needs it (`user_role`, `require_developer` in
vouch.api), so a Clerk outage never stops a request that doesn't. It lives in the user's private
metadata (`{"role": "developer"}`, docs/requirements.md, 2026-11-04), which only the backend can
read: from Clerk's Backend API with the secret key, cached per user for ROLE_TTL seconds (a
development instance allows 100 requests per 10 s). Anyone else is a compliance lead.

Fails closed: a token that doesn't verify is SignedOut (401); a role Clerk can't be asked for is
ClerkUnavailable (503), never access.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple

import httpx
import jwt
from clerk_backend_api.security import authenticate_request
from clerk_backend_api.security.types import AuthenticateRequestOptions
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm
from starlette.requests import Request

from vouch.settings import CLERK_JWT_KEY_ENV, ClerkKeys, SettingsError, User

log = logging.getLogger(__name__)

Role = Literal["compliance-lead", "developer"]

CLERK_API = "https://api.clerk.com/v1"
USER_ID = re.compile(r"user_[A-Za-z0-9]{1,64}")
"""Clerk's user ids; anything else is refused, as the id scopes what a user stores."""


class SignedOut(Exception):
    """No verified user on this request: the API answers 401."""


class ClerkUnavailable(Exception):
    """Clerk couldn't be asked for the user's role: the API answers 503."""


@dataclass(frozen=True)
class _Headers:
    """What authenticate_request reads of a request: its headers, here the Authorization alone."""

    headers: dict[str, str]


class _CachedRole(NamedTuple):
    expires: float
    role: Role


def _checked_jwt_key(keys: ClerkKeys) -> str:
    """The JWT key as the SDK will use it (line breaks dropped), parsed once. Raises SettingsError."""
    key = re.sub(r"\r\n|\n|\r", "", keys.jwt_key.get_secret_value())
    try:
        parsed = RSAAlgorithm(RSAAlgorithm.SHA256).prepare_key(key)
    except (jwt.InvalidKeyError, ValueError, TypeError):
        parsed = None
    if not isinstance(parsed, RSAPublicKey):  # prepare_key also takes a private key
        raise SettingsError(f"{CLERK_JWT_KEY_ENV} is not a PEM public key")
    return key


class ClerkAuth:
    """Checks one request's session token, and finds a user's role. Built once per app."""

    ROLE_TTL = 300.0
    """Seconds a user's role is kept before Clerk is asked again."""

    def __init__(
        self,
        keys: ClerkKeys,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        """Raises SettingsError, naming the setting, when the JWT key isn't a public key."""
        self._options = AuthenticateRequestOptions(
            jwt_key=_checked_jwt_key(keys),
            authorized_parties=list(keys.authorized_parties),
            accepts_token=["session_token"],  # an API key or machine token is never a user
        )
        self._secret_key = keys.secret_key
        self._client = client or httpx.Client(timeout=5.0)
        self._clock = clock
        self._roles: dict[str, _CachedRole] = {}

    def close(self) -> None:
        """Closes the connection to Clerk's Backend API (the app's shutdown)."""
        self._client.close()

    def user(self, request: Request) -> User:
        """The request's verified user, checked offline. Raises SignedOut."""
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise SignedOut()
        try:
            state = authenticate_request(_Headers({"Authorization": authorization}), self._options)
        except (jwt.PyJWTError, ValueError):  # a token the SDK couldn't even read
            log.info("signed out: an unreadable token")
            raise SignedOut() from None
        if not state.is_signed_in:
            log.info("signed out: %s", state.reason)  # the reason only, never the token
            raise SignedOut()
        claims: dict[str, Any] = state.payload or {}
        user_id = claims.get("sub")
        usable = (
            isinstance(user_id, str)
            and USER_ID.fullmatch(user_id) is not None
            and isinstance(claims.get("sid"), str)
            and bool(claims["sid"])
            and isinstance(claims.get("exp"), int | float)
            # A pending session (`sts`, with Organizations required) hasn't finished signing in.
            and claims.get("sts") != "pending"
        )
        if not usable:
            log.info("signed out: not a usable session token")
            raise SignedOut()
        return User(id=user_id)

    def role(self, user: User) -> Role:
        """The user's role, cached. Raises ClerkUnavailable, or SignedOut for a deleted user."""
        now = self._clock()
        cached = self._roles.get(user.id)
        if cached is not None and now < cached.expires:
            return cached.role
        role = self._fetch_role(user.id)
        self._roles[user.id] = _CachedRole(expires=now + self.ROLE_TTL, role=role)
        return role

    def _fetch_role(self, user_id: str) -> Role:
        """Developer when the user's private metadata says so exactly; else compliance lead."""
        try:
            response = self._client.get(
                f"{CLERK_API}/users/{user_id}",
                headers={"Authorization": f"Bearer {self._secret_key.get_secret_value()}"},
            )
            if response.status_code == 404:  # deleted since the token was issued
                log.info("signed out: Clerk does not know the token's user")
                raise SignedOut()
            response.raise_for_status()
            metadata = response.json()["private_metadata"]
        except SignedOut:
            raise
        # Unreachable or refused (HTTPError), or not the answer expected (the rest).
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            log.error("Clerk's user lookup failed: %s %s", type(error).__name__, status or "")
            raise ClerkUnavailable() from None
        developer = isinstance(metadata, dict) and metadata.get("role") == "developer"
        return "developer" if developer else "compliance-lead"
