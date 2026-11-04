"""The HTTP API: health, and one control's assessment streamed as server-sent events.

Run it locally with `uv run uvicorn vouch.api:app --port 8000`. With VOUCH_FRONTEND naming the
built front end (frontend/dist), it serves that too, on one origin, as the container does.
"""

import logging
from collections.abc import Iterable
from importlib.metadata import version
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, StringConstraints

from vouch.auth import ClerkAuth, ClerkUnavailable, SignedOut
from vouch.controls import Control, UnknownControlError, load_control
from vouch.graph import Assessment, assess_steps
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


def current_user(request: Request, settings: AppSettings) -> User:
    """The request's user: local mode's default user; in clerk mode, the verified token's (auth.py).

    No valid token is a 401; sign-in that can't be checked (Clerk unreachable) a 503, never access.
    """
    if settings.auth_mode == "local":
        return DEFAULT_USER
    auth: ClerkAuth | None = getattr(request.app.state, "auth", None)
    try:
        if auth is None:
            raise SignedOut()
        return auth.user(request)
    except SignedOut:
        raise HTTPException(
            status_code=401, detail=SIGNED_OUT, headers={"WWW-Authenticate": "Bearer"}
        ) from None
    except ClerkUnavailable:
        raise HTTPException(status_code=503, detail=CLERK_UNAVAILABLE) from None


public = APIRouter(prefix="/api")
"""The routes open to anyone: health alone."""

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])
"""Every other route: a route added here needs a signed-in user in clerk mode."""


@public.get("/health")
def health(settings: AppSettings) -> dict[str, Any]:
    """Also tells the front end how to sign in: Clerk's publishable key is public."""
    publishable_key = settings.clerk.publishable_key if settings.clerk else None
    auth = {"mode": settings.auth_mode, "publishable_key": publishable_key}
    return {"status": "ok", "version": version("vouch"), "auth": auth}


@router.post("/assessments", response_class=EventSourceResponse)
def create_assessment(
    control: Annotated[Control, Depends(requested_control)], settings: AppSettings
) -> Iterable[ServerSentEvent]:
    """A `step` event per agent step, one `result`, then close; or one `error`, then close.

    Until sign-in exists (M3) this runs only the scripted fake model, so an open URL costs
    nothing: ASSESS_FAKE_SCRIPT's script, else the bundled demo script.
    """
    try:
        for item in assess_steps(control, fake_model(settings, DEMO_SCRIPT)):
            if isinstance(item, Assessment):
                yield ServerSentEvent(event="result", data={"assessment": _api_assessment(item)})
            else:
                text = item.describe()
                yield ServerSentEvent(event="step", data={"node": item.node, "text": text})
    except Exception:  # any failure reaches the browser as one formal sentence
        log.exception("assessment of %s failed", control.id)
        yield ServerSentEvent(event="error", data={"message": FAILED})


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
    signed_in = settings.auth_mode == "clerk"
    # In clerk mode the API's own map (/docs, /openapi.json) isn't served: it'd map it for anyone.
    no_map: dict[str, Any] = {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="Vouch", **(no_map if signed_in else {}))
    app.state.settings = settings
    app.state.auth = ClerkAuth(settings.clerk) if settings.clerk and signed_in else None
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
