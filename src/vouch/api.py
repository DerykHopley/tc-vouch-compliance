"""The HTTP API: health, and one control's assessment streamed as server-sent events.

Run it locally with `uv run uvicorn vouch.api:app --port 8000`. With VOUCH_FRONTEND naming the
built front end (frontend/dist), it serves that too, on one origin, as the container does.
"""

import logging
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, StringConstraints

from vouch.allow_list import ALLOWED_TOOLS, GUARD, ToolAllowList
from vouch.auth import ClerkAuth, ClerkUnavailable, Role, SignedOut
from vouch.controls import Control, UnknownControlError, load_control
from vouch.database import Connection, Database, DatabaseUnavailable
from vouch.graph import Assessment, assess_steps
from vouch.limits import TOO_MANY_REQUESTS, MessageLimit, RateLimit
from vouch.redaction import install_log_redaction, redact_data, remember_secrets, removal_step
from vouch.settings import (
    DEFAULT_USER,
    DEMO_SCRIPT,
    FRONTEND_ENV,
    Settings,
    SettingsError,
    User,
    fake_model,
    load_settings,
)

log = logging.getLogger(__name__)

# The trial's verdicts, as the API names them (GLOSSARY.md, "Verdict"). #20 renames them inside.
VERDICTS = {"found": "evidenced", "partial": "partly-evidenced", "gap": "not-evidenced"}
FAILED = "The assessment could not be completed. Please try again later."
SIGNED_OUT = "Sign in to use Vouch."
CLERK_UNAVAILABLE = "Vouch cannot check your sign-in just now. Please try again shortly."
DEVELOPERS_ONLY = "This is available to developers only."
DATABASE_UNAVAILABLE = "The database is unavailable just now. Please try again shortly."


class AssessmentRequest(BaseModel):
    control_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def requested_control(request: Annotated[AssessmentRequest, Body()]) -> Control:
    """Looked up before the stream starts, so an unknown control is a plain 404."""
    try:
        return load_control(request.control_id)
    except UnknownControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def app_settings(request: Request) -> Settings:
    """The Settings the app was created with."""
    return request.app.state.settings


AppSettings = Annotated[Settings, Depends(app_settings)]


def _signed_out() -> HTTPException:
    return HTTPException(status_code=401, detail=SIGNED_OUT, headers={"WWW-Authenticate": "Bearer"})


def current_user(request: Request, settings: AppSettings) -> User:
    """The request's user: local mode's default user; in clerk mode, the verified token's (auth.py).

    Checked offline, so Clerk's API is never asked. No valid token is a 401.
    """
    if settings.auth_mode == "local":
        return DEFAULT_USER
    auth: ClerkAuth = request.app.state.auth
    try:
        return auth.user(request)
    except SignedOut:
        raise _signed_out() from None


CurrentUser = Annotated[User, Depends(current_user)]


def app_database(request: Request) -> Database | None:
    """The app's Database; None when no database URL is set."""
    return request.app.state.database


AppDatabase = Annotated[Database | None, Depends(app_database)]


def database_connection(db: AppDatabase) -> Iterator[Connection]:
    """A connection for a route that needs the database. A 503 when there's none to be had, or
    when it drops while the route uses it."""
    try:
        if db is None:
            raise DatabaseUnavailable("no database URL is set")
        with db.connection() as conn:
            yield conn
    except DatabaseUnavailable:
        raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE) from None


def user_role(request: Request, user: CurrentUser, settings: AppSettings) -> Role:
    """The user's role, for a route that needs it. Local mode's one user is a developer: local
    mode is for a developer's machine. In clerk mode Clerk's API is asked (cached, auth.py);
    when it can't answer, a 503, never access."""
    if settings.auth_mode == "local":
        return "developer"
    auth: ClerkAuth = request.app.state.auth
    try:
        return auth.role(user)
    except SignedOut:
        raise _signed_out() from None
    except ClerkUnavailable:
        raise HTTPException(status_code=503, detail=CLERK_UNAVAILABLE) from None


def require_developer(role: Annotated[Role, Depends(user_role)]) -> None:
    """A route only a developer may use (the developer page, #34); anyone else gets a 403."""
    if role != "developer":
        raise HTTPException(status_code=403, detail=DEVELOPERS_ONLY)


def rate_limit(request: Request, user: CurrentUser) -> None:
    """Security layer 1: a user over the request rate gets a 429 with a Retry-After."""
    wait = request.app.state.rate_limit.wait_for(user.id)
    if wait is not None:
        headers = {"Retry-After": str(wait)}
        raise HTTPException(status_code=429, detail=TOO_MANY_REQUESTS, headers=headers)


public = APIRouter(prefix="/api")
"""The routes open to anyone: health alone."""

router = APIRouter(prefix="/api", dependencies=[Depends(current_user), Depends(rate_limit)])
"""Every other route: a route added here needs a signed-in user in clerk mode, within the rate."""


