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

With DISCORD_SERVER_ID set (#152), every route but health also needs access (`access`, the
`require_access` dependency in vouch.api): a member of that Discord server, a developer, a user
with an accepted Clerk invitation, or one whose private metadata says `{"access": "invited"}`.
Membership is read with the user's Discord token, which Clerk keeps from the sign-in (scope
`guilds`), from Discord's GET /users/@me/guilds, 200 servers a page. The answer is cached per
user for ACCESS_TTL seconds, so Discord is asked once a period, not once a request.

Fails closed: a token that doesn't verify is SignedOut (401); a role or access Clerk or Discord
can't answer for is ClerkUnavailable (503), never access. A Discord token Discord refuses, or one
without the scope, is SignInAgain: signing in with Discord again gets a fresh one.
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
DISCORD_API = "https://discord.com/api/v10"
SERVERS_PER_PAGE = 200
"""Discord's most servers per page of GET /users/@me/guilds."""
MAX_SERVER_PAGES = 25
"""A stop for a listing that never ends: far past what a Discord account can join."""
USER_ID = re.compile(r"user_[A-Za-z0-9]{1,64}")
"""Clerk's user ids; anything else is refused, as the id scopes what a user stores."""


class SignedOut(Exception):
    """No verified user on this request: the API answers 401."""


class ClerkUnavailable(Exception):
    """Clerk (or Discord) couldn't be asked for the user's role or access: the API answers 503."""


class NotAMember(Exception):
    """Signed in, but not in the course's Discord server, nor invited: the API answers 403."""


class SignInAgain(Exception):
    """No Discord token Vouch can use (refused, or without the `guilds` scope): the API answers
    403, and the page asks the user to sign in with Discord again."""


Access = Literal["allowed", "not-a-member", "sign-in-again"]


@dataclass(frozen=True)
class _Headers:
    """What authenticate_request reads of a request: its headers, here the Authorization alone."""

    headers: dict[str, str]


class _CachedRole(NamedTuple):
    expires: float
    role: Role


