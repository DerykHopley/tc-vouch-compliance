"""The assessment graph: one VDA ISA control under one compliance profile in, one Assessment out.

The agent gathers evidence (ReAct over `search_policies`), drafts an answer, and judges each
applicable requirement. The control's verdict rolls up from those verdicts in code, never from
the model.
"""

import json
import operator
import re
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Annotated, Any, Literal, TypedDict, get_args

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field, StringConstraints

from vouch.isa import Column, IsaControl, Requirement, load_isa_control
from vouch.profile import ComplianceProfile, ControlRequirements, NotApplicableRequirement
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
PROMPT_VERSION = "m1-remediation-review-1"  # change it whenever a prompt below changes

JudgedVerdict = Literal["evidenced", "partly-evidenced", "not-evidenced"]
Verdict = JudgedVerdict | Literal["not-applicable"]
JUDGED_VERDICTS: tuple[JudgedVerdict, ...] = get_args(JudgedVerdict)
NOT_APPLICABLE: Verdict = "not-applicable"
Decision = Literal["accept", "reject"]
NOT_JUDGED = "not judged"  # the reason given when the model skipped a requirement or misnamed it
NO_SOURCE = "no evidence source could be read"
# The columns the roll-up counts: should requirements only give the should score.
ROLL_UP_COLUMNS: frozenset[Column] = frozenset({"must", "high", "very high", "overlay"})
# Vouch prepares for an audit; it never claims the audit's result (GLOSSARY.md, "Verdict").
# A negation ("non-compliant", "not certified") and handing something on ("passed to the
# CISO", "passed on to IT") are not claims; "passed on 3 March" is. English only: German answers come with the second profile (M4).
AUDIT_RESULT = re.compile(
    r"(?<!non-)(?<!non )(?<!not )\b(compliant|certified|passed(?!\s+(?:on\s+)?to\b))\b",
    re.IGNORECASE,
)
OUTPUT_RETRIES = 1  # an answer or a judgement claiming an audit result is asked for once more
USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")
# A remediation item the model didn't draft, or one for a requirement the lead overrode to
# not evidenced: the lead fills it in.
NOT_DRAFTED = "not drafted"  # its "missing"
NO_OWNER = "no owner suggested"  # its owner role
DEFAULT_DUE_DAYS = 90  # its due date, counted from today
MAX_DUE_DAYS = 365


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


class RemediationItemReply(BaseModel):
    """The model's draft of one remediation item."""

    requirement_id: str = Field(description="The not-evidenced requirement's id, such as M1.")
    missing: str = Field(description="What evidence or practice is missing, in one sentence.")
    owner_role: str = Field(description="The role that should own the fix, not a person's name.")
    due_in_days: int = Field(
        ge=1, le=MAX_DUE_DAYS, description="Days from today until the fix is due."
    )


class RemediationPlan(BaseModel):
    """One remediation item for each not-evidenced requirement listed."""

    items: list[RemediationItemReply] = Field(default_factory=list)


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


@dataclass(frozen=True)
class RemediationItem:
    """A drafted remediation item for a not-evidenced requirement, until the lead approves it."""

    requirement_id: str
    missing: str
    owner_role: str
    due_date: str  # ISO 8601

    @classmethod
    def not_drafted(cls, requirement_id: str, today: date) -> "RemediationItem":
        """An item for the lead to fill in."""
        due = (today + timedelta(days=DEFAULT_DUE_DAYS)).isoformat()
        return cls(requirement_id, NOT_DRAFTED, NO_OWNER, due)


class OverrideRequest(BaseModel):
    """The lead changes one requirement's verdict, with a reason."""

    requirement_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    verdict: JudgedVerdict
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ReviewDecision(BaseModel):
    """The compliance lead's review of one control: accept or reject, a note, any overrides."""

    decision: Decision
    note: str = ""
    overrides: list[OverrideRequest] = Field(default_factory=list)


@dataclass(frozen=True)
class Override:
    """A requirement's verdict changed by the lead; the agent's verdict and reason are kept."""

    requirement_id: str
    verdict: Verdict
    reason: str
    agent_verdict: Verdict
    agent_reason: str


@dataclass(frozen=True)
class Review:
    """The lead's recorded decision on the control."""

    decision: Decision
    note: str
    overrides: list[Override]

    @property
    def status(self) -> "Status":
        return REVIEWED[self.decision]


Status = Literal["waiting-for-review", "accepted", "rejected"]
WAITING: Status = "waiting-for-review"
REVIEWED: dict[Decision, Status] = {"accept": "accepted", "reject": "rejected"}
WAITING_TEXT = "Waiting for the compliance lead's review."


