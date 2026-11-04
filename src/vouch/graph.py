"""The assessment graph: one VDA ISA control under one compliance profile in, one Assessment out.

The agent gathers evidence (ReAct over `search_policies`), drafts an answer, and judges each
applicable requirement. The control's verdict rolls up from those verdicts in code, never from
the model.
"""

import json
import operator
import re
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from vouch.isa import Column, IsaControl, Requirement, load_isa_control
from vouch.profile import ComplianceProfile, ControlRequirements
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
PROMPT_VERSION = "m1-judge-requirements-1"  # change it whenever a prompt below changes

Verdict = Literal["evidenced", "partly-evidenced", "not-evidenced", "not-applicable"]
JUDGED_VERDICTS: tuple[Verdict, ...] = ("evidenced", "partly-evidenced", "not-evidenced")
NOT_APPLICABLE: Verdict = "not-applicable"
NOT_JUDGED = "not judged"  # the reason given when the model skipped a requirement or misnamed it
NO_SOURCE = "no evidence source could be read"
# The columns the roll-up counts: should requirements only give the should score.
ROLL_UP_COLUMNS: frozenset[Column] = frozenset({"must", "high", "very high", "overlay"})
# Vouch prepares for an audit; it never claims the audit's result (GLOSSARY.md, "Verdict").
# A negation ("non-compliant", "not certified") and handing something on ("passed to the
# CISO") are not claims. English only: German answers come with the second profile (M4).
AUDIT_RESULT = re.compile(
    r"(?<!non-)(?<!non )(?<!not )\b(compliant|certified|passed(?!\s+(?:to|on)\b))\b",
    re.IGNORECASE,
)
OUTPUT_RETRIES = 1  # an answer or a judgement claiming an audit result is asked for once more
USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")


class OutputCheckError(RuntimeError):
    """The answer or a judgement's reason still claimed an audit result after one retry."""


def claims_audit_result(text: str) -> str | None:
    """The word with which the text claims an audit result (lower case), or None."""
    found = AUDIT_RESULT.search(text)
    return found.group(1).lower() if found else None


class SubmitAnswer(BaseModel):
    """Hand back the final answer for the control. The verdicts are decided by the judge step."""

    answer: str = Field(description="A short draft answer for an auditor.")
    cited: list[str] = Field(default_factory=list, description="doc_ids the answer relies on.")


class RequirementJudgement(BaseModel):
    requirement_id: str = Field(description="The requirement's id, such as M1 or S2.")
    verdict: str = Field(description="evidenced, partly-evidenced or not-evidenced.")
    reason: str = Field(description="One sentence saying why, from the evidence.")


class Judgement(BaseModel):
    """A verdict for each requirement listed, judged from the evidence alone."""

    requirements: list[RequirementJudgement] = Field(default_factory=list)


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


@dataclass(frozen=True)
class RequirementVerdict:
    """One requirement of the control and its verdict, with the reason."""

    id: str
    column: Column
    text: str
    verdict: Verdict
    reason: str


@dataclass(frozen=True)
class ShouldScore:
    """How many applicable should requirements are evidenced, of how many ("should 3 of 4")."""

    evidenced: int
    total: int


@dataclass
class Assessment:
    control_id: str
    profile_id: str
    verdict: Verdict  # the control's, rolled up from its requirements
    should_score: ShouldScore
    requirements: list[RequirementVerdict]
    answer: str
    sources: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steps: list[dict[str, str]] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    prompt_version: str = PROMPT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def roll_up(requirements: Iterable[RequirementVerdict]) -> Verdict:
    """The control's verdict, over must, high, very high and overlay requirements, leaving out
    not applicable ones: all evidenced gives evidenced; none evidenced (only partly or not
    evidenced) gives not-evidenced; otherwise partly-evidenced. With none of those left, the
    control is not applicable."""
    counted = [
        r.verdict
        for r in requirements
        if r.column in ROLL_UP_COLUMNS and r.verdict != NOT_APPLICABLE
    ]
    if not counted:
        return NOT_APPLICABLE
    if all(v == "evidenced" for v in counted):
        return "evidenced"
    if not any(v == "evidenced" for v in counted):
        return "not-evidenced"
    return "partly-evidenced"


