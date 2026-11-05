"""One assessment run, as the HTTP API's assessment stream and the chat's `start_assessment`
tool both make it (#45), and which users have one running.

A run's model is metered and its tool calls checked against the compliance profile's
allow-list (security layer 2); each refused call is a guard step.
"""

import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any, cast

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from vouch.allow_list import GUARD, ToolAllowList
from vouch.graph import Assessment, RequirementVerdict, assess_steps
from vouch.isa import IsaControl
from vouch.model_choice import ModelChoice
from vouch.profile import ComplianceProfile

RUNNING_ALREADY = (
    "An assessment is already running for you. Please wait until it has finished before "
    "starting another."
)


def assessment_events(
    control: IsaControl,
    profile: ComplianceProfile,
    models: ModelChoice,
    chosen: str | None,
    tools: frozenset[str],
    saver: BaseCheckpointSaver,
    meter: BaseCallbackHandler,
    *,
    thread_id: str,
    scope: str,
    note: str = "",
) -> Iterator[tuple[str, Any]]:
    """The run up to the review interrupt, as (event, data): a `step` per agent step (and per
    refused tool call), a `requirement` per judged requirement, then one `result`, the proposed
    Assessment itself. `meter` sees every model call."""
    model = ToolAllowList(models.for_assessment(chosen, callbacks=[meter]), tools)
    # assess_steps takes a BaseChatModel; ToolAllowList offers the part the graph uses.
    steps = assess_steps(
        control,
        profile,
        cast(BaseChatModel, model),
        saver,
        note=note,
        thread_id=thread_id,
        scope=scope,
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
    """Whose assessments are running in this process, by memory scope (user and profile). The
    chat starts one only when none is (`hold`); the assessment stream notes its own
    (`running`) whatever else runs, as it did before the chat could start them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: Counter[str] = Counter()

    def busy(self, scope: str) -> bool:
        with self._lock:
            return self._runs[scope] > 0

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

    @contextmanager
    def running(self, scope: str) -> Iterator[None]:
        """A run for `scope`, noted while the block runs, whatever else is running."""
        with self._lock:
            self._runs[scope] += 1
        try:
            yield
        finally:
            self.release(scope)