class UnknownAssessmentError(LookupError):
    """No assessment has this thread id."""


class NotWaitingForReviewError(RuntimeError):
    """The assessment exists but isn't paused at the review (it was reviewed already)."""


class InvalidReviewError(ValueError):
    """The review decision doesn't fit the assessment (an unknown requirement, say)."""


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
    remediation: list[RemediationItem] = field(default_factory=list)
    thread_id: str = ""  # resume this thread with the lead's review decision
    status: Status = WAITING
    review: Review | None = None

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
    profile_id: str
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
    today: str  # ISO 8601: due dates count from it
    note: str  # the lead's note from a rejected review, when this run was asked for again
    remediation: list[RemediationItem]
    remediate_rejected_word: str | None
    not_drafted: list[str]
    review: Review
    steps: Annotated[list[dict[str, str]], operator.add]  # each node's described step
    model: str  # the model that drafted it, and its prompt version
    prompt_version: str


def _format_requirements(requirements: Iterable[Requirement]) -> str:
    return "\n".join(f"- {r.id} ({r.column}): {r.text}" for r in requirements)


def _note_text(note: str) -> str:
    """The lead's rejection note, for a run asked for again; nothing on a first run."""
    if not note:
        return ""
    return (
        "\n\nThe compliance lead rejected the previous assessment of this control with this "
        f"note: {note}"
    )


def _prompt(
    control: IsaControl,
    control_requirements: ControlRequirements,
    profile: ComplianceProfile,
    note: str = "",
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
            f"{_note_text(note)}"
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
            f"{_note_text(state.get('note', ''))}"
        ),
    ]


def _not_evidenced(requirements: list[RequirementVerdict]) -> list[RequirementVerdict]:
    return [r for r in requirements if r.verdict == "not-evidenced"]


def _remediation_prompt(state: AssessState) -> list[AnyMessage]:
    control = state["control"]
    not_evidenced = "\n".join(
        f"- {r.id} ({r.column}): {r.text}\n  Why: {r.reason}"
        for r in _not_evidenced(state["requirements"])
    )
    return [
        SystemMessage(
            "Draft one remediation item for each requirement listed, which the company's "
            "evidence does not show: what is missing, the role that should own the fix (a "
            f"role, never a person's name), and how many days from today (1 to {MAX_DUE_DAYS}) "
            "until it is due. Say what is missing; never call the company compliant, "
            "certified or passed."
        ),
        HumanMessage(f"Control {control.id}: {control.question}\nNot evidenced:\n{not_evidenced}"),
    ]


def _drafts(
    not_evidenced: list[RequirementVerdict], plan: RemediationPlan | None, today: date
) -> tuple[list[RemediationItem], list[str]]:
    """One remediation item per not-evidenced requirement, in the requirements' order, and the
    ids the model didn't draft.

    A skipped requirement gets an item saying "not drafted" (RemediationItem.not_drafted).
    Items for other requirements are ignored; of two for one requirement, the first counts.
    """
    given: dict[str, RemediationItemReply] = {}
    for item in plan.items if plan else []:
        given.setdefault(item.requirement_id, item)
    drafts, not_drafted = [], []
    for req in not_evidenced:
        item = given.get(req.id)
        if item is None:
            not_drafted.append(req.id)
            drafts.append(RemediationItem.not_drafted(req.id, today))
            continue
        due = (today + timedelta(days=item.due_in_days)).isoformat()
        drafts.append(RemediationItem(req.id, item.missing, item.owner_role, due))
    return drafts, not_drafted


def _check_overrides(
    requirements: list[RequirementVerdict], overrides: list[OverrideRequest]
) -> None:
    """Raise InvalidReviewError for an override naming a requirement the assessment didn't
    judge (unknown, or not applicable under the profile), or naming one twice."""
    judged = {r.id for r in requirements if r.verdict != NOT_APPLICABLE}
    named = [o.requirement_id for o in overrides]
    for requirement_id in named:
        if requirement_id not in judged:
            raise InvalidReviewError(
                f"override names {requirement_id}, which this assessment didn't judge"
            )
    if len(set(named)) != len(named):
        raise InvalidReviewError("an override names the same requirement twice")


def _reviewed_remediation(
    requirements: list[RequirementVerdict], drafted: list[RemediationItem], today: date
) -> list[RemediationItem]:
    """The remediation items after overrides: one per requirement still not evidenced. A
    requirement overridden away from not evidenced loses its item; one overridden to it gets a
    "not drafted" item for the lead."""
    by_id = {item.requirement_id: item for item in drafted}
    return [
        by_id.get(r.id) or RemediationItem.not_drafted(r.id, today)
        for r in _not_evidenced(requirements)
    ]