def should_score(requirements: Iterable[RequirementVerdict]) -> ShouldScore:
    """How many applicable should requirements are evidenced, of how many."""
    should = [r for r in requirements if r.column == "should" and r.verdict != NOT_APPLICABLE]
    return ShouldScore(evidenced=sum(r.verdict == "evidenced" for r in should), total=len(should))


class AssessState(TypedDict, total=False):
    control: IsaControl
    control_requirements: ControlRequirements
    evidence: Annotated[list[Passage], operator.add]
    warnings: Annotated[list[str], operator.add]
    usage: Annotated[list[dict[str, int]], operator.add]
    answer: str | None
    answer_retries: int
    rejected_word: str | None  # the audit-result word that made check_answer refuse the answer
    judge_rejected_word: str | None  # the one that made the judge ask for a new judgement
    cited: list[str]
    sources: list[dict[str, str]]
    requirements: list[RequirementVerdict]
    not_judged: list[str]
    messages: Annotated[list[AnyMessage], add_messages]


def _format_requirements(requirements: Iterable[Requirement]) -> str:
    return "\n".join(f"- {r.id} ({r.column}): {r.text}" for r in requirements)


def _prompt(
    control: IsaControl, control_requirements: ControlRequirements, profile: ComplianceProfile
) -> list[AnyMessage]:
    return [
        SystemMessage(
            f"You assess one {control.source} control for {profile.company} against the "
            f"company's own evidence. Use {SEARCH_POLICIES} to find evidence. When you are "
            f"done, call {SUBMIT_ANSWER} with a short draft answer for an auditor, written in "
            f"the language with ISO 639-1 code '{profile.answer_language}', and the doc_ids "
            "you cite. Say what the evidence shows; never call the company compliant, "
            "certified or passed."
        ),
        HumanMessage(
            f"Control {control.id}: {control.question}\n"
            f"Requirements:\n{_format_requirements(control_requirements.applicable)}"
        ),
    ]


def _judge_prompt(state: AssessState) -> list[AnyMessage]:
    control = state["control"]
    evidence = "\n".join(f"[{p['doc_id']} / {p['heading']}] {p['text']}" for p in state["evidence"])
    return [
        SystemMessage(
            "Judge each requirement listed from the evidence alone, not the draft answer. "
            "Give each one verdict: evidenced (the evidence fully shows it), partly-evidenced "
            "(it shows some of it) or not-evidenced (it shows none of it), and one sentence "
            "saying why."
        ),
        HumanMessage(
            f"Control {control.id}: {control.question}\n"
            f"Requirements:\n{_format_requirements(state['control_requirements'].applicable)}\n\n"
            f"Evidence:\n{evidence or '(none)'}\n\nDraft answer: {state.get('answer', '')}"
        ),
    ]


def _judged_requirements(
    control_requirements: ControlRequirements,
    judgement: Judgement | None,
    unjudged_reason: str = NOT_JUDGED,
) -> tuple[list[RequirementVerdict], list[str]]:
    """Each of the control's requirements with its verdict, and the ids the model didn't judge
    validly.

    A requirement the model skipped, or gave a verdict outside JUDGED_VERDICTS, is
    not-evidenced with `unjudged_reason`. Judgements for ids the control doesn't apply are
    ignored. Not applicable requirements follow, with the profile's reason.
    """
    given: dict[str, RequirementJudgement] = {}
    for item in judgement.requirements if judgement else []:
        given.setdefault(item.requirement_id, item)
    results, not_judged = [], []
    for req in control_requirements.applicable:
        item = given.get(req.id)
        if item is not None and item.verdict in JUDGED_VERDICTS:
            verdict, reason = item.verdict, item.reason
        else:
            verdict, reason = "not-evidenced", unjudged_reason
            not_judged.append(req.id)
        results.append(RequirementVerdict(req.id, req.column, req.text, verdict, reason))
    for excluded in control_requirements.not_applicable:
        req = excluded.requirement
        results.append(
            RequirementVerdict(req.id, req.column, req.text, NOT_APPLICABLE, excluded.reason)
        )
    return results, not_judged