class _CachedAccess(NamedTuple):
    expires: float
    access: Access


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
    ACCESS_TTL = 300.0
    """Seconds a user's access is kept before Clerk and Discord are asked again."""

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
        self._server_id = keys.discord_server_id
        self._client = client or httpx.Client(timeout=5.0)
        self._clock = clock
        self._roles: dict[str, _CachedRole] = {}
        self._access: dict[str, _CachedAccess] = {}

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

    def access(self, user: User) -> None:
        """Returns when the user may use Vouch; always, with no Discord server set. Cached.

        Raises NotAMember, SignInAgain, ClerkUnavailable, or SignedOut for a deleted user."""
        if self._server_id is None:
            return
        now = self._clock()
        cached = self._access.get(user.id)
        if cached is None or now >= cached.expires:
            cached = _CachedAccess(
                expires=now + self.ACCESS_TTL, access=self._fetch_access(user.id)
            )
            self._access[user.id] = cached
            log.info("access checked: %s", cached.access)
        if cached.access == "not-a-member":
            raise NotAMember()
        if cached.access == "sign-in-again":
            raise SignInAgain()

    def _fetch_access(self, user_id: str) -> Access:
        """The developer, then the invited, then the Discord server's members are allowed."""
        found = self._user_for_access(user_id)
        if _role_of(found) == "developer" or _flagged_invited(found):
            return "allowed"
        if any(self._was_invited(address) for address in _verified_addresses(found)):
            return "allowed"
        token = self._discord_token(user_id)
        if token is None:
            return "sign-in-again"
        return self._membership(token)

    def _clerk(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        """A GET on Clerk's Backend API with the secret key. Raises ClerkUnavailable when Clerk
        can't be reached or refuses (a 404 is left to the caller)."""
        try:
            response = self._client.get(
                f"{CLERK_API}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._secret_key.get_secret_value()}"},
            )
            if response.status_code != 404:
                response.raise_for_status()
            return response
        except httpx.HTTPError as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            log.error("Clerk's API failed: %s %s", type(error).__name__, status or "")
            raise ClerkUnavailable() from None

    def _user_for_access(self, user_id: str) -> dict[str, Any]:
        """The user as Clerk's Backend API has it. Raises SignedOut when Clerk doesn't know them
        (deleted since the token was issued), ClerkUnavailable when Clerk can't answer."""
        response = self._clerk(f"/users/{user_id}")
        if response.status_code == 404:
            log.info("signed out: Clerk does not know the token's user")
            raise SignedOut()
        found = _json(response, "Clerk's user lookup")
        if not isinstance(found, dict):
            log.error("Clerk's user lookup failed: not a user")
            raise ClerkUnavailable()
        return found

    def _was_invited(self, address: str) -> bool:
        """Whether an invitation to exactly `address` was accepted (Clerk's query matches part)."""
        response = self._clerk("/invitations", {"status": "accepted", "query": address})
        invitations = _json(response, "Clerk's invitations") if response.status_code != 404 else []
        if not isinstance(invitations, list):
            log.error("Clerk's invitations failed: not a list")
            raise ClerkUnavailable()
        return any(
            isinstance(invitation, dict)
            and invitation.get("status") == "accepted"
            and str(invitation.get("email_address", "")).casefold() == address.casefold()
            for invitation in invitations
        )

    def _discord_token(self, user_id: str) -> str | None:
        """The Discord token Clerk keeps from the user's sign-in, when it has the `guilds` scope;
        None when there's none to use (signed up another way, or before the scope was added)."""
        response = self._clerk(f"/users/{user_id}/oauth_access_tokens/oauth_discord")
        tokens = _json(response, "Clerk's Discord token") if response.status_code != 404 else []
        if not isinstance(tokens, list):
            log.error("Clerk's Discord token failed: not a list")
            raise ClerkUnavailable()
        for found in tokens:
            token = found.get("token") if isinstance(found, dict) else None
            scopes = found.get("scopes") if isinstance(found, dict) else None
            if isinstance(token, str) and token and (scopes is None or "guilds" in scopes):
                return token
        return None

    def _membership(self, token: str) -> Access:
        """Allowed when the course's server is among the token's user's Discord servers, read a
        page at a time; sign-in-again when Discord refuses the token. Raises ClerkUnavailable
        when Discord can't answer."""
        after: str | None = None
        for _ in range(MAX_SERVER_PAGES):
            params = {"limit": str(SERVERS_PER_PAGE)} | ({"after": after} if after else {})
            try:
                response = self._client.get(
                    f"{DISCORD_API}/users/@me/guilds",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as error:
                log.error("Discord's server list failed: %s", type(error).__name__)
                raise ClerkUnavailable() from None
            if response.status_code in (401, 403):  # refused or revoked; or no `guilds` scope
                log.info("Discord refused the user's token: %s", response.status_code)
                return "sign-in-again"
            if response.status_code != 200:
                log.error("Discord's server list failed: %s", response.status_code)
                raise ClerkUnavailable()
            page = _json(response, "Discord's server list")
            ids = (
                [s.get("id") for s in page if isinstance(s, dict)]
                if isinstance(page, list)
                else None
            )
            if ids is None or not all(isinstance(i, str) for i in ids):
                log.error("Discord's server list failed: not a list of servers")
                raise ClerkUnavailable()
            if self._server_id in ids:
                return "allowed"
            if len(ids) < SERVERS_PER_PAGE or ids[-1] == after:
                return "not-a-member"
            after = ids[-1]
        log.error("Discord's server list failed: more than %s pages", MAX_SERVER_PAGES)
        raise ClerkUnavailable()


def _json(response: httpx.Response, what: str) -> Any:
    """The answer's JSON. Raises ClerkUnavailable when it isn't JSON."""
    try:
        return response.json()
    except ValueError:
        log.error("%s failed: not JSON", what)
        raise ClerkUnavailable() from None


def _role_of(found: dict[str, Any]) -> Role:
    """Developer when the private metadata says so exactly; anyone else is a compliance lead."""
    metadata = found.get("private_metadata")
    developer = isinstance(metadata, dict) and metadata.get("role") == "developer"
    return "developer" if developer else "compliance-lead"


def _flagged_invited(found: dict[str, Any]) -> bool:
    """The owner's hand-set flag in private metadata: `{"access": "invited"}`."""
    metadata = found.get("private_metadata")
    return isinstance(metadata, dict) and metadata.get("access") == "invited"


def _verified_addresses(found: dict[str, Any]) -> list[str]:
    """The user's e-mail addresses that Clerk has verified."""
    addresses = found.get("email_addresses")
    return [
        entry["email_address"]
        for entry in (addresses if isinstance(addresses, list) else [])
        if isinstance(entry, dict)
        and isinstance(entry.get("email_address"), str)
        and isinstance(entry.get("verification"), dict)
        and entry["verification"].get("status") == "verified"
    ]
