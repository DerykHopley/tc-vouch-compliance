"""The HTTP API: health, and one control's assessment streamed as server-sent events.

Run it locally with `uv run uvicorn vouch.api:app --port 8000`. With VOUCH_FRONTEND naming the
built front end (frontend/dist), it serves that too, on one origin, as the container does.
"""

import logging
import os
from collections.abc import Iterable
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, StringConstraints

from vouch.graph import Assessment, RequirementVerdict, assess_steps
from vouch.isa import IsaControl, UnknownControlError, load_isa_control
from vouch.profile import load_named_profile
from vouch.settings import DEMO_SCRIPT, fake_model_from_env

log = logging.getLogger(__name__)

PROFILE = "nordlicht"  # the one compliance profile until a user can choose theirs
FAILED = "The assessment could not be completed. Please try again later."
FRONTEND_ENV = "VOUCH_FRONTEND"

router = APIRouter(prefix="/api")


class AssessmentRequest(BaseModel):
    control_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def requested_control(request: Annotated[AssessmentRequest, Body()]) -> IsaControl:
    """Looked up before the stream starts, so an unknown control is a plain 404."""
    try:
        return load_isa_control(request.control_id)
    except UnknownControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": version("vouch")}


@router.post("/assessments", response_class=EventSourceResponse)
def create_assessment(
    control: Annotated[IsaControl, Depends(requested_control)],
) -> Iterable[ServerSentEvent]:
    """A `step` event per agent step, a `requirement` event per judged requirement, one
    `result`, then close; or one `error`, then close.

    Until sign-in exists (M3) this runs only the scripted fake model, so an open URL costs
    nothing: ASSESS_FAKE_SCRIPT's script, else the bundled demo script.
    """
    try:
        profile = load_named_profile(PROFILE)
        for item in assess_steps(control.id, profile, fake_model_from_env(DEMO_SCRIPT)):
            if isinstance(item, Assessment):
                yield ServerSentEvent(event="result", data={"assessment": item.to_dict()})
            elif isinstance(item, RequirementVerdict):
                yield ServerSentEvent(event="requirement", data={"requirement": asdict(item)})
            else:
                text = item.describe()
                yield ServerSentEvent(event="step", data={"node": item.node, "text": text})
    except Exception:  # any failure reaches the browser as one formal sentence
        log.exception("assessment of %s failed", control.id)
        yield ServerSentEvent(event="error", data={"message": FAILED})


@router.get("/{path:path}", include_in_schema=False)
def unknown_api_path(path: str) -> None:
    """Under /api, a path that isn't a route is a 404, never the front end's page."""
    raise HTTPException(status_code=404)


def create_app(frontend: Path | None = None) -> FastAPI:
    """The API under /api; given the built front end, that on every other path.

    A path that is neither a route nor a built file gets the front end's index.html when a
    browser asks for a page, so a deep link survives a refresh (client routing).
    """
    app = FastAPI(title="Vouch")
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


_frontend = os.environ.get(FRONTEND_ENV)
app = create_app(Path(_frontend) if _frontend else None)
