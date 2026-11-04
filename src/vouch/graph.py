"""The assessment graph: one VDA ISA control under one compliance profile in, one Assessment out.

The agent gathers evidence (ReAct over the evidence-source tools the profile allows), drafts
an answer, and judges each applicable requirement, quoting the passages each verdict relies
on. Every evidence-source tool call passes the guards first (`vouch.guards`), and document
text reaches the model only as data. Each quote is checked in code against the document it
cites; one that fails is dropped, shown as a guard step. The control's verdict rolls up from
the verdicts in code, never from the model.
"""

import datetime
import json
import logging
import operator
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from vouch.citations import Citation, CitationClaim, DropReason, QuoteChecker
from vouch.guards import (
    DATA_RULE,
    allowed_folder,
    as_data,
    draft_as_data,
    flagged_steps,
    refusal_step,
)
from vouch.isa import Column, IsaControl, Requirement, load_isa_control
from vouch.profile import ComplianceProfile, ControlRequirements, EvidenceSourceName
from vouch.search import (
    DEFAULT_LIMIT,
    FOLDER_ESCAPED,
    MAX_LIMIT,
    MAX_QUERY_CHARS,
    SOURCE_UNAVAILABLE,
    ErrorResult,
    Passage,
    search_policies,
    search_records,
)

SUBMIT_ANSWER = "submit_answer"
SEARCH_POLICIES = "search_policies"
SEARCH_RECORDS = "search_records"
GUARD = "guard"  # the node a guard step names: a refusal, a flagged passage, a dropped quote
PROMPT_VERSION = "m1-kind-stale-1"  # change it whenever a prompt below changes

Verdict = Literal["evidenced", "partly-evidenced", "not-evidenced", "not-applicable"]
JUDGED_VERDICTS: tuple[Verdict, ...] = ("evidenced", "partly-evidenced", "not-evidenced")
NOT_APPLICABLE: Verdict = "not-applicable"
NOT_JUDGED = "not judged"  # the reason given when the model skipped a requirement or misnamed it
# The reasons every requirement is not-evidenced, without asking the model, when nothing was read.
NO_SOURCE = "no evidence source could be read"
NO_SOURCES_ALLOWED = "no evidence sources allowed"
NO_ALLOWED_SEARCH = "no allowed source was searched"  # sources were allowed; only others asked
# The reasons an evidenced or partly evidenced verdict becomes not-evidenced after the quote
# check: nothing checked shows it (#21).
NO_CITATION = "No citation given, so nothing checked shows it."
CITATIONS_DROPPED = (
    "Every citation given for it was dropped by the quote check, so nothing checked shows it."
)
SHOWN_VERDICTS: tuple[Verdict, ...] = ("evidenced", "partly-evidenced")  # need a citation
# What the judge's rules add to a reason when they cap a verdict at partly-evidenced (#22).
CAPPED = "At most partly evidenced:"
DOCUMENTED_ONLY = "it asks for something to be done, and only documented evidence shows it"
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

log = logging.getLogger(__name__)


class OutputCheckError(RuntimeError):
    """The answer or a judgement's reason still claimed an audit result after one retry."""


def claims_audit_result(text: str) -> str | None:
    """The word with which the text claims an audit result (lower case), or None."""
    found = AUDIT_RESULT.search(text)
    return found.group(1).lower() if found else None


class SubmitAnswer(BaseModel):
    """Hand back the final answer for the control. The verdicts are decided by the judge step."""

    answer: str = Field(description="A short draft answer for an auditor.")


class RequirementJudgement(BaseModel):
    requirement_id: str = Field(description="The requirement's id, such as M1 or S2.")
    verdict: str = Field(description="evidenced, partly-evidenced or not-evidenced.")
    reason: str = Field(description="One sentence saying why, from the evidence.")
    about_doing: bool = Field(
        default=False,
        description="True when the requirement asks for something to be done (a check "
        "carried out, an assessment made), so it needs implemented evidence.",
    )
    citations: list[CitationClaim] = Field(
        default_factory=list, description="The passages the verdict relies on, quoted exactly."
    )


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

