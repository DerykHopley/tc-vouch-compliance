"""A run that ends before the review leaves no index entry (#137): lists and progress then never
load its thread. Before, its entry stayed in the user's index for good, loaded and dropped on
every list, and enough of them pushed real assessments past MAX_ASSESSMENTS."""

from collections.abc import Callable

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.memory import InMemoryStore

from vouch import memory as memory_module
from vouch.allow_list import allowed_tools
from vouch.fake_model import ScriptedChatModel
from vouch.graph import Assessment, memory_checkpointer
from vouch.isa import load_base_framework
from vouch.memory import Memory, newest_by_control, progress
from vouch.model_choice import MemoryModelStore, ModelChoice
from vouch.profile import DEFAULT_PROFILE, load_named_profile
from vouch.runs import AssessmentRun, RunningAssessments, assessment_events, begin_run
from vouch.settings import Settings
from vouch.spending import UsageMeter

PROFILE = load_named_profile(DEFAULT_PROFILE)
CONTROLS = {c.id: c for c in load_base_framework().controls}
MODEL_DOWN = RuntimeError("the model is down")
FAILS_MID_RUN, FAILS_AT_SETUP = "4.1.2", "5.1.1"


class Unreachable(ScriptedChatModel):
    """The scripted fake, failing every call as OpenRouter's model does after its retries (as in
    test_review)."""

    def _generate(self, *args, **kwargs):
        raise MODEL_DOWN


class FakesButBroken(ModelChoice):
    """The scripted fakes, but an Unreachable model for FAILS_MID_RUN and none to be had for
    FAILS_AT_SETUP."""

    def __init__(self) -> None:
        super().__init__(Settings(), MemoryModelStore())

    def for_assessment(
        self,
        chosen: str | None,
        callbacks: list[BaseCallbackHandler] | None = None,
        *,
        answer_language: str = "en",
        control_id: str | None = None,
    ) -> BaseChatModel:
        if control_id == FAILS_MID_RUN:
            fake = super().for_assessment(chosen, callbacks, control_id="1.1.1")
            assert isinstance(fake, ScriptedChatModel)
            return Unreachable(script=fake.script)
        if control_id == FAILS_AT_SETUP:
            raise MODEL_DOWN
        return super().for_assessment(
            chosen, callbacks, answer_language=answer_language, control_id=control_id
        )


@pytest.fixture
def memory() -> Memory:
    return Memory(InMemoryStore(), "lead-137", DEFAULT_PROFILE)


@pytest.fixture
def run(memory: Memory) -> AssessmentRun:
    saver = memory_checkpointer()
    tools = allowed_tools(PROFILE)
    return AssessmentRun(FakesButBroken(), None, tools, saver, UsageMeter(None), memory.scope)


def events_of(memory: Memory, run: AssessmentRun, control_id: str):
    thread_id = begin_run(RunningAssessments(), memory, control_id, lambda: None)
    return assessment_events(CONTROLS[control_id], PROFILE, run, memory, thread_id=thread_id)


@pytest.mark.parametrize("control_id", [FAILS_MID_RUN, FAILS_AT_SETUP])
def test_a_run_that_fails_before_the_review_is_not_in_the_index_afterwards(memory, run, control_id):
    with pytest.raises(RuntimeError, match="the model is down"):
        list(events_of(memory, run, control_id))

    assert memory.thread_ids() == []


def test_a_run_left_part_way_is_not_in_the_index_afterwards(memory, run):
    events = events_of(memory, run, "1.1.1")

    next(events)  # its first step, then the lead leaves
    events.close()

    assert memory.thread_ids() == []


def test_a_run_left_right_after_its_result_stays_in_the_index(memory, run):
    events = events_of(memory, run, "1.1.1")

    for event, _ in events:
        if event == "result":
            events.close()  # the reader stops at the result, as the chat's worker may

    assert len(memory.thread_ids()) == 1


def test_lists_and_progress_never_load_a_failed_runs_thread(memory, run, monkeypatch):
    list(events_of(memory, run, "1.1.1"))
    with pytest.raises(RuntimeError):
        list(events_of(memory, run, FAILS_MID_RUN))
    loaded: list[str] = []
    real_loader = memory_module.assessment_loader

    def counting_loader(
        saver: BaseCheckpointSaver, scope: str
    ) -> Callable[[str], Assessment | None]:
        load = real_loader(saver, scope=scope)

        def counted(thread_id: str) -> Assessment | None:
            loaded.append(thread_id)
            return load(thread_id)

        return counted

    monkeypatch.setattr(memory_module, "assessment_loader", counting_loader)

    memory.assessments(run.saver)
    newest_by_control(memory, run.saver)
    progress(memory, run.saver)

    assert set(loaded) == set(memory.thread_ids()) and len(set(loaded)) == 1
