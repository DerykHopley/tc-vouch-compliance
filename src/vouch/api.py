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

router = APIRouter(prefix="/api")


class AssessmentRequest(BaseModel):
    control_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def requested_control(request: Annotated[AssessmentRequest, Body()]) -> Control:
    """Looked up before the stream starts, so an unknown control is a plain 404."""
    try:
        return load_control(request.control_id)
    except UnknownControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def current_user(request: Request) -> User:
    """The request's user: local mode's default user; in clerk mode, refused until #32 signs in."""
    settings: Settings = request.app.state.settings
    if settings.auth_mode == "local":
        return DEFAULT_USER
    raise HTTPException(status_code=401, detail="Sign in to use Vouch.")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": version("vouch")}


@router.post(
    "/assessments", response_class=EventSourceResponse, dependencies=[Depends(current_user)]
)
def create_assessment(
    control: Annotated[Control, Depends(requested_control)], request: Request
) -> Iterable[ServerSentEvent]:
    """A `step` event per agent step, one `result`, then close; or one `error`, then close.

    Until sign-in exists (M3) this runs only the scripted fake model, so an open URL costs
    nothing: ASSESS_FAKE_SCRIPT's script, else the bundled demo script.
    """
    settings: Settings = request.app.state.settings
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

    `settings` defaults to the environment's (load_settings).

    A path that is neither a route nor a built file gets the front end's index.html when a
    browser asks for a page, so a deep link survives a refresh (client routing).
    """
    app = FastAPI(title="Vouch")
    settings = settings or load_settings()
    app.state.settings = settings
    frontend = settings.frontend
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


def _app_from_env() -> FastAPI:
    try:
        settings = load_settings()
    except SettingsError as error:
        raise SystemExit(f"Vouch cannot start: {error}.") from None
    return create_app(settings)


app = _app_from_env()
