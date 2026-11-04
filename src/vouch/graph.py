"""The assessment graph: one control in, one Assessment out."""

import json
import operator
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from vouch.controls import Control, EvidencePoint
from vouch.search import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_QUERY_CHARS,
    SOURCE_UNAVAILABLE,
    Passage,
    search_policies,
)

SUBMIT_ANSWER = "submit_answer"
SEARCH_POLICIES = "search_policies"
Verdict = Literal["found", "partial", "gap"]


class SubmitAnswer(BaseModel):
    """Hand back the final answer for the control. The verdict is decided by the judge step."""

    answer: str = Field(description="A short draft answer for an auditor.")
    cited: list[str] = Field(default_factory=list, description="doc_ids the answer relies on.")


class Judgement(BaseModel):
    """Which of the control's evidence points the gathered evidence covers."""

    covered_points: list[str] = Field(
        default_factory=list, description="Ids of the evidence points the evidence covers."
    )


SUBMIT_ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": SUBMIT_ANSWER,
        "description": SubmitAnswer.__doc__,
        "parameters": SubmitAnswer.model_json_schema(),
    },
}

SEARCH_POLICIES_TOOL = {
    "type": "function",
    "function": {
        "name": SEARCH_POLICIES,
        "description": "Search the company's policies by keyword; returns passages with "
        "doc_id, heading and text.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                    "default": DEFAULT_LIMIT,
                },
            },
            "required": ["query"],
        },
    },
}


@dataclass
class Assessment:
    control_id: str
    verdict: Verdict
    answer: str
    sources: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    covered_points: list[str] = field(default_factory=list)
    missing_points: list[str] = field(default_factory=list)
    gap_note: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AssessState(TypedDict, total=False):
    control: Control
    evidence: Annotated[list[Passage], operator.add]
    warnings: Annotated[list[str], operator.add]
    answer: str | None
    verdict: Verdict | None
    cited: list[str]
    sources: list[dict[str, str]]
    covered_points: list[str]
    missing_points: list[str]
    gap_note: dict[str, Any] | None
    messages: Annotated[list[AnyMessage], add_messages]


def _format_points(control: Control) -> str:
    return "\n".join(f"- {p.id}: {p.text}" for p in control.evidence_points)


def _prompt(control: Control) -> list[AnyMessage]:
    points = _format_points(control)
    return [
        SystemMessage(
            "You assess one compliance control against the company's policies. "
            f"Use {SEARCH_POLICIES} to find evidence. "
            f"When you are done, call {SUBMIT_ANSWER} with your answer and the doc_ids you cite."
        ),
        HumanMessage(
            f"Control {control.id}: {control.title}\n{control.statement}\nEvidence points:\n{points}"
        ),
    ]


def _judge_prompt(state: AssessState) -> list[AnyMessage]:
    control = state["control"]
    points = _format_points(control)
    evidence = "\n".join(f"[{p['doc_id']} / {p['heading']}] {p['text']}" for p in state["evidence"])
    return [
        SystemMessage(
            "Decide which evidence points the evidence covers. Name only points the "
            "evidence itself supports, not the draft answer."
        ),
        HumanMessage(
            f"Control {control.id}: {control.statement}\nEvidence points:\n{points}\n\n"
            f"Evidence:\n{evidence or '(none)'}\n\nDraft answer: {state.get('answer', '')}"
        ),
    ]


def _verdict_from(points: tuple[EvidencePoint, ...], named_ids: list[str]) -> dict[str, Any]:
    """Covered and missing ids in the control's order, and the verdict they give."""
    named = set(named_ids)
    covered = [p.id for p in points if p.id in named]
    missing = [p.id for p in points if p.id not in named]
    verdict: Verdict = "found" if not missing else "partial" if covered else "gap"
    return {"covered_points": covered, "missing_points": missing, "verdict": verdict}


def build_graph(model: BaseChatModel):
    """Compile the graph around a model passed in by the caller."""
    bound = model.bind_tools([SEARCH_POLICIES_TOOL, SUBMIT_ANSWER_TOOL])
    judge_model = model.with_structured_output(Judgement)

    def gather(state: AssessState) -> dict[str, Any]:
        reply = bound.invoke(state["messages"])
        update: dict[str, Any] = {"messages": [reply]}
        for call in reply.tool_calls:
            if call["name"] == SUBMIT_ANSWER:
                answer = SubmitAnswer.model_validate(call["args"])
                update.update(answer=answer.answer, cited=answer.cited)
        return update

    def tools(state: AssessState) -> dict[str, Any]:
        messages, evidence, warnings = [], [], []
        for call in state["messages"][-1].tool_calls:
            if call["name"] == SEARCH_POLICIES:
                args = call["args"]
                result = search_policies(args.get("query"), args.get("limit", DEFAULT_LIMIT))
                if isinstance(result, list):
                    evidence.extend(result)
                elif result["error"] == SOURCE_UNAVAILABLE:
                    warnings.append(SOURCE_UNAVAILABLE)
                content = json.dumps(result)
            else:
                content = f"tool {call['name']} is not available"
            messages.append(ToolMessage(content, tool_call_id=call["id"]))
        return {"messages": messages, "evidence": evidence, "warnings": warnings}

    def cite(state: AssessState) -> dict[str, Any]:
        """Keep only cited passages a search returned."""
        cited = set(state.get("cited", []))
        sources: list[dict[str, str]] = []
        for passage in state["evidence"]:
            source = {"doc_id": passage["doc_id"], "heading": passage["heading"]}
            if passage["doc_id"] in cited and source not in sources:
                sources.append(source)
        return {"sources": sources}

    def judge(state: AssessState) -> dict[str, Any]:
        """The model names the covered points; the verdict follows from them, not from it."""
        if SOURCE_UNAVAILABLE in state["warnings"]:
            named_ids: list[str] = []  # no source could be read, so nothing is covered
        else:
            named_ids = judge_model.invoke(_judge_prompt(state)).covered_points
        return _verdict_from(state["control"].evidence_points, named_ids)

    def escalate(state: AssessState) -> dict[str, Any]:
        control = state["control"]
        return {
            "gap_note": {
                "control_id": control.id,
                "missing_points": state["missing_points"],
                "owner_role": control.owner_role,
            }
        }

    def after_gather(state: AssessState) -> str:
        return "cite" if state.get("answer") is not None else "tools"

    def after_judge(state: AssessState) -> str:
        return "escalate" if state["verdict"] == "gap" else END

    graph = StateGraph(AssessState)
    graph.add_node("gather", gather)
    graph.add_node("tools", tools)
    graph.add_node("cite", cite)
    graph.add_node("judge", judge)
    graph.add_node("escalate", escalate)
    graph.add_edge(START, "gather")
    graph.add_conditional_edges("gather", after_gather, ["tools", "cite"])
    graph.add_edge("tools", "gather")
    graph.add_edge("cite", "judge")
    graph.add_conditional_edges("judge", after_judge, ["escalate", END])
    graph.add_edge("escalate", END)
    return graph.compile()


def assess(control: Control, model: BaseChatModel) -> Assessment:
    """Run the graph for one control and return its Assessment."""
    final = build_graph(model).invoke(
        {"control": control, "evidence": [], "warnings": [], "messages": _prompt(control)}
    )
    return Assessment(
        control_id=control.id,
        verdict=final["verdict"],
        answer=final["answer"],
        sources=final["sources"],
        warnings=list(dict.fromkeys(final["warnings"])),
        covered_points=final["covered_points"],
        missing_points=final["missing_points"],
        gap_note=final.get("gap_note"),
    )