def _apply_overrides(
    requirements: list[RequirementVerdict], overrides: list[OverrideRequest]
) -> tuple[list[RequirementVerdict], list[Override]]:
    """The requirements with the lead's verdicts and reasons in place, and the overrides kept
    with the agent's verdicts. InvalidReviewError for overrides `_check_overrides` refuses."""
    _check_overrides(requirements, overrides)
    by_id = {o.requirement_id: o for o in overrides}
    reviewed, kept = [], []
    for req in requirements:
        change = by_id.get(req.id)
        if change is None:
            reviewed.append(req)
            continue
        reviewed.append(
            RequirementVerdict(req.id, req.column, req.text, change.verdict, change.reason)
        )
        kept.append(Override(req.id, change.verdict, change.reason, req.verdict, req.reason))
    return reviewed, kept


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


def _ask_until_clean[T](
    model: Runnable,
    prompt: list[AnyMessage],
    read: Callable[[Any], tuple[T, list[str]]],
    *,
    what: str,
    refusal: Callable[[str], str],
) -> tuple[T, list[dict[str, int]], str | None]:
    """Ask a structured-output model; `read` turns its parsed reply into a result and the
    texts to check. A text claiming an audit result gets the reply asked for again once, with
    `refusal(word)` added to the prompt, then OutputCheckError naming `what`.

    Returns the result, the token usage, and the word that was refused (or None)."""
    usage: list[dict[str, int]] = []
    rejected = None
    for attempt in range(OUTPUT_RETRIES + 1):
        reply = model.invoke(prompt)
        usage += _usage_of(reply["raw"])
        result, texts = read(reply["parsed"])
        word = next((w for text in texts if (w := claims_audit_result(text))), None)
        if word is None:
            break
        if attempt == OUTPUT_RETRIES:
            raise OutputCheckError(f"{what} claimed an audit result (“{word}”) again")
        rejected = word
        prompt = [*prompt, HumanMessage(refusal(word))]
    return result, usage, rejected


def _no_model(_: Any) -> Any:
    raise RuntimeError("this graph was built without a model: it can only resume a review")


