"""A scripted chat model: plays back a JSON script, never touches the network."""

import json
from pathlib import Path
from typing import Annotated, Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Discriminator, Field, Tag, ValidationError, model_validator

from vouch.graph import SUBMIT_ANSWER, Judgement, SubmitAnswer, Verdict


class ScriptExhaustedError(RuntimeError):
    """The graph asked for more turns than the script holds."""


class ScriptError(ValueError):
    """A fake script file that can't be read or doesn't follow the script format."""

    def __init__(self, path: Path, problem: str):
        super().__init__(f"bad fake script {path}: {problem}")


class ToolTurn(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class AnswerTurn(SubmitAnswer):
    """The answer the model hands back, plus the script's `verdict`.

    The graph ignores `verdict` (the judge step decides it), but the script
    format still allows only found, partial or gap there.
    """

    verdict: Verdict | None = None


def _turn_kind(turn: Any) -> str:
    """A turn with a `tool` key is a tool turn; any other is the answer."""
    has_tool = "tool" in turn if isinstance(turn, dict) else isinstance(turn, ToolTurn)
    return "tool" if has_tool else "answer"


Turn = Annotated[
    Annotated[ToolTurn, Tag("tool")] | Annotated[AnswerTurn, Tag("answer")],
    Discriminator(_turn_kind),
]


class Script(BaseModel):
    """The script format: tool turns, then one answer turn last; an optional judge.

    Keys the format doesn't name are ignored, as they always were.
    """

    gather: list[Turn] = Field(min_length=1)
    judge: Judgement = Field(default_factory=Judgement)

    @model_validator(mode="after")
    def _answer_comes_last_and_only_last(self) -> "Script":
        is_answer = [isinstance(turn, AnswerTurn) for turn in self.gather]
        if not is_answer[-1] or any(is_answer[:-1]):
            raise ValueError("the last gather turn, and only the last, must be the answer")
        return self


def _load_script(path: Path) -> Script:
    try:
        raw = json.loads(path.read_text())
    except OSError as error:
        raise ScriptError(path, error.strerror or "can't be read") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ScriptError(path, f"not valid JSON ({error})") from error
    try:
        return Script.model_validate(raw)
    except ValidationError as error:
        first = error.errors()[0]  # one line on stderr, so the first problem only
        where = ".".join(str(part) for part in first["loc"]) or "script"
        raise ScriptError(path, f"{where}: {first['msg']}") from error


class ScriptedChatModel(BaseChatModel):
    """Plays the script's `gather` turns in order, one per model call.

    A tool turn becomes a tool call; the answer turn becomes a call to the
    `submit_answer` tool, the same way a real model hands back its answer.
    """

    script: Script
    turn: int = 0

    @classmethod
    def from_file(cls, path: str | Path) -> "ScriptedChatModel":
        """Load and check a script file; a bad one raises ScriptError naming the path."""
        return cls(script=_load_script(Path(path)))

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        """The script already says which tool each turn calls, so the tools offered are ignored."""
        return self

    def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> Runnable:
        """The judge's structured output is the script's `judge` entry."""
        return RunnableLambda(lambda _: schema.model_validate(self.script.judge.model_dump()))

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        gather = self.script.gather
        if self.turn >= len(gather):
            raise ScriptExhaustedError(f"script has {len(gather)} gather turns")
        step = gather[self.turn]
        self.turn += 1
        if isinstance(step, ToolTurn):
            name, args = step.tool, step.args
        else:
            name, args = SUBMIT_ANSWER, step.model_dump(include=set(SubmitAnswer.model_fields))
        call = {"name": name, "args": args, "id": f"call_{self.turn}", "type": "tool_call"}
        return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=[call]))])