def _usage_of(message: Any) -> list[dict[str, int]]:
    """The message's token usage as a one-item list to add to the state, or none."""
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return []
    return [{k: usage[k] for k in USAGE_KEYS}]


def build_graph(model: BaseChatModel):
    """Compile the graph around a model passed in by the caller."""
    bound = model.bind_tools([SEARCH_POLICIES_TOOL, SUBMIT_ANSWER_TOOL])
    judge_model = model.with_structured_output(Judgement, include_raw=True)

    def gather(state: AssessState) -> dict[str, Any]:
        reply = bound.invoke(state["messages"])
        update: dict[str, Any] = {"messages": [reply], "usage": _usage_of(reply)}
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

    def check_answer(state: AssessState) -> dict[str, Any]:
        """The answer may not claim an audit result: asked for again once, then an error.

        A refusal answers every tool call of the model's message, as a provider requires
        before the next call; a search asked for alongside the answer is not run.
        """
        word = claims_audit_result(state["answer"] or "")
        if word is None:
            return {"rejected_word": None}
        retries = state.get("answer_retries", 0)
        if retries >= OUTPUT_RETRIES:
            raise OutputCheckError(f"the answer claimed an audit result (“{word}”) again")
        refusal = (
            f"Answer refused: it says “{word}”, an audit result Vouch never claims. "
            f"Say what the evidence shows instead, and call {SUBMIT_ANSWER} again."
        )
        not_run = "Not run: the answer in the same message was refused. Call it again if needed."
        replies = [
            ToolMessage(
                refusal if call["name"] == SUBMIT_ANSWER else not_run, tool_call_id=call["id"]
            )
            for call in state["messages"][-1].tool_calls
        ]
        return {
            "answer": None,
            "answer_retries": retries + 1,
            "rejected_word": word,
            "messages": replies,
        }

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
        """The model judges each requirement; the code keeps only valid verdicts for them.

        A reason claiming an audit result gets the judgement asked for again once, then an error.
        """
        control_requirements = state["control_requirements"]
        if SOURCE_UNAVAILABLE in state["warnings"]:  # nothing could be read, so nothing shown
            results, _ = _judged_requirements(control_requirements, None, NO_SOURCE)
            return {"requirements": results, "not_judged": []}
        prompt, usage, rejected = _judge_prompt(state), [], None
        for attempt in range(OUTPUT_RETRIES + 1):
            reply = judge_model.invoke(prompt)
            usage += _usage_of(reply["raw"])
            results, not_judged = _judged_requirements(control_requirements, reply["parsed"])
            judged = [r for r in results if r.verdict != NOT_APPLICABLE]
            word = next((w for r in judged if (w := claims_audit_result(r.reason))), None)
            if word is None:
                break
            if attempt == OUTPUT_RETRIES:
                raise OutputCheckError(
                    f"a judgement's reason claimed an audit result (“{word}”) again"
                )
            rejected = word
            prompt = [
                *prompt,
                HumanMessage(
                    f"Judgement refused: a reason says “{word}”, an audit result Vouch never "
                    "claims. Judge again, saying only what the evidence shows."
                ),
            ]
        return {
            "requirements": results,
            "not_judged": not_judged,
            "judge_rejected_word": rejected,
            "usage": usage,
        }

    def after_gather(state: AssessState) -> str:
        return "check_answer" if state.get("answer") is not None else "tools"

    def after_check(state: AssessState) -> str:
        return "cite" if state.get("answer") is not None else "gather"

    graph = StateGraph(AssessState)
    graph.add_node("gather", gather)
    graph.add_node("tools", tools)
    graph.add_node("check_answer", check_answer)
    graph.add_node("cite", cite)
    graph.add_node("judge", judge)
    graph.add_edge(START, "gather")
    graph.add_conditional_edges("gather", after_gather, ["tools", "check_answer"])
    graph.add_edge("tools", "gather")
    graph.add_conditional_edges("check_answer", after_check, ["gather", "cite"])
    graph.add_edge("cite", "judge")
    graph.add_edge("judge", END)
    return graph.compile()


