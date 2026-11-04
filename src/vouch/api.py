"""The HTTP API: health, one control's assessment streamed as server-sent events up to the
compliance lead's review, and the review that resumes it.

Run it locally with `uv run uvicorn vouch.api:app --port 8000`. With VOUCH_FRONTEND naming the
built front end (frontend/dist), it serves that too, on one origin, as the container does.
"""

import logging
import os
from collections.abc import Iterable
from dataclasses import asdict
from functools import cache
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, StringConstraints

from vouch.graph import (
    Assessment,
    InvalidReviewError,
    NotWaitingForReviewError,
    RequirementVerdict,
    ReviewDecision,
    assess_steps,
    memory_checkpointer,
    resume,
)
from vouch.isa import IsaControl, UnknownControlError, load_isa_control
from vouch.profile import (
    DEFAULT_PROFILE,
    ComplianceProfile,
    ProfileError,
    UnknownProfileError,
    load_named_profile,
)
from vouch.settings import DEMO_SCRIPT, fake_model_from_env

log = logging.getLogger(__name__)

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


@cache
def load_default_profile() -> ComplianceProfile:
    """The default compliance profile, read once; a failed read isn't kept, so it's tried again."""
    return load_named_profile(DEFAULT_PROFILE)


def compliance_profile() -> ComplianceProfile:
    """Loaded before the stream starts, so a broken profile is a plain 500, not an event."""
    try:
        return load_default_profile()
    except (ProfileError, UnknownProfileError):
        log.exception("the compliance profile %s could not be loaded", DEFAULT_PROFILE)
        raise HTTPException(status_code=500, detail=FAILED) from None


@cache
def checkpointer() -> BaseCheckpointSaver:
    """Where paused assessments wait for review: in memory, one per process, until M2 brings
    Postgres. A restart forgets them."""
    return memory_checkpointer()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": version("vouch")}


@router.post("/assessments", response_class=EventSourceResponse)
def create_assessment(
    control: Annotated[IsaControl, Depends(requested_control)],
    profile: Annotated[ComplianceProfile, Depends(compliance_profile)],
    saver: Annotated[BaseCheckpointSaver, Depends(checkpointer)],
) -> Iterable[ServerSentEvent]:
    """A `step` event per agent step, a `requirement` event per judged requirement, one
    `result` (the proposed Assessment, waiting for review, with the `thread_id` to review it
    by), then close; or one `error`, then close.

    Until sign-in exists (M3) this runs only the scripted fake model, so an open URL costs
    nothing: ASSESS_FAKE_SCRIPT's script, else the bundled demo script.
    """
    try:
        for item in assess_steps(control, profile, fake_model_from_env(DEMO_SCRIPT), saver):
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


@router.post("/assessments/{thread_id}/review")
def review_assessment(
    thread_id: str,
    decision: Annotated[ReviewDecision, Body()],
    saver: Annotated[BaseCheckpointSaver, Depends(checkpointer)],
) -> dict[str, dict]:
    """Resume a waiting assessment with the lead's decision; the reviewed Assessment.

    409 when the thread is unknown or not waiting; 422 for an override naming a requirement
    the assessment didn't judge (the decision's shape is checked before, also 422)."""
    try:
        reviewed = resume(thread_id, decision, fake_model_from_env(DEMO_SCRIPT), saver)
    except NotWaitingForReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InvalidReviewError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"assessment": reviewed.to_dict()}


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
