"""One assessment run, as the HTTP API's assessment stream and the chat's `start_assessment`
tool both make it (#45): how it starts (one at a time per lead, remembered, counted) and what it
streams.

A run's model is metered and its tool calls checked against the compliance profile's
allow-list (security layer 2); each refused call is a guard step.
"""

import logging
import threading
from collections import Counter
from collections.abc import Callable, Generator
from dataclasses import asdict, dataclass, replace
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from vouch.allow_list import GUARD, ToolAllowList
from vouch.graph import Assessment, RequirementVerdict, assess_steps, new_thread_id
from vouch.isa import IsaControl
from vouch.memory import Memory, rejection_note
from vouch.model_choice import ModelChoice
from vouch.profile import ComplianceProfile
from vouch.spending import UsageMeter

log = logging.getLogger(__name__)

ASSESSMENT_FAILED = "The assessment could not be completed. Please try again later."
"""The one sentence for an assessment that failed, wherever it was started."""
RUNNING_ALREADY = (
    "An assessment is already running for you. Please wait until it has finished before "
    "starting another."
)


class AlreadyRunning(RuntimeError):
    """The lead has an assessment running already (RunningAssessments)."""

    def __init__(self) -> None:
        super().__init__(RUNNING_ALREADY)


@dataclass(frozen=True)
class AssessmentRun:
    """What one assessment runs on: the model (`models` and the `chosen` id), the tools the
    compliance profile allows, the saver its thread is kept in, the meter that sees every model
    call, and whose it is (`scope`, vouch.memory.Memory.scope)."""

    models: ModelChoice
    chosen: str | None
    tools: frozenset[str]
    saver: BaseCheckpointSaver
    meter: UsageMeter
    scope: str

    def metered_afresh(self) -> "AssessmentRun":
        """The same run with a meter of its own, for another assessment from the same request
        (the chat may start more than one)."""
        return replace(self, meter=UsageMeter(self.chosen))


def assessment_events(
    control: IsaControl,
    profile: ComplianceProfile,
    run: AssessmentRun,
    memory: Memory,
    *,
    thread_id: str,
    note: str = "",
) -> Generator[tuple[str, Any]]:
    """The run up to the review interrupt, as (event, data): a `step` per agent step (and per
    refused tool call), a `requirement` per judged requirement, then one `result`, the proposed
    Assessment itself. The run's meter sees every model call. Without a `note`, the lead's note
    from the control's last rejection is carried in (#42), wherever the run was started."""
    note = note or rejection_note(memory, run.saver, control.id)
    chosen = run.models.for_assessment(
        run.chosen,
        callbacks=[run.meter],
        answer_language=profile.answer_language,
        control_id=control.id,
    )
    model = ToolAllowList(chosen, run.tools)
    # assess_steps takes a BaseChatModel; ToolAllowList offers the part the graph uses.
    steps = assess_steps(
        control,
        profile,
        cast(BaseChatModel, model),
        run.saver,
        note=note,
        thread_id=thread_id,
        scope=run.scope,
    )
    for item in steps:
        if isinstance(item, Assessment):
            yield "result", item
        elif isinstance(item, RequirementVerdict):
            yield "requirement", {"requirement": asdict(item)}
        else:
            yield "step", item.to_dict()
        for refusal in model.take_refusals():
            yield "step", {"node": GUARD, "text": refusal}


class RunningAssessments:
    """Whose assessments are running in this process, by memory scope (user and profile): at
    most one each (`begin_run`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: Counter[str] = Counter()

    def try_hold(self, scope: str) -> bool:
        """Note a run for `scope` if none is running; False when one is."""
        with self._lock:
            if self._runs[scope]:
                return False
            self._runs[scope] += 1
            return True

    def release(self, scope: str) -> None:
        with self._lock:
            self._runs[scope] -= 1
            if self._runs[scope] <= 0:
                del self._runs[scope]


def begin_run(
    running: RunningAssessments, memory: Memory, control_id: str, count: Callable[[], None]
) -> str:
    """Start the lead's one run of `control_id`, before any model is called: held (AlreadyRunning
    when another of theirs is going), remembered on a new thread (#38), then counted (`count`
    raises when it can't be). Whatever fails, nothing is left: the index entry is forgotten and
    the hold released. On success the caller releases the hold once the run has ended. The new
    thread's id."""
    if not running.try_hold(memory.scope):
        raise AlreadyRunning()
    try:
        thread_id = new_thread_id()
        memory.remember_assessment(thread_id, control_id)
        try:
            count()
        except Exception:
            try:
                memory.forget_assessment(thread_id)
            except Exception:  # left in the index, it's never listed: its run never started
                log.exception("a refused assessment stays in the index")
            raise
    except Exception:
        running.release(memory.scope)
        raise
    return thread_id