def _search_tool_spec(name: str, what: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Search the company's {what} by keyword; returns passages with "
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


SEARCH_POLICIES_TOOL = _search_tool_spec(SEARCH_POLICIES, "policies")
SEARCH_RECORDS_TOOL = _search_tool_spec(
    SEARCH_RECORDS, "records (what was actually done: logs, registers, reviews)"
)


def _search_policies(args: dict[str, Any], folder: Path) -> list[Passage] | ErrorResult:
    # Looked up when called, so a test can replace vouch.graph.search_policies.
    return search_policies(args.get("query"), args.get("limit", DEFAULT_LIMIT), folder=folder)


def _search_records(args: dict[str, Any], folder: Path) -> list[Passage] | ErrorResult:
    return search_records(args.get("query"), args.get("limit", DEFAULT_LIMIT), folder=folder)


@dataclass(frozen=True)
class EvidenceTool:
    """An evidence-source tool: the profile source it reads, what the compliance lead calls
    it, and once built, its spec for the model and how to run it in the folder the guards
    allow."""

    source: EvidenceSourceName
    label: str  # in step text, never the tool's name: "the policies"
    spec: dict[str, Any] | None = None  # None: not built yet, so never offered or run
    run: Callable[[dict[str, Any], Path], list[Passage] | ErrorResult] | None = None


# Every evidence-source tool, by the name the model calls. A new source tool goes here, so the
# allow-list covers it before it can run.
EVIDENCE_TOOLS: dict[str, EvidenceTool] = {
    SEARCH_POLICIES: EvidenceTool(
        "policies", "the policies", SEARCH_POLICIES_TOOL, _search_policies
    ),
    SEARCH_RECORDS: EvidenceTool("records", "the records", SEARCH_RECORDS_TOOL, _search_records),
    "vulnerability_status": EvidenceTool("patch", "Patch's vulnerability status"),  # #24
}


def _asking(call: dict[str, Any], profile: ComplianceProfile | None) -> str:
    """In plain words, what a tool call the model made is about to search, or why it won't."""
    tool = EVIDENCE_TOOLS.get(call["name"])
    if tool is None:
        return "Asked for a source that isn't available."
    if profile is None or tool.source not in profile.evidence_sources:
        return f"Asked for {tool.label}, which this profile doesn't allow."
    if tool.run is None:
        return f"Asked for {tool.label}, which Vouch can't search yet."
    return f"Searching {tool.label} for “{call['args'].get('query', '')}”."


@dataclass(frozen=True)
class RequirementVerdict:
    """One requirement of the control and its verdict, with the reason."""

    id: str
    column: Column
    text: str
    verdict: Verdict
    reason: str
    citations: tuple[Citation, ...] = ()  # only quotes found in the cited document


@dataclass(frozen=True)
class DroppedCitation:
    """A citation the quote check dropped, and why."""

    requirement_id: str
    doc_id: str
    reason: DropReason


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
    guards: list[str]  # the last tools step's guard sentences: refusals and flagged passages
    searched: list[str]  # the last tools step's searches that ran, one sentence each
    searches_run: Annotated[int, operator.add]
    searches_refused: Annotated[int, operator.add]
    usage: Annotated[list[dict[str, int]], operator.add]
    answer: str | None
    answer_retries: int
    rejected_word: str | None  # the audit-result word that made check_answer refuse the answer
    judge_rejected_word: str | None  # the one that made the judge ask for a new judgement
    requirements: list[RequirementVerdict]
    not_judged: list[str]
    claims: dict[str, list[CitationClaim]]  # the judge's citations per requirement, unchecked
    document_folders: list[str]  # where cited documents are looked up: the profile's sources
    today: str  # the ISO date staleness is judged on (#22)
    about_doing: list[str]  # the requirements the judge said ask for something to be done
    dropped_citations: list[DroppedCitation]
    messages: Annotated[list[AnyMessage], add_messages]


def _format_requirements(requirements: Iterable[Requirement]) -> str:
    return "\n".join(f"- {r.id} ({r.column}): {r.text}" for r in requirements)


def _offered_tools(profile: ComplianceProfile) -> list[dict[str, Any]]:
    """The specs of the evidence-source tools built so far that the profile allows (the
    guards still check every call, whatever the model asks for)."""
    return [
        tool.spec
        for tool in EVIDENCE_TOOLS.values()
        if tool.spec is not None and tool.source in profile.evidence_sources
    ]


def _passage_key(passage: Passage) -> tuple[str, str, str]:
    return passage["doc_id"], passage["heading"], passage["text"]


def _prompt(
    control: IsaControl, control_requirements: ControlRequirements, profile: ComplianceProfile
) -> list[AnyMessage]:
    offered = [t["function"]["name"] for t in _offered_tools(profile)]
    search = (
        f"Use {' and '.join(offered)} to find evidence."
        if offered
        else "No evidence source is allowed, so there is no evidence to find."
    )
    return [
        SystemMessage(
            f"You assess one {control.source} control for {profile.company} against the "
            f"company's own evidence. {search} When you are "
            f"done, call {SUBMIT_ANSWER} with a short draft answer for an auditor, written in "
            f"the language with ISO 639-1 code '{profile.answer_language}'. "
            "Say what the evidence shows; never call the company compliant, "
            f"certified or passed. {DATA_RULE}"
        ),
        HumanMessage(
            f"Control {control.id}: {control.question}\n"
            f"Requirements:\n{_format_requirements(control_requirements.applicable)}"
        ),
    ]


def _judge_prompt(state: AssessState) -> list[AnyMessage]:
    control = state["control"]
    evidence = as_data(state["evidence"]) if state["evidence"] else "(none)"
    return [
        SystemMessage(
            "Judge each requirement listed from the evidence alone, not the draft answer. "
            "Give each one verdict: evidenced (the evidence fully shows it), partly-evidenced "
            "(it shows some of it) or not-evidenced (it shows none of it), and one sentence "
            "saying why. For each verdict other than not-evidenced, give the citations it "
            "relies on: the doc_id and a passage copied word for word from that document, at "
            "least four words from one paragraph, list item or table row. A quote that isn't "
            f"in the document is dropped, and a verdict with no citation left is not-evidenced. "
            "Records show what was actually done (implemented evidence); policies say what "
            "should be done (documented evidence). For a requirement that asks for something to "
            "be done, set about_doing and say so in the reason: it is evidenced only with "
            "implemented evidence. Evidence past its review date is stale and shows a "
            f"requirement partly at most. {DATA_RULE}"
        ),
        HumanMessage(
            f"Control {control.id}: {control.question}\n"
            f"Requirements:\n{_format_requirements(state['control_requirements'].applicable)}\n\n"
            f"Evidence:\n{evidence}\n\n"
            f"The earlier model's draft answer:\n{draft_as_data(state.get('answer') or '')}"
        ),
    ]


def _judged_requirements(
    control_requirements: ControlRequirements,
    judgement: Judgement | None,
    unjudged_reason: str = NOT_JUDGED,
) -> tuple[list[RequirementVerdict], list[str], dict[str, list[CitationClaim]], list[str]]:
    """Each of the control's requirements with its verdict, the ids the model didn't judge
    validly, the citations given for each one it did (unchecked), and the ids it said ask for
    something to be done.

    A requirement the model skipped, or gave a verdict outside JUDGED_VERDICTS, is
    not-evidenced with `unjudged_reason`. Judgements for ids the control doesn't apply are
    ignored; for an id judged twice, the first counts. Not applicable requirements follow,
    with the profile's reason.
    """
    given: dict[str, RequirementJudgement] = {}
    for item in judgement.requirements if judgement else []:
        given.setdefault(item.requirement_id, item)
    results, not_judged, claims, doing = [], [], {}, []
    for req in control_requirements.applicable:
        item = given.get(req.id)
        if item is not None and item.verdict in JUDGED_VERDICTS:
            verdict, reason = item.verdict, item.reason
            claims[req.id] = item.citations
            if item.about_doing:
                doing.append(req.id)
        else:
            verdict, reason = "not-evidenced", unjudged_reason
            not_judged.append(req.id)
        results.append(RequirementVerdict(req.id, req.column, req.text, verdict, reason))
    for excluded in control_requirements.not_applicable:
        req = excluded.requirement
        results.append(
            RequirementVerdict(req.id, req.column, req.text, NOT_APPLICABLE, excluded.reason)
        )
    return results, not_judged, claims, doing


def _usage_of(message: Any) -> list[dict[str, int]]:
    """The message's token usage as a one-item list to add to the state, or none."""
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return []
    return [{k: usage[k] for k in USAGE_KEYS}]


def build_graph(model: BaseChatModel, profile: ComplianceProfile):
    """Compile the graph around a model and the compliance profile whose sources it may use."""
    bound = model.bind_tools([*_offered_tools(profile), SUBMIT_ANSWER_TOOL])
    judge_model = model.with_structured_output(Judgement, include_raw=True)

    def gather(state: AssessState) -> dict[str, Any]:
        reply = bound.invoke(state["messages"])
        update: dict[str, Any] = {"messages": [reply], "usage": _usage_of(reply)}
        for call in reply.tool_calls:
            if call["name"] == SUBMIT_ANSWER:
                answer = SubmitAnswer.model_validate(call["args"])
                update["answer"] = answer.answer
        return update

    def tools(state: AssessState) -> dict[str, Any]:
        """Run each tool call the guards let through; a refusal is the call's reply and a
        `guard` step. Passages reach the model as data, instruction-like ones flagged; a
        passage already gathered isn't added or flagged again."""
        messages, new, warnings, guards, searched = [], [], [], [], []
        known = {_passage_key(p) for p in state["evidence"]}
        run = refused = 0
        for call in state["messages"][-1].tool_calls:
            name, args = call["name"], call["args"]
            tool = EVIDENCE_TOOLS.get(name)
            folder = allowed_folder(profile, tool.source) if tool else None
            if tool is not None and not isinstance(folder, Path):  # refused before it runs
                guards.append(refusal_step(name, folder.get("detail", folder["error"])))
                refused += 1
                content = json.dumps(folder)
            elif tool is None or tool.run is None:  # not a tool, or a source not built yet
                content = f"tool {name} is not available"
            else:
                result = tool.run(args, folder)
                content = as_data(result) if isinstance(result, list) else json.dumps(result)
                if isinstance(result, list):
                    run += 1
                    found = _plural(len(result), "passage")
                    searched.append(f"the {tool.source} for “{args.get('query', '')}”: {found}")
                    for passage in result:
                        if _passage_key(passage) not in known:
                            known.add(_passage_key(passage))
                            new.append(passage)
                elif result["error"] == FOLDER_ESCAPED:  # the folder changed since it loaded
                    detail = f"the {tool.source} folder now points outside its allowed folder"
                    guards.append(refusal_step(name, detail))
                    warnings.append(FOLDER_ESCAPED)
                    refused += 1
                elif result["error"] == SOURCE_UNAVAILABLE:
                    warnings.append(SOURCE_UNAVAILABLE)
            messages.append(ToolMessage(content, tool_call_id=call["id"]))
        return {
            "messages": messages,
            "evidence": new,
            "warnings": warnings,
            "guards": guards + flagged_steps(new),
            "searched": searched,
            "searches_run": run,
            "searches_refused": refused,
        }

    def no_evidence_reason(state: AssessState) -> str | None:
        """Why nothing could be read, so the model isn't asked to judge; None when something
        could be. No source the profile allows can run: none allowed. Allowed sources, but every
        search asked for was refused: none of them was searched, unless an allowed folder
        couldn't be read (it now points outside), which is no source read."""
        if not _offered_tools(profile):
            return NO_SOURCES_ALLOWED
        if state["searches_refused"] and not state["searches_run"]:
            return NO_SOURCE if FOLDER_ESCAPED in state["warnings"] else NO_ALLOWED_SEARCH
        if SOURCE_UNAVAILABLE in state["warnings"]:
            return NO_SOURCE
        return None

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

    def judge(state: AssessState) -> dict[str, Any]:
        """The model judges each requirement; the code keeps only valid verdicts for them.

        A reason claiming an audit result gets the judgement asked for again once, then an error.
        """
        control_requirements = state["control_requirements"]
        reason = no_evidence_reason(state)
        if reason is not None:  # nothing could be read, so nothing is shown
            results, *_ = _judged_requirements(control_requirements, None, reason)
            return {"requirements": results, "not_judged": [], "claims": {}, "about_doing": []}
        prompt, usage, rejected = _judge_prompt(state), [], None
        for attempt in range(OUTPUT_RETRIES + 1):
            reply = judge_model.invoke(prompt)
            usage += _usage_of(reply["raw"])
            results, not_judged, claims, doing = _judged_requirements(
                control_requirements, reply["parsed"]
            )
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
            "claims": claims,
            "about_doing": doing,
            "judge_rejected_word": rejected,
            "usage": usage,
        }

    def check_quotes(state: AssessState) -> dict[str, Any]:
        """Keep each citation whose quote is in the document it cites; drop and log the rest.

        A requirement judged evidenced or partly evidenced with no citation left (none given,
        or every one dropped) becomes not-evidenced: nothing checked shows it. Then the
        judge's rules (`_apply_rules`) cap what the checked citations can show. Each document
        is read once.
        """
        today = datetime.date.fromisoformat(state["today"])
        claims = state.get("claims", {})
        checker = QuoteChecker(state["document_folders"], today=today)
        doing = set(state.get("about_doing", []))
        results, dropped = [], []
        for requirement in state["requirements"]:
            kept: list[Citation] = []
            given = claims.get(requirement.id, [])
            for claim in given:
                checked = checker.check(claim)
                if isinstance(checked, Citation):
                    if checked not in kept:
                        kept.append(checked)
                    continue
                log.warning(
                    "dropped a citation for %s from %s (%s): %r",
                    requirement.id,
                    claim.doc_id,
                    checked,
                    claim.quote,
                )
                dropped.append(DroppedCitation(requirement.id, claim.doc_id, checked))
            if not kept and requirement.verdict in SHOWN_VERDICTS:
                reason = CITATIONS_DROPPED if given else NO_CITATION
                requirement = replace(requirement, verdict="not-evidenced", reason=reason)
            requirement = replace(requirement, citations=tuple(kept))
            results.append(_apply_rules(requirement, requirement.id in doing, checker))
        return {"requirements": results, "dropped_citations": dropped}

    def after_gather(state: AssessState) -> str:
        return "check_answer" if state.get("answer") is not None else "tools"

    def after_check(state: AssessState) -> str:
        return "judge" if state.get("answer") is not None else "gather"

    graph = StateGraph(AssessState)
    graph.add_node("gather", gather)
    graph.add_node("tools", tools)
    graph.add_node("check_answer", check_answer)
    graph.add_node("judge", judge)
    graph.add_node("check_quotes", check_quotes)
    graph.add_edge(START, "gather")
    graph.add_conditional_edges("gather", after_gather, ["tools", "check_answer"])
    graph.add_edge("tools", "gather")
    graph.add_conditional_edges("check_answer", after_check, ["gather", "judge"])
    graph.add_edge("judge", "check_quotes")
    graph.add_edge("check_quotes", END)
    return graph.compile()