@dataclass(frozen=True)
class Step:
    """One node of the graph finished: its name and the state update it made."""

    node: str
    update: dict[str, Any]

    def describe(self) -> str:
        """One plain sentence, in the glossary's words, saying what the step did."""
        update = self.update
        if self.node == "gather":
            calls = update["messages"][-1].tool_calls if update.get("messages") else []
            queries = [c["args"].get("query", "") for c in calls if c["name"] == SEARCH_POLICIES]
            if queries:
                return "Searching the policies for " + ", ".join(f"“{q}”" for q in queries) + "."
            if any(c["name"] == SUBMIT_ANSWER for c in calls):
                cited = _plural(len(update.get("cited", [])), "document")
                return f"Drafted an answer citing {cited}."
        if self.node == "tools":
            if update.get("warnings"):
                return "The policy folder could not be read."
            return f"Found {_plural(len(update.get('evidence', [])), 'passage')}."
        if self.node == "check_answer":
            word = update.get("rejected_word")
            if word:
                return f"The answer claimed an audit result (“{word}”), so it was asked for again."
            return "Checked the answer: it claims no audit result."
        if self.node == "cite":
            return f"Kept {_plural(len(update['sources']), 'cited passage')}."
        if self.node == "judge":
            judged = [r for r in update["requirements"] if r.verdict != NOT_APPLICABLE]
            evidenced = sum(r.verdict == "evidenced" for r in judged)
            text = f"Judged {evidenced} of {len(judged)} requirements evidenced."
            word = update.get("judge_rejected_word")
            if word:
                text = (
                    f"A reason claimed an audit result (“{word}”), so the judgement was asked "
                    f"for again. {text}"
                )
            if update.get("not_judged"):
                text += " Not judged: " + ", ".join(update["not_judged"]) + "."
            return text
        return f"Finished the {self.node} step."


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _model_name(model: BaseChatModel) -> str:
    return getattr(model, "model_name", None) or model._llm_type


def _total_usage(usage: list[dict[str, int]]) -> dict[str, int]:
    return {k: sum(u[k] for u in usage) for k in USAGE_KEYS}


def assess_steps(
    control: IsaControl, profile: ComplianceProfile, model: BaseChatModel
) -> Iterator[Step | RequirementVerdict | Assessment]:
    """Run the graph for one loaded control: a Step per node as it finishes, each
    RequirementVerdict once judged, then the Assessment."""
    control_requirements = profile.requirements_for(control)
    initial = {
        "control": control,
        "control_requirements": control_requirements,
        "evidence": [],
        "warnings": [],
        "usage": [],
        "messages": _prompt(control, control_requirements, profile),
    }
    final: dict[str, Any] = {}
    steps: list[dict[str, str]] = []
    for mode, chunk in build_graph(model).stream(initial, stream_mode=["updates", "values"]):
        if mode == "values":
            final = chunk
            continue
        for node, update in chunk.items():
            step = Step(node, update or {})
            steps.append({"node": node, "text": step.describe()})
            yield step
            if node == "judge":
                yield from update["requirements"]
    requirements = final["requirements"]
    yield Assessment(
        control_id=control.id,
        profile_id=profile.id,
        verdict=roll_up(requirements),
        should_score=should_score(requirements),
        requirements=requirements,
        answer=final["answer"],
        sources=final["sources"],
        warnings=list(dict.fromkeys(final["warnings"])),
        steps=steps,
        token_usage=_total_usage(final["usage"]),
        model=_model_name(model),
    )


def assess(control_id: str, profile: ComplianceProfile, model: BaseChatModel) -> Assessment:
    """Run the graph for one control under a compliance profile and return its Assessment.

    An unknown control raises UnknownControlError before the model is called."""
    *_, assessment = assess_steps(load_isa_control(control_id), profile, model)
    if not isinstance(assessment, Assessment):
        raise TypeError(f"the graph ended without an Assessment for {control_id}")
    return assessment
