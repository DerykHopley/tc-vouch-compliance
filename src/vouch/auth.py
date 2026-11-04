"""Sign-in in clerk mode: who a request is for, and their role.

The request must carry a Clerk session token in `Authorization: Bearer ...` (the front end gets
a fresh one per call; they live 60 s). clerk-backend-api's `authenticate_request` checks it with
the instance's JWT public key (CLERK_JWT_KEY), so no network call: the RS256 signature, and
`exp`/`nbf`/`iat` with 5 s of skew. Only the header counts, never the `__session` cookie, which a
browser also sends with another site's form post (CSRF). Only `sub` is read from the token.

The role lives in the user's private metadata (`{"role": "developer"}`, docs/requirements.md,
2026-11-04), which only the backend can read: from Clerk's Backend API with the secret key,
cached per user for ROLE_TTL seconds (a development instance allows 100 requests per 10 s).
Anyone else is a compliance lead.

Fails closed: a token that doesn't verify is SignedOut (401); Clerk unreachable, or anything
unexpected while checking, is ClerkUnavailable (503), never access.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from clerk_backend_api.security import authenticate_request
from clerk_backend_api.security.types import AuthenticateRequestOptions
from starlette.requests import Request

from vouch.settings import ClerkKeys, Role, User

log = logging.getLogger(__name__)

CLERK_API = "https://api.clerk.com/v1"
USER_ID = re.compile(r"user_[A-Za-z0-9]{1,64}")
"""Clerk's user ids; anything else is refused, as the id scopes what a user stores."""


class SignedOut(Exception):
    """No verified user on this request: the API answers 401."""


class ClerkUnavailable(Exception):
    """Sign-in could not be checked: the API answers 503."""


@dataclass(frozen=True)
class _Headers:
    """What authenticate_request reads of a request: its headers, here the Authorization alone."""

    headers: dict[str, str]


class ClerkAuth:
    """Checks one request's session token and finds its user's role. Built once per app."""

    ROLE_TTL = 300.0
    """Seconds a user's role is kept before Clerk is asked again."""

    def __init__(
        self,
        keys: ClerkKeys,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._options = AuthenticateRequestOptions(
            jwt_key=keys.jwt_key.get_secret_value(),
            accepts_token=["session_token"],  # an API key or machine token is never a user
        )
        self._secret_key = keys.secret_key
        self._client = client or httpx.Client(timeout=5.0)
        self._clock = clock
        self._roles: dict[str, tuple[float, Role]] = {}

    def user(self, request: Request) -> User:
        """The request's verified user. Raises SignedOut or ClerkUnavailable."""
        user_id = self._verified_user_id(request)
        return User(id=user_id, role=self._role(user_id))

    def _verified_user_id(self, request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise SignedOut()
        try:
            state = authenticate_request(_Headers({"Authorization": authorization}), self._options)
        # The verifier itself failed, however it failed: never let the request through.
        except Exception as error:  # noqa: BLE001 (logged by type: its message may hold the token)
            log.error("sign-in check failed: %s", type(error).__name__)
            raise ClerkUnavailable() from None
        if not state.is_signed_in:
            log.info("signed out: %s", state.reason)  # the reason only, never the token
            raise SignedOut()
        claims: dict[str, Any] = state.payload or {}
        user_id = claims.get("sub")
        # A pending session (Clerk's `sts`, with Organizations required) hasn't finished signing in.
        pending = claims.get("sts") == "pending"
        if not isinstance(user_id, str) or not USER_ID.fullmatch(user_id) or pending:
            log.info("signed out: a token without a usable user")
            raise SignedOut()
        return user_id

    def _role(self, user_id: str) -> Role:
        now = self._clock()
        cached = self._roles.get(user_id)
        if cached is not None and now < cached[0]:
            return cached[1]
        role = self._fetch_role(user_id)
        self._roles[user_id] = (now + self.ROLE_TTL, role)
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