@dataclass(frozen=True)
class Step:
    """One node of the graph finished: its name, the state update it made, and the profile
    it ran under (which says what a tool call will search)."""

    node: str
    update: dict[str, Any]
    profile: ComplianceProfile | None = None

    def describe(self) -> str:
        """One plain sentence, in the glossary's words, saying what the step did."""
        update = self.update
        if self.node == "gather":
            calls = update["messages"][-1].tool_calls if update.get("messages") else []
            if any(c["name"] == SUBMIT_ANSWER for c in calls):
                return "Drafted an answer."
            if calls:
                return " ".join(_asking(call, self.profile) for call in calls)
        if self.node == "tools":
            if update.get("searched"):  # only searches that ran
                return "Searched " + "; ".join(update["searched"]) + "."
            if SOURCE_UNAVAILABLE in update.get("warnings", []):
                return "The policy folder could not be read."
            return "No search ran."
        if self.node == "check_answer":
            word = update.get("rejected_word")
            if word:
                return f"The answer claimed an audit result (“{word}”), so it was asked for again."
            return "Checked the answer: it claims no audit result."
        if self.node == "check_quotes":
            kept = sum(len(r.citations) for r in update["requirements"])
            dropped = len(update["dropped_citations"])
            text = f"Checked each quote against its document: kept {_plural(kept, 'citation')}"
            return text + (f", dropped {dropped}." if dropped else ".")
        if self.node == GUARD:
            if "text" in update:  # a refused tool call or a flagged passage
                return update["text"]
            d: DroppedCitation = update["dropped_citation"]
            return f"Dropped a citation for {d.requirement_id} from {d.doc_id}: {d.reason}."
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