def build_graph(model: BaseChatModel | None, checkpointer: BaseCheckpointSaver | None = None):
    """Compile the graph around a model and a checkpointer passed in by the caller.

    The run pauses at the review interrupt, so a checkpointer keeps the thread to resume.
    Without a model the graph can only resume a review: no model is called after it."""
    if model is None:
        bound = judge_model = remediation_model = RunnableLambda(_no_model)
    else:
        bound = model.bind_tools([SEARCH_POLICIES_TOOL, SUBMIT_ANSWER_TOOL])
        judge_model = model.with_structured_output(Judgement, include_raw=True)
        remediation_model = model.with_structured_output(RemediationPlan, include_raw=True)

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

        def read(judgement: Judgement | None) -> tuple[Any, list[str]]:
            results, not_judged = _judged_requirements(control_requirements, judgement)
            return (results, not_judged), [r.reason for r in results if r.verdict != NOT_APPLICABLE]

        (results, not_judged), usage, rejected = _ask_until_clean(
            judge_model,
            _judge_prompt(state),
            read,
            what="a judgement's reason",
            refusal=lambda word: (
                f"Judgement refused: a reason says “{word}”, an audit result Vouch never "
                "claims. Judge again, saying only what the evidence shows."
            ),
        )
        return {
            "requirements": results,
            "not_judged": not_judged,
            "judge_rejected_word": rejected,
            "usage": usage,
        }

    def remediate(state: AssessState) -> dict[str, Any]:
        """The model drafts one remediation item per not-evidenced requirement; none of those,
        no call. A draft claiming an audit result gets the plan asked for again once, then an
        error. Before the interrupt, so it only reads and asks the model."""
        not_evidenced = _not_evidenced(state["requirements"])
        if not not_evidenced:
            return {"remediation": [], "not_drafted": [], "remediate_rejected_word": None}
        today = date.fromisoformat(state["today"])

        def read(plan: RemediationPlan | None) -> tuple[Any, list[str]]:
            drafts, not_drafted = _drafts(not_evidenced, plan, today)
            return (drafts, not_drafted), [f"{d.missing} {d.owner_role}" for d in drafts]

        (drafts, not_drafted), usage, rejected = _ask_until_clean(
            remediation_model,
            _remediation_prompt(state),
            read,
            what="a remediation draft",
            refusal=lambda word: (
                f"Remediation refused: a draft says “{word}”, an audit result Vouch never "
                "claims. Draft again, saying only what is missing."
            ),
        )
        return {
            "remediation": drafts,
            "not_drafted": not_drafted,
            "remediate_rejected_word": rejected,
            "usage": usage,
        }

    def review(state: AssessState) -> dict[str, Any]:
        """Pause for the compliance lead. This node re-runs from its start on resume, so
        nothing before the interrupt writes; the decision comes back as its value."""
        decision = ReviewDecision.model_validate(interrupt({"control_id": state["control"].id}))
        requirements, overrides = _apply_overrides(state["requirements"], decision.overrides)
        today = date.fromisoformat(state["today"])
        return {
            "requirements": requirements,
            "remediation": _reviewed_remediation(requirements, state["remediation"], today),
            "review": Review(decision.decision, decision.note, overrides),
        }

    def after_gather(state: AssessState) -> str:
        return "check_answer" if state.get("answer") is not None else "tools"

    def after_check(state: AssessState) -> str:
        return "cite" if state.get("answer") is not None else "gather"

    def described(node: str, run: Any) -> Any:
        """The node, recording its step in the state, so a resumed thread keeps every step."""

        def run_and_describe(state: AssessState) -> dict[str, Any]:
            update = run(state)
            return {**update, "steps": [Step(node, update).to_dict()]}

        return run_and_describe

    graph = StateGraph(AssessState)
    for node, run in {
        "gather": gather,
        "tools": tools,
        "check_answer": check_answer,
        "cite": cite,
        "judge": judge,
        "remediate": remediate,
        "review": review,
    }.items():
        graph.add_node(node, described(node, run))
    graph.add_edge(START, "gather")
    graph.add_conditional_edges("gather", after_gather, ["tools", "check_answer"])
    graph.add_edge("tools", "gather")
    graph.add_conditional_edges("check_answer", after_check, ["gather", "cite"])
    graph.add_edge("cite", "judge")
    graph.add_edge("judge", "remediate")
    graph.add_edge("remediate", "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=checkpointer)


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
        if self.node == "remediate":
            drafts = update["remediation"]
            if not drafts:
                return "No requirement is not evidenced, so no remediation item was drafted."
            text = f"Drafted {_plural(len(drafts), 'remediation item')}."
            word = update.get("remediate_rejected_word")
            if word:
                text = (
                    f"A draft claimed an audit result (“{word}”), so the drafts were asked for "
                    f"again. {text}"
                )
            if update.get("not_drafted"):
                text += " Not drafted: " + ", ".join(update["not_drafted"]) + "."
            return text
        if self.node == "review":
            review = update.get("review")
            if review is None:
                return WAITING_TEXT
            text = f"The compliance lead {review.status} the assessment"
            if review.overrides:
                ids = ", ".join(o.requirement_id for o in review.overrides)
                text += f", overriding {ids}"
            return text + "."
        return f"Finished the {self.node} step."

    def to_dict(self) -> dict[str, str]:
        """The step as the stream and the Assessment carry it."""
        return {"node": self.node, "text": self.describe()}


def _waiting_step() -> Step:
    """The step a paused run ends with: the review node, interrupted, made no update."""
    return Step("review", {})


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _model_name(model: BaseChatModel) -> str:
    return getattr(model, "model_name", None) or model._llm_type


def _total_usage(usage: list[dict[str, int]]) -> dict[str, int]:
    return {k: sum(u[k] for u in usage) for k in USAGE_KEYS}


# Vouch's own types in the graph's state, which a checkpointer may read back: a new one goes
# here, or the saver refuses to read it back (LangGraph's serializer allow-list).
STATE_TYPES: tuple[type, ...] = (
    IsaControl,
    Requirement,
    ControlRequirements,
    NotApplicableRequirement,
    RequirementVerdict,
    RemediationItem,
    Review,
    Override,
)


def memory_checkpointer() -> InMemorySaver:
    """LangGraph's in-memory saver, where a paused assessment waits for review (M2: Postgres).

    It reads back LangGraph's safe types and STATE_TYPES only, not any type with a warning."""
    serde = JsonPlusSerializer(allowed_msgpack_modules=None).with_msgpack_allowlist(STATE_TYPES)
    return InMemorySaver(serde=serde)


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _assessment(values: dict[str, Any], thread_id: str) -> Assessment:
    """The Assessment from the thread's state: proposed while it waits, reviewed after."""
    requirements = values["requirements"]
    review: Review | None = values.get("review")
    steps = list(values["steps"])
    if review is None:
        status: Status = WAITING
        steps.append(_waiting_step().to_dict())
    else:
        status = review.status
    return Assessment(
        control_id=values["control"].id,
        profile_id=values["profile_id"],
        verdict=roll_up(requirements),
        should_score=should_score(requirements),
        requirements=requirements,
        answer=values["answer"],
        sources=values["sources"],
        warnings=list(dict.fromkeys(values["warnings"])),
        steps=steps,
        token_usage=_total_usage(values["usage"]),
        model=values["model"],
        prompt_version=values["prompt_version"],
        remediation=values["remediation"],
        thread_id=thread_id,
        status=status,
        review=review,
    )


def assess_steps(
    control: IsaControl,
    profile: ComplianceProfile,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
    *,
    note: str = "",
    today: date | None = None,
) -> Iterator[Step | RequirementVerdict | Assessment]:
    """Run the graph for one loaded control, on a new thread, up to the review interrupt: a
    Step per node as it finishes, each RequirementVerdict once judged, a Step saying it waits
    for review, then the proposed Assessment, whose `thread_id` resumes it (see `resume`).

    Without a checkpointer the thread is kept in a fresh in-memory saver, so it can't be
    resumed. `note` is the lead's note from a rejected review, when the control is assessed
    again; `today` (default: the date now) is what due dates count from."""
    thread_id = uuid.uuid4().hex  # always a new thread: never piled onto an earlier run's state
    graph = build_graph(model, checkpointer or memory_checkpointer())
    control_requirements = profile.requirements_for(control)
    initial = {
        "control": control,
        "control_requirements": control_requirements,
        "profile_id": profile.id,
        "model": _model_name(model),
        "prompt_version": PROMPT_VERSION,
        "evidence": [],
        "warnings": [],
        "usage": [],
        "steps": [],
        "remediation": [],
        "today": (today or date.today()).isoformat(),
        "note": note,
        "messages": _prompt(control, control_requirements, profile, note),
    }
    config = _config(thread_id)
    for node, update in (
        (node, update)
        for chunk in graph.stream(initial, config, stream_mode="updates")
        for node, update in chunk.items()
    ):
        if node == "__interrupt__":
            yield _waiting_step()
            continue
        yield Step(node, update or {})
        if node == "judge":
            yield from update["requirements"]
    yield _assessment(graph.get_state(config).values, thread_id)


def assess(
    control_id: str,
    profile: ComplianceProfile,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
    *,
    note: str = "",
    today: date | None = None,
) -> Assessment:
    """Run the graph for one control under a compliance profile, on a new thread, up to the
    review interrupt and return the proposed Assessment; `resume` its `thread_id` with the
    lead's decision. Assessing a rejected control again is a new run, with the lead's `note`.

    An unknown control raises UnknownControlError before the model is called."""
    *_, assessment = assess_steps(
        load_isa_control(control_id), profile, model, checkpointer, note=note, today=today
    )
    if not isinstance(assessment, Assessment):
        raise TypeError(f"the graph ended without an Assessment for {control_id}")
    return assessment


# One lock per thread, so two reviews of one assessment can't both find it waiting and resume
# it: the second finds it reviewed. Locks are per process, as the in-memory saver is (M2).
_review_locks: dict[str, threading.Lock] = {}
_review_locks_guard = threading.Lock()


def _review_lock(thread_id: str) -> threading.Lock:
    with _review_locks_guard:
        return _review_locks.setdefault(thread_id, threading.Lock())


def resume(
    thread_id: str, decision: ReviewDecision, checkpointer: BaseCheckpointSaver
) -> Assessment:
    """Resume a thread waiting for review with the lead's decision and return the reviewed
    Assessment: overrides applied, the roll-up and remediation items recalculated, the decision
    recorded. No model is called.

    Raises UnknownAssessmentError for an unknown thread, NotWaitingForReviewError for one not
    waiting (reviewed already, perhaps at the same moment), and InvalidReviewError for an
    override `_check_overrides` refuses. Nothing is resumed when any is raised."""
    graph = build_graph(None, checkpointer)
    config = _config(thread_id)
    with _review_lock(thread_id):
        snapshot = graph.get_state(config)
        if not snapshot.values:
            raise UnknownAssessmentError(f"no assessment {thread_id}")
        if not snapshot.interrupts:
            raise NotWaitingForReviewError(f"assessment {thread_id} is not waiting for review")
        _check_overrides(snapshot.values["requirements"], decision.overrides)
        values = graph.invoke(Command(resume=decision.model_dump()), config)
    return _assessment(values, thread_id)