@public.get("/health")
def health(settings: AppSettings, db: AppDatabase) -> dict[str, Any]:
    """Also tells the front end how to sign in (Clerk's publishable key is public), and, with a
    database URL set, whether the database is reachable."""
    publishable_key = settings.clerk.publishable_key if settings.clerk else None
    auth = {"mode": settings.auth_mode, "publishable_key": publishable_key}
    answer: dict[str, Any] = {"status": "ok", "version": version("vouch"), "auth": auth}
    if db is not None:
        answer["database"] = "reachable" if db.reachable() else "unavailable"
    return answer


@router.post("/assessments", response_class=EventSourceResponse)
def create_assessment(
    control: Annotated[Control, Depends(requested_control)],
    settings: AppSettings,
    request: Request,
) -> Iterable[ServerSentEvent]:
    """A `step` event per agent step, one `result`, then close; or one `error`, then close.

    Until sign-in exists (M3) this runs only the scripted fake model, so an open URL costs
    nothing: ASSESS_FAKE_SCRIPT's script, else the bundled demo script.

    Every tool call the model makes is checked against the allow-list here too (security
    layer 2): a refused one is a `guard` step.
    """
    try:
        model = ToolAllowList(fake_model(settings, DEMO_SCRIPT), request.app.state.allowed_tools)
        # assess_steps takes a BaseChatModel; ToolAllowList offers the part the graph uses.
        # The graph's signature is spec M1's to change, so the cast stays until then.
        for item in assess_steps(control, cast(BaseChatModel, model)):
            if isinstance(item, Assessment):
                yield from _redacted("result", {"assessment": _api_assessment(item)})
            else:
                yield from _redacted("step", {"node": item.node, "text": item.describe()})
            for refusal in model.take_refusals():
                yield from _redacted("step", {"node": GUARD, "text": refusal})
    except Exception:  # any failure reaches the browser as one formal sentence
        log.exception("assessment of %s failed", control.id)
        yield ServerSentEvent(event="error", data={"message": FAILED})


_REMOVED_FROM = {"result": "assessment", "step": "step"}
"""What a guard step calls the event something was removed from."""


def _redacted(event: str, data: dict[str, Any]) -> Iterator[ServerSentEvent]:
    """The event with secrets and personal data removed (security layer 4); a guard step before
    it says what was removed."""
    clean, removed = redact_data(data)
    if removed:
        text = removal_step(removed, _REMOVED_FROM[event])
        yield ServerSentEvent(event="step", data={"node": GUARD, "text": text})
    yield ServerSentEvent(event=event, data=clean)


def _api_assessment(assessment: Assessment) -> dict[str, Any]:
    return assessment.to_dict() | {"verdict": VERDICTS[assessment.verdict]}


@router.get("/{path:path}", include_in_schema=False)
def unknown_api_path(path: str) -> None:
    """Under /api, a path that isn't a route is a 404, never the front end's page."""
    raise HTTPException(status_code=404)


def create_app(settings: Settings | None = None) -> FastAPI:
    """The API under /api; given the built front end (`settings.frontend`), that on every other path.

    `settings` defaults to the environment's (load_settings). Settings that can't serve stop
    the app with a message naming the setting.

    A path that is neither a route nor a built file gets the front end's index.html when a
    browser asks for a page, so a deep link survives a refresh (client routing).
    """
    if settings is None:
        try:
            settings = load_settings()
        except SettingsError as error:
            raise SystemExit(f"Vouch cannot start: {error}.") from None
    clerk_mode = settings.auth_mode == "clerk"
    auth = None
    if clerk_mode:
        if settings.clerk is None:  # load_settings never makes these Settings
            raise SystemExit("Vouch cannot start: clerk mode needs the Clerk settings.")
        try:
            auth = ClerkAuth(settings.clerk)
        except SettingsError as error:
            raise SystemExit(f"Vouch cannot start: {error}.") from None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if app.state.auth is not None:
            app.state.auth.close()
        if app.state.database is not None:
            app.state.database.close()

    # In clerk mode the API's own map (/docs, /openapi.json) isn't served: it'd map it for anyone.
    no_map: dict[str, Any] = {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="Vouch", lifespan=lifespan, **(no_map if clerk_mode else {}))
    app.add_middleware(MessageLimit)  # security layer 1: no oversized message reaches a route
    remember_secrets(settings)  # security layer 4: its own secret values never leave it
    app.state.settings = settings
    app.state.rate_limit = RateLimit()
    app.state.allowed_tools = ALLOWED_TOOLS
    app.state.auth = auth
    app.state.database = Database(settings.database_url) if settings.database_url else None
    frontend = settings.frontend
    app.include_router(public)
    app.include_router(router)
    if frontend is not None:
        if not (frontend / "index.html").is_file():
            raise SystemExit(
                f"Vouch cannot start: no front-end build at {frontend} ({FRONTEND_ENV}). "
                "Build it with `npm run build` in frontend/, or unset "
                f"{FRONTEND_ENV} to run the API alone."
            )
        app.frontend("/", directory=frontend)
    return app


app = create_app()
install_log_redaction()  # security layer 4, once: by import time uvicorn's handlers exist
