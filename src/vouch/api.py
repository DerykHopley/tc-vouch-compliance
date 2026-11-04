"""The HTTP API: health, and one control's assessment streamed as server-sent events.

Run it locally with `uv run uvicorn vouch.api:app --port 8000`.
"""

import logging
from collections.abc import Iterable
from importlib.metadata import version
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, StringConstraints

from vouch.controls import Control, UnknownControlError, load_control
from vouch.graph import Assessment, assess_steps
from vouch.settings import DEMO_SCRIPT, fake_model_from_env

log = logging.getLogger(__name__)

# The trial's verdicts, as the API names them (GLOSSARY.md, "Verdict"). #20 renames them inside.
VERDICTS = {"found": "evidenced", "partial": "partly-evidenced", "gap": "not-evidenced"}
FAILED = "The assessment could not be completed. Please try again later."

app = FastAPI(title="Vouch")


class AssessmentRequest(BaseModel):
    control_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def requested_control(request: Annotated[AssessmentRequest, Body()]) -> Control:
    """Looked up before the stream starts, so an unknown control is a plain 404."""
    try:
        return load_control(request.control_id)
    except UnknownControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": version("vouch")}


@app.post("/api/assessments", response_class=EventSourceResponse)
def create_assessment(
    control: Annotated[Control, Depends(requested_control)],
) -> Iterable[ServerSentEvent]:
    """A `step` event per agent step, one `result`, then close; or one `error`, then close.

    Until sign-in exists (M3) this runs only the scripted fake model, so an open URL costs
    nothing: ASSESS_FAKE_SCRIPT's script, else the bundled demo script.
    """
    try:
        for item in assess_steps(control, fake_model_from_env(DEMO_SCRIPT)):
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
