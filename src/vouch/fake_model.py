"""A scripted chat model: plays back a JSON script, never touches the network."""

import json
import math
from pathlib import Path
from typing import Annotated, Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Discriminator, Field, Tag, ValidationError, model_validator

from vouch.graph import SUBMIT_ANSWER, Judgement, RemediationPlan, SubmitAnswer


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
    """The answer the model hands back. The judge step, not the answer, decides verdicts."""


def _turn_kind(turn: Any) -> str:
    """A turn with a `tool` key is a tool turn; any other is the answer."""
    has_tool = "tool" in turn if isinstance(turn, dict) else isinstance(turn, ToolTurn)
    return "tool" if has_tool else "answer"


Turn = Annotated[
    Annotated[ToolTurn, Tag("tool")] | Annotated[AnswerTurn, Tag("answer")],
    Discriminator(_turn_kind),
]


class Script(BaseModel):
    """The script format: tool turns and answer turns, an answer last; an optional judge and
    an optional remediation plan.

    An answer turn before the last one is played when the graph asks for the answer again
    (the output check refused it). `judge` is one judgement, or a list played in turn when the
    graph asks for a judgement again; the last one repeats. Its verdicts are any strings, as a
    real model's can be; the graph decides what an unknown one means. Keys the format doesn't
    name are ignored, as they always were. `remediate` works as `judge` does, for the drafts of
    remediation items.
    """

    gather: list[Turn] = Field(min_length=1)
    judge: Judgement | list[Judgement] = Field(default_factory=Judgement)
    remediate: RemediationPlan | list[RemediationPlan] = Field(default_factory=RemediationPlan)

    def replies_for(self, schema: type[BaseModel]) -> list[BaseModel]:
        """The structured replies scripted for a schema: the plans for a RemediationPlan, the
        judgements for anything else (the judge's)."""
        replies = self.remediate if schema is RemediationPlan else self.judge
        return replies if isinstance(replies, list) else [replies]

    @model_validator(mode="after")
    def _answer_comes_last(self) -> "Script":
        ends_with_answer = isinstance(self.gather[-1], AnswerTurn)
        if not ends_with_answer:  # a ValueError, so pydantic reports it as a validation error
            raise ValueError("the last gather turn must be an answer")
        if not self.replies_for(Judgement):
            raise ValueError("judge: a list of judgements needs at least one")
        if not self.replies_for(RemediationPlan):
            raise ValueError("remediate: a list of plans needs at least one")
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


CHARS_PER_TOKEN = 4
"""The fake's token counts: about four characters a token, as for English text."""


def _tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _usage(messages: list[BaseMessage], written: str) -> UsageMetadata:
    """Token counts for one call, as a real model reports them (#35: usage and cost)."""
    sent = _tokens("".join(str(m.content) for m in messages))
    out = _tokens(written)
    return UsageMetadata(input_tokens=sent, output_tokens=out, total_tokens=sent + out)


class ScriptedChatModel(BaseChatModel):
    """Plays the script's `gather` turns in order, one per model call.

    A tool turn becomes a tool call; the answer turn becomes a call to the
    `submit_answer` tool, the same way a real model hands back its answer. Each reply reports
    token counts (`usage_metadata`) estimated from the text sent and written.
    """

    script: Script
    turn: int = 0
    turns: dict[Any, int] = Field(default_factory=dict)  # structured replies given, per schema
    prompts: list[list[BaseMessage]] = Field(default_factory=list)  # each call's messages, in order

    @classmethod
    def from_file(
        cls, path: str | Path, callbacks: list[BaseCallbackHandler] | None = None
    ) -> "ScriptedChatModel":
        """Load and check a script file; a bad one raises ScriptError naming the path.
        `callbacks` see every call (a usage meter)."""
        return cls(script=_load_script(Path(path)), callbacks=callbacks)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        """The script already says which tool each turn calls, so the tools offered are ignored."""
        return self

    def with_structured_output(
        self, schema: type[BaseModel], *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable:
        """The structured output is the script's next judgement, or for a RemediationPlan its
        next plan (the last repeats), from a model call made with this model's callbacks (so the
        usage meter sees it); with `include_raw`, a dict with the raw message (carrying token
        usage), as LangChain's models give."""
        call = ScriptedReply(source=self, reply_schema=schema, callbacks=self.callbacks)

        def reply(messages: list[BaseMessage]) -> Any:
            raw = call.invoke(messages)
            parsed = schema.model_validate_json(raw.content)
            return {"raw": raw, "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return RunnableLambda(reply)

    def next_reply(self, schema: type[BaseModel], messages: list[BaseMessage]) -> BaseModel:
        """The script's next structured reply for `schema`; the last one repeats."""
        self.prompts.append(list(messages))
        replies = self.script.replies_for(schema)
        turn = self.turns.get(schema, 0)
        self.turns[schema] = turn + 1
        return schema.model_validate(replies[min(turn, len(replies) - 1)].model_dump())

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.prompts.append(list(messages))
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
        reply = AIMessage("", tool_calls=[call], usage_metadata=_usage(messages, json.dumps(args)))
        return ChatResult(generations=[ChatGeneration(message=reply)])


class ScriptedReply(BaseChatModel):
    """A structured-output call (the judge's, the remediation drafts'): answers with the
    source model's next scripted reply for `reply_schema` as JSON, a model call like any other,
    so its callbacks (the usage meter) see it."""

    source: ScriptedChatModel
    reply_schema: type[BaseModel]

    @property
    def _llm_type(self) -> str:
        return "scripted-fake-judge"

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        content = self.source.next_reply(self.reply_schema, messages).model_dump_json()
        reply = AIMessage(content, usage_metadata=_usage(messages, content))
        return ChatResult(generations=[ChatGeneration(message=reply)])
