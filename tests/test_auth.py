"""Sign-in in clerk mode (#32), through the HTTP API.

No Clerk account and no network: the tests make their own RSA key pair, sign session tokens
shaped like Clerk's with the private half, and give the app the public half as the JWT key, as
the Clerk dashboard would. The app verifies them on its real code path (clerk-backend-api's
`authenticate_request`, networkless with the JWT key). Clerk's Backend API, where the role lives
(private metadata), is a fake behind httpx's MockTransport.
"""

import time
from collections.abc import Callable
from typing import Annotated, Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import SecretStr

from vouch import api
from vouch.api import create_app, current_user, require_developer, user_role
from vouch.auth import ClerkAuth, Role
from vouch.settings import ClerkKeys, Settings, User

SECRET = "sk_test_fake-secret"
ORIGIN = "http://localhost:5173"


def _key_pair() -> tuple[rsa.RSAPrivateKey, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public.decode()


PRIVATE, PUBLIC = _key_pair()
OTHER_PRIVATE, _ = _key_pair()
KEYS = ClerkKeys(
    secret_key=SecretStr(SECRET),
    publishable_key="pk_test_ZmFrZSQ",
    jwt_key=SecretStr(PUBLIC),
    authorized_parties=(ORIGIN, "https://vouch.example.com"),
)


def token(sub: str = "user_alice", key: Any = PRIVATE, ttl: int = 60, **claims: Any) -> str:
    """A session token as Clerk's front end sends it: RS256, short-lived, the user in `sub`.
    A claim given as None is left out."""
    now = int(time.time())
    body = {
        "sub": sub,
        "sid": f"sess_{sub}",
        "iss": "https://vouch-test.clerk.accounts.dev",
        "azp": ORIGIN,
        "iat": now - 1,
        "nbf": now - 1,
        "exp": now + ttl,
        "v": 2,
    } | claims
    body = {name: value for name, value in body.items() if value is not None}
    return jwt.encode(body, key, algorithm="RS256", headers={"kid": "ins_test"})


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


class FakeClerk:
    """Clerk's Backend API, GET /v1/users/{id} only: each user's private metadata, and their
    name and e-mail addresses where `profiles` gives them (the User object's own fields)."""

    def __init__(self, metadata: dict[str, dict[str, Any]] | None = None):
        self.metadata = metadata if metadata is not None else {}
        self.profiles: dict[str, dict[str, Any]] = {}
        self.calls: list[httpx.Request] = []
        self.fail: Callable[[httpx.Request], httpx.Response] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail is not None:
            return self.fail(request)
        if request.headers.get("authorization") != f"Bearer {SECRET}":
            return httpx.Response(401, json={"errors": [{"code": "authentication_invalid"}]})
        user_id = request.url.path.removeprefix("/v1/users/")
        if request.method != "GET" or user_id not in self.metadata:
            return httpx.Response(404, json={"errors": [{"code": "resource_not_found"}]})
        # The fields the app reads, and one it must not: public metadata is the browser's.
        no_name = {
            "first_name": None,
            "last_name": None,
            "primary_email_address_id": None,
            "email_addresses": [],
        }
        return httpx.Response(
            200,
            json={
                "id": user_id,
                "object": "user",
                "public_metadata": {"role": "developer"},
                "private_metadata": self.metadata[user_id],
            }
            | no_name
            | self.profiles.get(user_id, {}),
        )


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no route to Clerk", request=request)


@pytest.fixture
def clerk() -> FakeClerk:
    return FakeClerk({"user_alice": {}, "user_bob": {}, "user_dev": {"role": "developer"}})


def clerk_app(clerk: FakeClerk, clock: Callable[[], float] = time.monotonic):
    """The app in clerk mode, its Clerk Backend API the fake, plus routes that show the user,
    their role, and one only a developer may use (as #34's developer page will)."""
    app = create_app(Settings(auth_mode="clerk", clerk=KEYS))
    http = httpx.Client(transport=httpx.MockTransport(clerk))
    app.state.auth = ClerkAuth(KEYS, client=http, clock=clock)

    @app.get("/whoami")
    def whoami(user: Annotated[User, Depends(current_user)]) -> dict[str, str]:
        return {"id": user.id}

    @app.get("/role")
    def role(role: Annotated[Role, Depends(user_role)]) -> dict[str, str]:
        return {"role": role}

    @app.get("/developer", dependencies=[Depends(require_developer)])
    def developer() -> dict[str, str]:
        return {"ok": "yes"}

    return app


@pytest.fixture
def client(clerk: FakeClerk) -> TestClient:
    return TestClient(clerk_app(clerk), raise_server_exceptions=False)


# --- Every route except health refuses a request without a valid token ------------------------

BAD_TOKENS = {
    "none": None,
    "forged": lambda: token(key=OTHER_PRIVATE),  # signed by another key
    "expired": lambda: token(ttl=-120),  # beyond Clerk's 5 s of clock skew
    "garbage": lambda: "not-a-jwt",
    "not-yet-valid": lambda: token(nbf=int(time.time()) + 120),
    "pending-session": lambda: token(sts="pending"),
    "not-a-user-id": lambda: token(sub="../etc"),
    "no-subject": lambda: token(sub=""),
    "machine-token": lambda: "ak_" + token(),  # an API key: never a user's session
    "unsigned": lambda: jwt.encode({"sub": "user_alice", "exp": time.time() + 60}, None, "none"),
    "other-origin": lambda: token(azp="https://evil.example"),
    "no-origin": lambda: token(azp=None),
    "jwt-template-no-sid": lambda: token(sid=None),
    "never-expires": lambda: token(exp=None),
}


def api_routes(app) -> set[tuple[str, str]]:
    """Every method and path the app serves under /api, a path parameter filled in: those in its
    OpenAPI map, and the API routers' own, which include those left out of the map."""
    mapped = {(m.upper(), path) for path, ops in app.openapi()["paths"].items() for m in ops}
    routed = {
        (method, route.path)
        for route in [*api.public.routes, *api.router.routes]
        for method in route.methods - {"HEAD"}
    }
    return {(m, path.replace("{path:path}", "anything")) for m, path in mapped | routed}


@pytest.mark.parametrize("bad", BAD_TOKENS.values(), ids=BAD_TOKENS.keys())
def test_every_route_except_health_refuses_a_request_without_a_valid_token(clerk, bad):
    app = clerk_app(clerk)
    client = TestClient(app, raise_server_exceptions=False)
    headers = bearer(bad()) if bad else {}
    routes = api_routes(app)
    assert {("POST", "/api/assessments"), ("GET", "/api/anything")} <= routes

    answers = {
        (method, path): client.request(method, path, headers=headers, json={}).status_code
        for method, path in routes
    }

    assert answers.pop(("GET", "/api/health")) == 200
    assert set(answers.values()) == {401}
    assert client.get("/developer", headers=headers).status_code == 401
    assert clerk.calls == []  # refused before Clerk's API is asked about anyone


def test_a_token_from_each_authorised_origin_is_accepted(client):
    for origin in KEYS.authorized_parties:
        assert client.get("/whoami", headers=bearer(token(azp=origin))).status_code == 200


def test_a_refusal_says_to_sign_in_and_names_bearer(client):
    response = client.post("/api/assessments", json={"control_id": "4.1.2"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Sign in to use Vouch."}
    assert response.headers["www-authenticate"] == "Bearer"


def test_a_session_cookie_alone_is_not_enough(client):
    """Only the Authorization header counts: a browser sends a cookie with another site's form
    post too (CSRF); the header only when Vouch's own page adds it."""
    response = client.get("/whoami", headers={"Cookie": f"__session={token()}"})

    assert response.status_code == 401


def test_a_valid_token_reaches_the_assessment_without_asking_clerk(client, clerk):
    response = client.post(
        "/api/assessments", json={"control_id": "4.1.2"}, headers=bearer(token())
    )

    assert response.status_code == 200
    assert "event: result" in response.text
    assert clerk.calls == []  # the token is checked offline; the role isn't needed here


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_the_api_map_is_not_served_in_clerk_mode(client, path):
    assert client.get(path).status_code == 404


# --- Each user is who their token says ---------------------------------------------------------


def test_two_users_are_told_apart_by_their_tokens(client):
    """The user id scopes what a user stores (GLOSSARY.md, "User"): each token gets its own."""
    alice = client.get("/whoami", headers=bearer(token("user_alice"))).json()
    bob = client.get("/whoami", headers=bearer(token("user_bob"))).json()

    assert (alice["id"], bob["id"]) == ("user_alice", "user_bob")


def test_claims_beyond_the_user_id_are_never_read(client):
    """A role claim in the token is ignored: the role is the backend's, from private metadata."""
    response = client.get(
        "/role", headers=bearer(token("user_alice", role="developer", metadata={"role": "dev"}))
    )

    assert response.json() == {"role": "compliance-lead"}


# --- The role comes from Clerk's private metadata, only where a route needs it ----------------


def test_a_user_named_developer_in_private_metadata_is_a_developer(client):
    assert client.get("/role", headers=bearer(token("user_dev"))).json() == {"role": "developer"}
    assert client.get("/developer", headers=bearer(token("user_dev"))).status_code == 200


@pytest.mark.parametrize(
    "metadata",
    [{}, {"role": "admin"}, {"role": "Developer"}, {"role": ["developer"]}, {"other": 1}],
    ids=["none", "other-role", "other-case", "a-list", "other-key"],
)
def test_anyone_else_is_a_compliance_lead(clerk, metadata):
    clerk.metadata["user_carol"] = metadata
    client = TestClient(clerk_app(clerk), raise_server_exceptions=False)

    role = client.get("/role", headers=bearer(token("user_carol")))
    developer = client.get("/developer", headers=bearer(token("user_carol")))

    assert role.json() == {"role": "compliance-lead"}
    assert developer.status_code == 403
    assert developer.json() == {"detail": "This is available to developers only."}


def test_public_metadata_never_makes_a_developer(client, clerk):
    """The fake's public metadata says developer for everyone; only private metadata counts."""
    assert client.get("/role", headers=bearer(token("user_alice"))).json()["role"] == (
        "compliance-lead"
    )
    assert str(clerk.calls[0].url) == "https://api.clerk.com/v1/users/user_alice"


def test_the_role_is_asked_once_per_user_then_cached_until_it_expires(clerk):
    now = [1000.0]
    client = TestClient(clerk_app(clerk, clock=lambda: now[0]), raise_server_exceptions=False)

    for _ in range(3):
        client.get("/role", headers=bearer(token("user_dev")))
    client.get("/role", headers=bearer(token("user_alice")))
    assert [call.url.path for call in clerk.calls] == ["/v1/users/user_dev", "/v1/users/user_alice"]

    clerk.metadata["user_dev"] = {}  # the owner takes the role away
    now[0] += ClerkAuth.USER_TTL + 1
    response = client.get("/role", headers=bearer(token("user_dev")))

    assert response.json()["role"] == "compliance-lead"
    assert len(clerk.calls) == 3


def test_a_user_clerk_does_not_know_is_refused_where_the_role_is_needed(client):
    """A deleted user's token still verifies until it expires; Clerk's 404 signs them out."""
    assert client.get("/role", headers=bearer(token("user_deleted"))).status_code == 401


# --- Clerk unreachable: 503 where the role is needed, never access ----------------------------

CLERK_FAILURES = {
    "unreachable": unreachable,
    "server-error": lambda request: httpx.Response(502, text="Bad gateway"),
    "rate-limited": lambda request: httpx.Response(429, json={"errors": []}),
    "secret-key-refused": lambda request: httpx.Response(401, json={"errors": []}),
    "not-json": lambda request: httpx.Response(200, text="<html>"),
    "timeout": lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)),
}


@pytest.mark.parametrize("failure", CLERK_FAILURES.values(), ids=CLERK_FAILURES.keys())
def test_clerk_failing_gives_503_with_a_message_never_access(client, clerk, failure):
    clerk.fail = failure

    for path in ("/role", "/developer"):
        response = client.get(path, headers=bearer(token("user_dev")))
        assert response.status_code == 503
        assert response.json() == {
            "detail": "Vouch cannot check your sign-in just now. Please try again shortly."
        }


def test_clerk_failing_never_stops_a_route_that_needs_no_role(client, clerk):
    clerk.fail = unreachable

    response = client.post(
        "/api/assessments", json={"control_id": "4.1.2"}, headers=bearer(token("user_dev"))
    )

    assert response.status_code == 200


def test_a_failed_role_lookup_is_not_cached(client, clerk):
    clerk.fail = unreachable
    assert client.get("/role", headers=bearer(token("user_dev"))).status_code == 503

    clerk.fail = None
    assert client.get("/role", headers=bearer(token("user_dev"))).json()["role"] == "developer"


def test_the_verifier_failing_never_lets_the_request_through(client, monkeypatch):
    """Anything unexpected while checking a token is a server error, never access."""

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("verifier broke")

    monkeypatch.setattr("vouch.auth.authenticate_request", broken)

    response = client.get("/whoami", headers=bearer(token()))

    assert response.status_code == 500


def test_no_token_or_secret_reaches_the_log(client, clerk, caplog):
    caplog.set_level("DEBUG")
    sent = token("user_dev")
    client.get("/whoami", headers=bearer(token(key=OTHER_PRIVATE)))
    clerk.fail = unreachable
    client.get("/role", headers=bearer(sent))

    assert caplog.text  # the refusals were logged
    assert sent not in caplog.text and sent.split(".")[1] not in caplog.text
    assert SECRET not in caplog.text


# --- Startup and shutdown ----------------------------------------------------------------------


@pytest.mark.parametrize("jwt_key", ["-----BEGIN PUBLIC KEY-----marker", "not a key", PUBLIC[:80]])
def test_a_malformed_jwt_key_stops_the_app_at_startup_naming_it(jwt_key):
    keys = ClerkKeys(KEYS.secret_key, KEYS.publishable_key, SecretStr(jwt_key), (ORIGIN,))

    with pytest.raises(SystemExit) as stopped:
        create_app(Settings(auth_mode="clerk", clerk=keys))

    assert "CLERK_JWT_KEY" in str(stopped.value)
    assert jwt_key not in str(stopped.value)


def test_a_private_key_as_the_jwt_key_stops_the_app_at_startup():
    private_pem = PRIVATE.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    keys = ClerkKeys(KEYS.secret_key, KEYS.publishable_key, SecretStr(private_pem), (ORIGIN,))

    with pytest.raises(SystemExit) as stopped:
        create_app(Settings(auth_mode="clerk", clerk=keys))

    assert "CLERK_JWT_KEY is not a PEM public key" in str(stopped.value)


def test_a_jwt_key_with_its_line_breaks_starts():
    assert "\n" in PUBLIC
    create_app(Settings(auth_mode="clerk", clerk=KEYS))


def test_the_connection_to_clerk_is_closed_on_shutdown(clerk):
    app = clerk_app(clerk)
    http = app.state.auth._client

    with TestClient(app):
        assert not http.is_closed

    assert http.is_closed


# --- What the front end needs, and local mode -------------------------------------------------


def test_health_tells_the_front_end_how_to_sign_in(client):
    response = client.get("/api/health")

    assert response.json()["auth"] == {
        "mode": "clerk",
        "publishable_key": "pk_test_ZmFrZSQ",
        "gate": "clerk",  # no Discord server set (#152)
    }


def test_local_mode_is_unchanged_one_default_user_and_no_sign_in():
    app = create_app(Settings())

    @app.get("/whoami")
    def whoami(
        user: Annotated[User, Depends(current_user)], role: Annotated[Role, Depends(user_role)]
    ) -> dict[str, str]:
        return {"id": user.id, "role": role}

    client = TestClient(app)

    # Local mode is for a developer's machine: its one user is a developer.
    assert client.get("/whoami").json() == {"id": "local-user", "role": "developer"}
    assert client.get("/api/health").json()["auth"] == {
        "mode": "local",
        "publishable_key": None,
        "gate": "none",
    }
    assert client.get("/openapi.json").status_code == 200  # the API map stays, locally


# --- The course's Discord server gates access (#152) --------------------------------------------

SERVER = "111111111111111111"  # the course's Discord server
OTHER_SERVER = "222222222222222222"
GATED = ClerkKeys(**(vars(KEYS) | {"discord_server_id": SERVER}))
DISCORD_TOKEN = "discord-oauth-token-marker"


class FakeAccess:
    """Clerk's Backend API (users, their Discord token, invitations) and Discord's API, one fake.

    `servers`: each user's Discord servers, in order. `scopes`: each user's token's scopes (none
    listed is a token Clerk doesn't hold). `invited`: e-mail addresses with an accepted invitation.
    """

    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, Any]] = {
            "user_alice": {},
            "user_bob": {},
            "user_dev": {"role": "developer"},
            "user_flagged": {"access": "invited"},
            "user_invited": {},
        }
        self.emails = {"user_invited": "reviewer@example.com", "user_bob": "bob@example.com"}
        self.servers: dict[str, list[str]] = {"user_alice": [OTHER_SERVER, SERVER]}
        self.scopes: dict[str, list[str]] = {
            user: ["identify", "email", "guilds"] for user in self.metadata
        }
        self.invited = {"reviewer@example.com"}
        self.calls: list[httpx.Request] = []
        self.clerk_fail: Callable[[httpx.Request], httpx.Response] | None = None
        self.discord_fail: Callable[[httpx.Request], httpx.Response] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host == "discord.com":
            return (self.discord_fail or self.discord)(request)
        return (self.clerk_fail or self.clerk)(request)

    def clerk(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {SECRET}":
            return httpx.Response(401, json={"errors": []})
        path = request.url.path
        if path == "/v1/invitations":
            query = request.url.params.get("query", "")
            status = request.url.params.get("status")
            # Clerk's query matches part of an address; the app must check the whole one.
            found = [e for e in self.invited if query and query in e and status == "accepted"]
            return httpx.Response(
                200,
                json=[
                    {"object": "invitation", "email_address": e, "status": "accepted"}
                    for e in found
                ],
            )
        user_id, _, rest = path.removeprefix("/v1/users/").partition("/")
        if user_id not in self.metadata:
            return httpx.Response(404, json={"errors": []})
        if rest == "oauth_access_tokens/oauth_discord":
            scopes = self.scopes.get(user_id)
            if scopes is None:
                return httpx.Response(200, json=[])
            tokens = [
                {
                    "object": "oauth_access_token",
                    "provider": "oauth_discord",
                    "token": f"{DISCORD_TOKEN}-{user_id}",
                    "scopes": scopes,
                }
            ]
            return httpx.Response(200, json=tokens)
        email = self.emails.get(user_id)
        addresses = (
            [
                {"email_address": email, "verification": {"status": "verified"}},
                {
                    "email_address": "unverified@example.com",
                    "verification": {"status": "unverified"},
                },
            ]
            if email
            else []
        )
        return httpx.Response(
            200,
            json={
                "id": user_id,
                "private_metadata": self.metadata[user_id],
                "public_metadata": {"access": "invited", "role": "developer"},  # never read
                "email_addresses": addresses,
            },
        )

    def discord(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        user_id = auth.removeprefix(f"Bearer {DISCORD_TOKEN}-")
        if not auth.startswith(f"Bearer {DISCORD_TOKEN}-") or user_id not in self.metadata:
            return httpx.Response(401, json={"message": "401: Unauthorized", "code": 0})
        if "guilds" not in self.scopes.get(user_id, []):
            return httpx.Response(403, json={"message": "Missing Access", "code": 50001})
        assert request.url.path == "/api/v10/users/@me/guilds"
        limit = int(request.url.params["limit"])
        after = request.url.params.get("after")
        servers = self.servers.get(user_id, [])
        start = servers.index(after) + 1 if after else 0
        page = servers[start : start + limit]
        return httpx.Response(200, json=[{"id": s, "name": f"server {s}"} for s in page])

    def discord_calls(self) -> list[httpx.Request]:
        return [call for call in self.calls if call.url.host == "discord.com"]


@pytest.fixture
def access() -> FakeAccess:
    return FakeAccess()


def gated_app(
    access: FakeAccess, keys: ClerkKeys = GATED, clock: Callable[[], float] = time.monotonic
):
    app = create_app(Settings(auth_mode="clerk", clerk=keys))
    http = httpx.Client(transport=httpx.MockTransport(access))
    app.state.auth = ClerkAuth(keys, client=http, clock=clock)
    return TestClient(app, raise_server_exceptions=False)


def ask(client: TestClient, user: str) -> httpx.Response:
    return client.get("/api/access", headers=bearer(token(user)))


def test_a_member_of_the_course_server_is_allowed(access):
    response = ask(gated_app(access), "user_alice")

    assert response.status_code == 200
    assert response.json() == {"access": "allowed"}
    [call] = access.discord_calls()
    assert call.url.params["limit"] == "200"


def test_someone_outside_the_course_server_gets_403_not_a_member_on_every_route(access):
    access.servers["user_bob"] = [OTHER_SERVER]
    client = gated_app(access)

    for response in (
        ask(client, "user_bob"),
        client.post(
            "/api/assessments", json={"control_id": "4.1.2"}, headers=bearer(token("user_bob"))
        ),
    ):
        assert response.status_code == 403
        assert response.json()["reason"] == "not-a-member"
        assert "Discord server" in response.json()["detail"]


def test_the_servers_are_read_page_by_page_past_200(access):
    access.servers["user_bob"] = [f"9{n:017d}" for n in range(450)] + [SERVER]

    assert ask(gated_app(access), "user_bob").status_code == 200
    pages = access.discord_calls()
    assert [call.url.params.get("after") for call in pages] == [
        None,
        access.servers["user_bob"][199],
        access.servers["user_bob"][399],
    ]


@pytest.mark.parametrize("user", ["user_dev", "user_flagged", "user_invited"])
def test_the_developer_and_invited_users_are_allowed_without_the_server(access, user):
    access.servers[user] = []
    access.scopes[user] = []  # not even a Discord token with the scope: never asked

    assert ask(gated_app(access), user).status_code == 200
    assert access.discord_calls() == []


def test_an_invitation_counts_only_for_the_whole_verified_address(access):
    access.invited = {"bob@example.com.evil", "unverified@example.com"}
    access.servers["user_bob"] = []

    assert ask(gated_app(access), "user_bob").json()["reason"] == "not-a-member"


@pytest.mark.parametrize(
    "token_case",
    ["no-discord-token", "no-guilds-scope-listed", "discord-refuses-the-token", "discord-403"],
)
def test_a_token_discord_rejects_or_without_the_scope_says_sign_in_again(access, token_case):
    if token_case == "no-discord-token":
        del access.scopes["user_alice"]
    elif token_case == "no-guilds-scope-listed":
        access.scopes["user_alice"] = ["identify", "email"]
    elif token_case == "discord-refuses-the-token":
        access.discord_fail = lambda request: httpx.Response(401, json={"code": 0})
    else:
        access.discord_fail = lambda request: httpx.Response(403, json={"code": 50001})

    response = ask(gated_app(access), "user_alice")

    assert response.status_code == 403
    assert response.json()["reason"] == "sign-in-again"


DISCORD_FAILURES = {
    "unreachable": unreachable,
    "server-error": lambda request: httpx.Response(502, text="Bad gateway"),
    "rate-limited": lambda request: httpx.Response(429, json={"retry_after": 1.5}),
    "not-json": lambda request: httpx.Response(200, text="<html>"),
    "not-a-list": lambda request: httpx.Response(200, json={"id": SERVER}),
}


@pytest.mark.parametrize("failure", DISCORD_FAILURES.values(), ids=DISCORD_FAILURES.keys())
def test_discord_failing_gives_503_never_access(access, failure):
    access.discord_fail = failure

    response = ask(gated_app(access), "user_alice")

    assert response.status_code == 503
    assert response.json()["detail"] == api.CLERK_UNAVAILABLE


@pytest.mark.parametrize("failure", CLERK_FAILURES.values(), ids=CLERK_FAILURES.keys())
def test_clerk_failing_gives_503_never_access(access, failure):
    access.clerk_fail = failure

    assert ask(gated_app(access), "user_alice").status_code == 503


def test_a_user_clerk_does_not_know_is_signed_out(access):
    assert ask(gated_app(access), "user_deleted").status_code == 401


def test_the_answer_is_cached_one_discord_call_per_user_per_period(access):
    now = [1000.0]
    client = gated_app(access, clock=lambda: now[0])

    assert ask(client, "user_alice").status_code == 200
    assert ask(client, "user_alice").status_code == 200
    assert len(access.discord_calls()) == 1

    access.servers["user_alice"] = [OTHER_SERVER]  # they left the server
    now[0] += ClerkAuth.ACCESS_TTL + 1
    assert ask(client, "user_alice").json()["reason"] == "not-a-member"
    assert len(access.discord_calls()) == 2


def test_a_refusal_is_kept_only_briefly_so_doing_what_the_page_says_gets_in(access):
    """Joining the server, or signing in again, is checked within REFUSAL_TTL, not ACCESS_TTL."""
    now = [1000.0]
    client = gated_app(access, clock=lambda: now[0])
    access.servers["user_bob"] = []
    assert ask(client, "user_bob").json()["reason"] == "not-a-member"
    assert ask(client, "user_bob").json()["reason"] == "not-a-member"
    assert len(access.discord_calls()) == 1  # still cached for a moment

    access.servers["user_bob"] = [SERVER]  # they joined the course server
    now[0] += ClerkAuth.REFUSAL_TTL + 1
    assert ClerkAuth.REFUSAL_TTL < ClerkAuth.ACCESS_TTL
    assert ask(client, "user_bob").status_code == 200


def test_invitations_are_asked_for_in_one_large_page(access):
    ask(gated_app(access), "user_invited")

    [invitations] = [call for call in access.calls if call.url.path == "/v1/invitations"]
    assert invitations.url.params["limit"] == "500"


def test_a_failed_check_is_not_cached(access):
    client = gated_app(access)
    access.discord_fail = unreachable
    assert ask(client, "user_alice").status_code == 503

    access.discord_fail = None
    assert ask(client, "user_alice").status_code == 200


def test_without_a_server_id_clerk_alone_decides_and_discord_is_never_asked(access):
    access.servers["user_bob"] = []
    client = gated_app(access, keys=KEYS)

    assert ask(client, "user_bob").json() == {"access": "allowed"}
    assert access.calls == []


def test_no_discord_token_reaches_the_log_or_an_answer(access, caplog):
    caplog.set_level("DEBUG")
    client = gated_app(access)
    access.discord_fail = lambda request: httpx.Response(500, text=request.headers["authorization"])
    answers = [ask(client, "user_alice").text]
    access.discord_fail = None
    access.servers["user_bob"] = []
    answers.append(ask(client, "user_bob").text)

    seen = caplog.text + "".join(answers)
    assert caplog.text
    assert DISCORD_TOKEN not in seen and SECRET not in seen


def test_health_says_whether_the_discord_server_gates_sign_in(access):
    gated = gated_app(access).get("/api/health").json()["auth"]
    open_ = gated_app(access, keys=KEYS).get("/api/health").json()["auth"]

    assert gated["gate"] == "discord-server"
    assert open_["gate"] == "clerk"


def test_local_mode_has_no_gate():
    client = TestClient(create_app(Settings()))

    assert client.get("/api/access").json() == {"access": "allowed"}
    assert client.get("/api/health").json()["auth"]["gate"] == "none"


def test_the_access_check_also_answers_the_role_so_clerk_is_asked_once(access):
    """#152's access check and #154's lookup read the same Clerk user: one call serves both."""
    client = gated_app(access, clock=lambda: 1000.0)

    assert ask(client, "user_dev").status_code == 200
    settings = client.get("/api/developer/settings", headers=bearer(token("user_dev")))

    assert settings.status_code == 200  # the role came from the access check's read
    user_reads = [c for c in access.calls if c.url.path == "/v1/users/user_dev"]
    assert len(user_reads) == 1