def _apply_rules(
    requirement: RequirementVerdict, about_doing: bool, checker: QuoteChecker
) -> RequirementVerdict:
    """The judge's rules on a requirement whose citations are checked (spec #2, ticket #22).

    Stale evidence caps it at partly-evidenced, and the reason names each stale document and
    why. A requirement about doing something (as the judge said) needs implemented evidence to
    be evidenced; with documented evidence only it is capped too, and the reason says so.
    """
    if requirement.verdict not in SHOWN_VERDICTS:
        return requirement
    stale = dict.fromkeys(c.doc_id for c in requirement.citations if c.stale)
    why = [f"{doc} is stale ({checker.stale_reason(doc)})" for doc in stale]
    documented_only = all(c.kind == "documented" for c in requirement.citations)
    if about_doing and documented_only and requirement.verdict == "evidenced":
        why.append(DOCUMENTED_ONLY)
    if not why:
        return requirement
    reason = f"{requirement.reason} {CAPPED} {'; '.join(why)}."
    return replace(requirement, verdict="partly-evidenced", reason=reason)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _model_name(model: BaseChatModel) -> str:
    return getattr(model, "model_name", None) or model._llm_type


def _total_usage(usage: list[dict[str, int]]) -> dict[str, int]:
    return {k: sum(u[k] for u in usage) for k in USAGE_KEYS}


