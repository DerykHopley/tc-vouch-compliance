"""Security layer 2 at the API: every tool call the model makes, checked against the allow-list.

The API hands the graph its model wrapped in ToolAllowList, so a call outside the allow-list
is stopped before the graph can run it, whatever the graph's own guards do.
"""

import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda

from vouch.graph import EVIDENCE_TOOLS, GUARD, SUBMIT_ANSWER
from vouch.guards import refusal_step
from vouch.profile import ComplianceProfile

__all__ = ["GUARD", "NOT_ALLOWED", "REFUSED_TOOL", "ToolAllowList", "allowed_tools"]

REFUSED_TOOL = "refused_tool"
"""A refused call's new name. The graph's tools node answers a call to a name it doesn't know
with "tool <name> is not available" and runs nothing (vouch.graph, build_graph's `tools`), so
the model still gets its call answered."""
NOT_ALLOWED = "it isn't on the compliance profile's allow-list"
_UNSAFE_NAME = re.compile(r"[^\w.-]")
"""A tool name is the model's own text: shown in a step, it keeps to word characters."""


def allowed_tools(profile: ComplianceProfile) -> frozenset[str]:
    """The compliance profile's allow-list as tool names: each evidence-source tool whose
    source the profile allows, and the answer. The graph's own guard (vouch.guards,
    allowed_folder) asks the same question of the same table (vouch.graph.EVIDENCE_TOOLS), so
    the two checks agree."""
    sources = profile.evidence_sources
    evidence = {name for name, tool in EVIDENCE_TOOLS.items() if tool.source in sources}
    return frozenset({*evidence, SUBMIT_ANSWER})


class ToolAllowList:
    """The model, with every tool call it makes checked against `allowed`.

    A call outside it is renamed REFUSED_TOOL before the graph sees it, so nothing runs. Each
    refusal leaves a guard step's sentence, which the API streams (take_refusals).
    """

    def __init__(self, model: BaseChatModel, allowed: frozenset[str]) -> None:
        self._model = model
        self._allowed = allowed
        self._refusals: list[str] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:
        return self._model.bind_tools(tools, **kwargs) | RunnableLambda(self._checked)

    @property
    def model_name(self) -> str:
        """The wrapped model's name, which the Assessment records (vouch.graph, _model_name)."""
        return getattr(self._model, "model_name", None) or self._model._llm_type

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:
        """Structured output (the judge) runs no tool, so there's nothing to check."""
        return self._model.with_structured_output(schema, **kwargs)

    def take_refusals(self) -> list[str]:
        """The guard steps' sentences since the last take, oldest first."""
        refusals, self._refusals = self._refusals, []
        return refusals

    def _checked(self, reply: AIMessage) -> AIMessage:
        calls = []
        for call in reply.tool_calls:
            if call["name"] in self._allowed:
                calls.append(call)
            else:
                name = _UNSAFE_NAME.sub("?", call["name"])[:64]
                self._refusals.append(refusal_step(name, NOT_ALLOWED))
                calls.append({**call, "name": REFUSED_TOOL})
        if calls == reply.tool_calls:
            return reply
        return reply.model_copy(update={"tool_calls": calls})
