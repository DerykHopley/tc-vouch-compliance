"""A run that ends before the review leaves no index entry (#137): lists and progress then never
load its thread. Before, its entry stayed in the user's index for good, loaded and dropped on
every list, and enough of them pushed real assessments past MAX_ASSESSMENTS."""

from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatResult
from langgraph.store.memory import InMemoryStore

from vouch import memory as memory_module
from vouch.allow_list import allowed_tools
from vouch.graph import memory_checkpointer
from vouch.isa import load_base_framework
from vouch.memory import Memory, newest_by_control, progress
from vouch.model_choice import MemoryModelStore, ModelChoice
from vouch.profile import DEFAULT_PROFILE, load_named_profile
from vouch.runs import AssessmentRun, RunningAssessments, assessment_events, begin_run
from vouch.settings import Settings
from vouch.spending import UsageMeter

CONTROLS = {c.id: c for c in load_base_framework().controls}
TOOLS = allowed_tools(load_named_profile(DEFAULT_PROFILE))


class BrokenModel(BaseChatModel):
    """A model whose every call fails, as OpenRouter's does after its retries."""

    @property
    def _llm_type(self) -> str:
        return "broken"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "BrokenModel":
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        raise RuntimeError("the model is down")


class Models(ModelChoice):
    """The fakes, but a broken model for the controls in `broken`."""

    def __init__(self, broken: set[str]) -> None:
        super().__init__(Settings(), MemoryModelStore())
        self.broken = broken

    def for_assessment(self, chosen, callbacks=None, *, answer_language="en", control_id=None):
        if control_id in self.broken:
            return BrokenModel()
        return super().for_assessment(
            chosen, callbacks, answer_language=answer_language, control_id=control_id
        )


@pytest.fixture
def lead():
    memory = Memory(InMemoryStore(), "lead-137", DEFAULT_PROFILE)
    saver = memory_checkpointer()
    run = AssessmentRun(Models({"4.1.2"}), None, TOOLS, saver, UsageMeter(None), memory.scope)
    return memory, run


def started(memory: Memory, control_id: str) -> str:
    return begin_run(RunningAssessments(), memory, control_id, lambda: None)


def run_through(memory: Memory, run: AssessmentRun, control_id: str) -> list[str]:
    thread_id = started(memory, control_id)
    events = assessment_events(
        CONTROLS[control_id], load_named_profile(DEFAULT_PROFILE), run, memory, thread_id=thread_id
    )
    return [event for event, _ in events]


def test_a_run_that_fails_before_the_review_is_not_in_the_index_afterwards(lead):
    memory, run = lead

    with pytest.raises(RuntimeError, match="the model is down"):
        run_through(memory, run, "4.1.2")

    assert memory.thread_ids() == []


def test_a_run_left_part_way_is_not_in_the_index_afterwards(lead):
    memory, run = lead
    thread_id = started(memory, "1.1.1")
    events = assessment_events(
        CONTROLS["1.1.1"], load_named_profile(DEFAULT_PROFILE), run, memory, thread_id=thread_id
    )

    next(events)  # its first step, then the lead leaves
    events.close()

    assert memory.thread_ids() == []


def test_a_run_that_reaches_the_review_stays_in_the_index(lead):
    memory, run = lead

    assert run_through(memory, run, "1.1.1")[-1] == "result"

    assert len(memory.thread_ids()) == 1


def test_lists_and_progress_never_load_a_failed_runs_thread(lead, monkeypatch):
    memory, run = lead
    run_through(memory, run, "1.1.1")
    with pytest.raises(RuntimeError):
        run_through(memory, run, "4.1.2")
    loaded: list[str] = []
    real = memory_module.assessment_loader

    def counting(saver, scope):
        load = real(saver, scope=scope)
        return lambda thread_id: (loaded.append(thread_id), load(thread_id))[1]

    monkeypatch.setattr(memory_module, "assessment_loader", counting)

    listed = memory.assessments(run.saver)
    newest = newest_by_control(memory, run.saver)
    progress(memory, run.saver)

    assert [a.control_id for a in listed] == ["1.1.1"] and set(newest) == {"1.1.1"}
    assert len(set(loaded)) == 1  # the one that reached the review, never the failed one