def assess_steps(
    control: IsaControl,
    profile: ComplianceProfile,
    model: BaseChatModel,
    today: datetime.date | None = None,
) -> Iterator[Step | RequirementVerdict | Assessment]:
    """Run the graph for one loaded control: a Step per node as it finishes (and a guard Step
    per dropped citation), each RequirementVerdict once its quotes are checked, then the
    Assessment. Staleness is judged on `today` (the real date when not given)."""
    control_requirements = profile.requirements_for(control)
    initial = {
        "control": control,
        "control_requirements": control_requirements,
        "evidence": [],
        "warnings": [],
        "searches_run": 0,
        "searches_refused": 0,
        "usage": [],
        "document_folders": [str(s.folder) for s in profile.evidence_sources.values()],
        "today": (today or datetime.date.today()).isoformat(),
        "messages": _prompt(control, control_requirements, profile),
    }
    final: dict[str, Any] = {}
    steps: list[dict[str, str]] = []
    graph = build_graph(model, profile)
    for mode, chunk in graph.stream(initial, stream_mode=["updates", "values"]):
        if mode == "values":
            final = chunk
            continue
        for node, update in chunk.items():
            step = Step(node, update or {}, profile)
            steps.append({"node": node, "text": step.describe()})
            yield step
            for text in (update or {}).get("guards", []):  # one per refusal or flagged passage
                steps.append({"node": GUARD, "text": text})
                yield Step(GUARD, {"text": text}, profile)
            if node == "check_quotes":
                for dropped in update["dropped_citations"]:
                    guard = Step(GUARD, {"dropped_citation": dropped}, profile)
                    steps.append({"node": GUARD, "text": guard.describe()})
                    yield guard
                yield from update["requirements"]
    requirements = final["requirements"]
    yield Assessment(
        control_id=control.id,
        profile_id=profile.id,
        verdict=roll_up(requirements),
        should_score=should_score(requirements),
        requirements=requirements,
        answer=final["answer"],
        warnings=list(dict.fromkeys(final["warnings"])),
        steps=steps,
        token_usage=_total_usage(final["usage"]),
        model=_model_name(model),
    )


def assess(
    control_id: str,
    profile: ComplianceProfile,
    model: BaseChatModel,
    today: datetime.date | None = None,
) -> Assessment:
    """Run the graph for one control under a compliance profile and return its Assessment.
    Staleness is judged on `today` (the real date when not given).

    An unknown control raises UnknownControlError before the model is called."""
    *_, assessment = assess_steps(load_isa_control(control_id), profile, model, today)
    if not isinstance(assessment, Assessment):
        raise TypeError(f"the graph ended without an Assessment for {control_id}")
    return assessment
