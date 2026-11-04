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
    """Clerk's Backend API, GET /v1/users/{id} only: each user's private metadata."""

    def __init__(self, metadata: dict[str, dict[str, Any]] | None = None):
        self.metadata = metadata if metadata is not None else {}
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
        return httpx.Response(
            200,
            json={
                "id": user_id,
                "object": "user",
                "public_metadata": {"role": "developer"},
                "private_metadata": self.metadata[user_id],
            },
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
    response = client.post("/api/assessments", json={"control_id": "NK-ENC-01"})

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
        "/api/assessments", json={"control_id": "NK-ENC-01"}, headers=bearer(token())
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
    now[0] += ClerkAuth.ROLE_TTL + 1
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
        "/api/assessments", json={"control_id": "NK-ENC-01"}, headers=bearer(token("user_dev"))
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

    assert response.json()["auth"] == {"mode": "clerk", "publishable_key": "pk_test_ZmFrZSQ"}


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
    assert client.get("/api/health").json()["auth"] == {"mode": "local", "publishable_key": None}
    assert client.get("/openapi.json").status_code == 200  # the API map stays, locally
