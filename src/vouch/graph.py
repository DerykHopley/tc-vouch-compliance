"""The assessment graph: one VDA ISA control under one compliance profile in, one Assessment out.

The graph applies the profile's country overlays first, taking an overlay branch when one adds a
requirement to the control. The agent then gathers evidence (ReAct over the evidence-source tools the profile allows), drafts
an answer, and judges each applicable requirement, quoting the passages each verdict relies
on. Every evidence-source tool call passes the guards first (`vouch.guards`), and document
text reaches the model only as data. Each quote is checked in code against the document it
cites; one that fails is dropped, shown as a guard step. The control's verdict rolls up from
the verdicts in code, never from the model.
"""

import json
import logging
import operator
import re
import textwrap
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, TypedDict, get_args

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

from vouch.citations import Citation, CitationClaim, DropReason, QuoteChecker
from vouch.documents import (
    DOCUMENTED,
    DocumentFacts,
    EvidenceKind,
    document_path,
    facts_of,
    read_facts,
)
from vouch.guards import (
    DATA_RULE,
    allowed_folder,
    as_data,
    draft_as_data,
    flagged_steps,
    refusal_step,
)
from vouch.isa import Column, IsaControl, Requirement, load_isa_control
from vouch.patch import EVIDENCE_PENDING, TIMED_OUT, vulnerability_status
from vouch.profile import (
    ComplianceProfile,
    ControlRequirements,
    EvidenceSourceName,
    NotApplicableRequirement,
)
from vouch.search import (
    DEFAULT_LIMIT,
    FOLDER_ESCAPED,
    MAX_LIMIT,
    MAX_QUERY_CHARS,
    SOURCE_UNAVAILABLE,
    ErrorResult,
    Passage,
    search_folder,
)

SUBMIT_ANSWER = "submit_answer"
SEARCH_POLICIES = "search_policies"
SEARCH_RECORDS = "search_records"
VULNERABILITY_STATUS = "vulnerability_status"
GUARD = "guard"  # the node a guard step names: a refusal, a flagged passage, a dropped quote
PROMPT_VERSION = "m1-kind-stale-patch-5"  # change it whenever a prompt below changes

JudgedVerdict = Literal["evidenced", "partly-evidenced", "not-evidenced"]
Verdict = JudgedVerdict | Literal["not-applicable"]
JUDGED_VERDICTS: tuple[JudgedVerdict, ...] = get_args(JudgedVerdict)
NOT_APPLICABLE: Verdict = "not-applicable"
Decision = Literal["accept", "reject"]
NOT_JUDGED = "not judged"  # the reason given when the model skipped a requirement or misnamed it
# The reasons every requirement is not-evidenced, without asking the model, when nothing was read.
NO_SOURCE = "no evidence source could be read"
NO_SOURCES_ALLOWED = "no evidence sources allowed"
NO_ALLOWED_SEARCH = "no allowed source was searched"  # sources were allowed; only others asked
# A requirement left not evidenced while a source is pending: its reason, or the note added
# to it. `sources` is the pending tools' labels.
PENDING_REASON = "evidence pending: {sources} could not be read"
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
# A remediation item the model didn't draft, or one for a requirement the lead overrode to
# not evidenced: the lead fills it in.
NOT_DRAFTED = "not drafted"  # its "missing"
LOAD_CONTROL = "load_control"  # the first step's node: the control, loaded before the graph
QUESTION_SHOWN = 90  # the load step shortens a longer control question to this many characters
NO_OWNER = "no owner suggested"  # its owner role
DEFAULT_DUE_DAYS = 90  # its due date, counted from today
MAX_DUE_DAYS = 365

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


def _search(args: dict[str, Any], folder: Path) -> list[Passage] | ErrorResult:
    # Looked up when called, so a test can replace vouch.graph.search_folder.
    return search_folder(args.get("query"), args.get("limit", DEFAULT_LIMIT), folder=folder)


class EvidencePassage(Passage):
    """A passage as the judge sees it: where it was found, its document's kind, and whether
    that document is stale on the run's date."""

    source: str
    kind: EvidenceKind
    stale: bool


def _with_facts(
    passages: list[Passage], source: str, folder: Path, today: date
) -> list[EvidencePassage]:
    """Each passage with its document's source, kind and staleness, each document read once.
    One that can't be read again takes its source's kind and counts as stale."""

    def facts_for(doc_id: str) -> DocumentFacts:
        path = document_path(folder, doc_id)
        try:
            if path is not None:
                return read_facts(path, source, today)
        except (OSError, UnicodeDecodeError):
            pass
        return facts_of(None, source, doc_id, today)

    facts = {doc_id: facts_for(doc_id) for doc_id in dict.fromkeys(p["doc_id"] for p in passages)}
    return [
        EvidencePassage(
            **p,
            source=source,
            kind=facts[p["doc_id"]].kind,
            stale=facts[p["doc_id"]].stale_because is not None,
        )
        for p in passages
    ]


VULNERABILITY_STATUS_TOOL = {
    "type": "function",
    "function": {
        "name": VULNERABILITY_STATUS,
        "description": "Ask Patch for the company's vulnerability status: the tracked CVEs "
        "with their CVSS scores, the assets affected, priorities, treatments, overdue items "
        "and risk-acceptance decisions; returns passages with doc_id, heading and text.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _vulnerability_status(_args: dict[str, Any], folder: Path) -> list[Passage] | ErrorResult:
    return vulnerability_status(folder)


def _for_query(args: dict[str, Any]) -> str:
    """“ for “the query”” in a step, or nothing for a tool called without one."""
    query = args.get("query")
    return f" for “{query}”" if query else ""


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
    SEARCH_POLICIES: EvidenceTool("policies", "the policies", SEARCH_POLICIES_TOOL, _search),
    SEARCH_RECORDS: EvidenceTool("records", "the records", SEARCH_RECORDS_TOOL, _search),
    VULNERABILITY_STATUS: EvidenceTool(
        "patch", "Patch's vulnerability status", VULNERABILITY_STATUS_TOOL, _vulnerability_status
    ),
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
    if call["args"].get("query"):
        return f"Searching {tool.label}{_for_query(call['args'])}."
    return f"Asking for {tool.label}."


@dataclass(frozen=True)
class RequirementVerdict:
    """One requirement of the control and its verdict, with the reason."""

    id: str
    column: Column
    text: str
    verdict: Verdict
    reason: str
    citations: tuple[Citation, ...] = ()  # only quotes found in the cited document

    def __post_init__(self) -> None:
        # A checkpointer reads a tuple back as a list; keep it a tuple, so equality holds.
        object.__setattr__(self, "citations", tuple(self.citations))


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


def _merge_pending(old: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    merged = {**old, **new}
    return {label: code for label, code in merged.items() if code}


def pending_reason(labels: Iterable[str]) -> str:
    """The reason, or the note on a reason, for a requirement waiting on these sources."""
    return PENDING_REASON.format(sources=" and ".join(labels))


class AssessState(TypedDict, total=False):
    control: IsaControl
    control_requirements: ControlRequirements
    overlay_requirements: tuple[Requirement, ...]  # the applicable ones the overlays add
    profile_id: str
    evidence: Annotated[list[EvidencePassage], operator.add]
    warnings: Annotated[list[str], operator.add]
    guards: list[str]  # the last tools step's guard sentences: refusals and flagged passages
    searched: list[str]  # the last tools step's searches that ran, one sentence each
    # The tools pending in this run, by label: EVIDENCE_PENDING or TIMED_OUT. An update of ""
    # clears one (it answered later).
    pending: Annotated[dict[str, str], _merge_pending]
    pending_said: list[str]  # the last tools step's sentences about pending sources
    pending_ids: list[str]  # requirements left not evidenced while a source was pending
    unreadable: list[str]  # the last tools step's sources that couldn't be read, by label
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
    document_folders: dict[str, str]  # source name to folder: where cited documents are found
    about_doing: list[str]  # the requirements the judge said ask for something to be done
    dropped_citations: list[DroppedCitation]
    verdict_changes: list[str]  # "M1 to partly evidenced": verdicts the quote check changed
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
    control: IsaControl,
    control_requirements: ControlRequirements,
    profile: ComplianceProfile,
    note: str = "",
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
            f"{_note_text(note)}"
        ),
    ]


def _overlay_prompt(overlay_requirements: Iterable[Requirement]) -> HumanMessage:
    listed = "\n".join(f"- {r.id} ({r.source}): {r.text}" for r in overlay_requirements)
    return HumanMessage(
        "These requirements come from a country overlay, national rules added to this "
        f"control. Gather evidence for each of them too:\n{listed}"
    )


def _judge_prompt(state: AssessState) -> list[AnyMessage]:
    control = state["control"]
    pending = [
        f"{label} could not be read, so its evidence is pending" for label in state["pending"]
    ]
    evidence = as_data(state["evidence"], pending) if state["evidence"] or pending else "(none)"
    return [
        SystemMessage(
            "Judge each requirement listed from the evidence alone, not the draft answer. "
            "Give each one verdict: evidenced (the evidence fully shows it), partly-evidenced "
            "(it shows some of it) or not-evidenced (it shows none of it), and one sentence "
            "saying why. For each verdict other than not-evidenced, give the citations it "
            "relies on: the doc_id and a passage copied word for word from that document, at "
            "least four words from one paragraph, list item or table row. A quote that isn't "
            f"in the document is dropped, and a verdict with no citation left is not-evidenced. "
            "Each passage gives its source, its kind (implemented: shows what was actually "
            "done; documented: says what should be done) and whether it is stale (past its "
            "review date). For a requirement that asks for something to be done, set "
            "about_doing and say so in the reason: it is evidenced only with implemented "
            "evidence. Stale evidence shows a requirement partly at most. "
            f"{DATA_RULE}"
        ),
        HumanMessage(
            f"Control {control.id}: {control.question}\n"
            f"Requirements:\n{_format_requirements(state['control_requirements'].applicable)}\n\n"
            f"Evidence:\n{evidence}\n\n"
            f"The earlier model's draft answer:\n{draft_as_data(state.get('answer') or '')}"
            f"{_note_text(state.get('note', ''))}"
        ),
    ]


def _not_evidenced(requirements: list[RequirementVerdict]) -> list[RequirementVerdict]:
    return [r for r in requirements if r.verdict == "not-evidenced"]


def _to_remediate(
    requirements: list[RequirementVerdict], pending_ids: Iterable[str]
) -> list[RequirementVerdict]:
    """The not-evidenced requirements a remediation item is for: not those whose evidence is
    pending, which would show a gap that may not be there."""
    pending = set(pending_ids)
    return [r for r in _not_evidenced(requirements) if r.id not in pending]


def _remediation_prompt(
    control: IsaControl, requirements: list[RequirementVerdict]
) -> list[AnyMessage]:
    not_evidenced = "\n".join(
        f"- {r.id} ({r.column}): {r.text}\n  Why: {r.reason}" for r in requirements
    )
    return [
        SystemMessage(
            "Draft one remediation item for each requirement listed, which the company's "
            "evidence does not show: what is missing, the role that should own the fix (a "
            f"role, never a person's name), and how many days from today (1 to {MAX_DUE_DAYS}) "
            "until it is due. Say what is missing; never call the company compliant, "
            f"certified or passed. {DATA_RULE}"
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
    requirements: list[RequirementVerdict],
    drafted: list[RemediationItem],
    today: date,
    pending_ids: Iterable[str] = (),
) -> list[RemediationItem]:
    """The remediation items after overrides: one per requirement still not evidenced, except
    those still pending. A requirement overridden away from not evidenced loses its item; one
    overridden to it (pending or not) gets a "not drafted" item for the lead."""
    by_id = {item.requirement_id: item for item in drafted}
    return [
        by_id.get(r.id) or RemediationItem.not_drafted(r.id, today)
        for r in _to_remediate(requirements, pending_ids)
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
        reviewed.append(replace(req, verdict=change.verdict, reason=change.reason))
        kept.append(Override(req.id, change.verdict, change.reason, req.verdict, req.reason))
    return reviewed, kept


def _noted_pending(
    requirements: list[RequirementVerdict], pending: dict[str, str]
) -> tuple[list[RequirementVerdict], list[str]]:
    """With sources pending, each not-evidenced requirement's reason notes it (unless it is
    the pending reason already), and the ids of those requirements."""
    if not pending:
        return requirements, []
    note = pending_reason(pending)
    noted = [
        replace(r, reason=f"{r.reason} ({note})")
        if r.verdict == "not-evidenced" and r.reason != note
        else r
        for r in requirements
    ]
    return noted, [r.id for r in _not_evidenced(noted)]


class JudgedRequirements(NamedTuple):
    """What the judge step reads from one judgement."""

    results: list[RequirementVerdict]
    not_judged: list[str]  # ids the model didn't judge validly
    claims: dict[str, list[CitationClaim]]  # the citations given per id, unchecked
    about_doing: list[str]  # ids the model said ask for something to be done


def _judged_requirements(
    control_requirements: ControlRequirements,
    judgement: Judgement | None,
    unjudged_reason: str = NOT_JUDGED,
) -> JudgedRequirements:
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
    return JudgedRequirements(results, not_judged, claims, doing)


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


def build_graph(
    model: BaseChatModel | None,
    profile: ComplianceProfile | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Compile the graph around a model, the compliance profile whose sources it may use, and
    a checkpointer, all passed in by the caller.

    The run pauses at the review interrupt, so a checkpointer keeps the thread to resume.
    Without a model and a profile the graph can only resume a review: nothing after it calls
    the model or reads a source."""
    if model is None or profile is None:
        bound = judge_model = remediation_model = RunnableLambda(_no_model)
    else:
        bound = model.bind_tools([*_offered_tools(profile), SUBMIT_ANSWER_TOOL])
        judge_model = model.with_structured_output(Judgement, include_raw=True)
        remediation_model = model.with_structured_output(RemediationPlan, include_raw=True)

    def apply_overlays(state: AssessState) -> dict[str, Any]:
        """The control's requirements under the profile, overlay requirements included, and the
        prompt that starts gathering."""
        control = state["control"]
        control_requirements = profile.requirements_for(control)
        applicable = set(control_requirements.applicable)
        return {
            "control_requirements": control_requirements,
            "overlay_requirements": tuple(
                r for r in control_requirements.overlay if r in applicable
            ),
            "messages": _prompt(control, control_requirements, profile, state.get("note", "")),
        }

    def overlay_branch(state: AssessState) -> dict[str, Any]:
        """Asks the gathering to find evidence for each overlay requirement too."""
        overlay_requirements = state["overlay_requirements"]
        return {
            # Unchanged; given again only so this step's describe() can name them.
            "overlay_requirements": overlay_requirements,
            "messages": [_overlay_prompt(overlay_requirements)],
        }

    def after_overlays(state: AssessState) -> str:
        return "overlay_branch" if state["overlay_requirements"] else "gather"

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
        messages, new, warnings, guards, searched, unreadable = [], [], [], [], [], []
        said: list[str] = []  # sentences about pending sources
        pending, changed = dict(state["pending"]), {}  # by label, as the calls run
        today = date.fromisoformat(state["today"])
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
            elif pending.get(tool.label) == TIMED_OUT:  # asking again would only wait again
                detail = f"{tool.label} is already pending in this run"
                content = json.dumps(ErrorResult(error=EVIDENCE_PENDING, detail=detail))
                said.append(f"{detail}, so it wasn't asked again.")
            else:
                result = tool.run(args, folder)
                if isinstance(result, list):
                    result = _with_facts(result, tool.source, folder, today)
                content = as_data(result) if isinstance(result, list) else json.dumps(result)
                if isinstance(result, list):
                    run += 1
                    found = _plural(len(result), "passage")
                    searched.append(f"{tool.label}{_for_query(args)}: {found}")
                    if pending.pop(tool.label, None):  # it answered after all
                        changed[tool.label] = ""
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
                    unreadable.append(tool.label)
                elif result["error"] in (EVIDENCE_PENDING, TIMED_OUT):
                    pending[tool.label] = changed[tool.label] = result["error"]
                    why = result.get("detail", f"{tool.label} could not be read")
                    said.append(f"{why}, so the evidence is pending.")
            messages.append(ToolMessage(content, tool_call_id=call["id"]))
        return {
            "messages": messages,
            "evidence": new,
            "warnings": warnings,
            "guards": guards + flagged_steps(new),
            "searched": searched,
            "unreadable": list(dict.fromkeys(unreadable)),
            "pending": changed,
            "pending_said": said,
            "searches_run": run,
            "searches_refused": refused,
        }

    def no_evidence_reason(state: AssessState) -> str | None:
        """Why nothing could be read, so the model isn't asked to judge; None when it is.

        No source the profile allows can run: none allowed. A search that ran: judged on what
        it read, any unreadable source left as a warning and any pending one noted. Otherwise
        an allowed source that couldn't be read (missing, or its folder now points outside)
        comes first: no source read; then a source whose evidence is pending: every
        requirement pending on it; then searches only of sources not allowed: none of the
        allowed ones searched.
        """
        if not _offered_tools(profile):
            return NO_SOURCES_ALLOWED
        if state["searches_run"]:
            return None
        if {SOURCE_UNAVAILABLE, FOLDER_ESCAPED} & set(state["warnings"]):
            return NO_SOURCE
        if state["pending"]:
            return pending_reason(state["pending"])
        if state["searches_refused"]:
            return NO_ALLOWED_SEARCH
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
            results = _judged_requirements(control_requirements, None, reason).results
            return {"requirements": results, "not_judged": [], "claims": {}, "about_doing": []}

        def read(judgement: Judgement | None) -> tuple[JudgedRequirements, list[str]]:
            judged = _judged_requirements(control_requirements, judgement)
            return judged, [r.reason for r in judged.results if r.verdict != NOT_APPLICABLE]

        judged, usage, rejected = _ask_until_clean(
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
            "requirements": judged.results,
            "not_judged": judged.not_judged,
            "claims": judged.claims,
            "about_doing": judged.about_doing,
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
        today = date.fromisoformat(state["today"])
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
            results.append(_apply_rules(requirement, requirement.id in doing))
        results, pending_ids = _noted_pending(results, state["pending"])
        judged = {r.id: r.verdict for r in state["requirements"]}
        changes = [
            f"{r.id} to {r.verdict.replace('-', ' ')}" for r in results if r.verdict != judged[r.id]
        ]
        return {
            "requirements": results,
            "dropped_citations": dropped,
            "pending_ids": pending_ids,
            "verdict_changes": changes,
        }

    def remediate(state: AssessState) -> dict[str, Any]:
        """The model drafts one remediation item per not-evidenced requirement; none of those,
        no call. A draft claiming an audit result gets the plan asked for again once, then an
        error. Before the interrupt, so it only reads and asks the model."""
        not_evidenced = _to_remediate(state["requirements"], state["pending_ids"])
        if not not_evidenced:
            return {
                "remediation": [],
                "not_drafted": [],
                "remediate_rejected_word": None,
                "pending_ids": state["pending_ids"],
            }
        today = date.fromisoformat(state["today"])

        def read(plan: RemediationPlan | None) -> tuple[Any, list[str]]:
            drafts, not_drafted = _drafts(not_evidenced, plan, today)
            return (drafts, not_drafted), [f"{d.missing} {d.owner_role}" for d in drafts]

        (drafts, not_drafted), usage, rejected = _ask_until_clean(
            remediation_model,
            _remediation_prompt(state["control"], not_evidenced),
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
            "pending_ids": state["pending_ids"],
        }

    def review(state: AssessState) -> dict[str, Any]:
        """Pause for the compliance lead. This node re-runs from its start on resume, so
        nothing before the interrupt writes; the decision comes back as its value."""
        decision = ReviewDecision.model_validate(interrupt({"control_id": state["control"].id}))
        requirements, overrides = _apply_overrides(state["requirements"], decision.overrides)
        today = date.fromisoformat(state["today"])
        overridden = {o.requirement_id for o in overrides}
        still_pending = [i for i in state["pending_ids"] if i not in overridden]
        remediation = _reviewed_remediation(
            requirements, state["remediation"], today, still_pending
        )
        return {
            "requirements": requirements,
            "remediation": remediation,
            "review": Review(decision.decision, decision.note, overrides),
        }

    def after_gather(state: AssessState) -> str:
        return "check_answer" if state.get("answer") is not None else "tools"

    def after_check(state: AssessState) -> str:
        return "judge" if state.get("answer") is not None else "gather"

    def described(node: str, run: Any) -> Any:
        """The node, recording its step in the state, so a resumed thread keeps every step."""

        def run_and_describe(state: AssessState) -> dict[str, Any]:
            update = run(state)
            steps = [Step(node, update, profile), *_guard_steps(update)]
            return {**update, "steps": [step.to_dict() for step in steps]}

        return run_and_describe

    graph = StateGraph(AssessState)
    for node, run in {
        "apply_overlays": apply_overlays,
        "overlay_branch": overlay_branch,
        "gather": gather,
        "tools": tools,
        "check_answer": check_answer,
        "judge": judge,
        "check_quotes": check_quotes,
        "remediate": remediate,
        "review": review,
    }.items():
        graph.add_node(node, described(node, run))
    graph.add_edge(START, "apply_overlays")
    graph.add_conditional_edges("apply_overlays", after_overlays, ["overlay_branch", "gather"])
    graph.add_edge("overlay_branch", "gather")
    graph.add_conditional_edges("gather", after_gather, ["tools", "check_answer"])
    graph.add_edge("tools", "gather")
    graph.add_conditional_edges("check_answer", after_check, ["gather", "judge"])
    graph.add_edge("judge", "check_quotes")
    # Remediation follows the quote check, which can turn a verdict into not-evidenced.
    graph.add_edge("check_quotes", "remediate")
    graph.add_edge("remediate", "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=checkpointer)


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
        if self.node == LOAD_CONTROL:
            control: IsaControl = update["control"]
            requirements: ControlRequirements = update["control_requirements"]
            question = textwrap.shorten(control.question, QUESTION_SHOWN, placeholder="…")
            applicable, excluded = len(requirements.applicable), len(requirements.not_applicable)
            text = (
                f"Loaded control {control.id}: {question} "
                f"{_plural(applicable, 'requirement')} {'applies' if applicable == 1 else 'apply'}"
                f" under {update['company']}'s profile"
            )
            if excluded:
                text += f"; {excluded} {'is' if excluded == 1 else 'are'} not applicable"
            return text + "."
        if self.node == "apply_overlays":
            added = _by_overlay(update["overlay_requirements"])
            if not added:
                return (
                    "No country overlay adds a requirement to this control, "
                    "so no overlay branch is taken."
                )
            listed = "; ".join(f"{name} adds {', '.join(ids)}" for name, ids in added.items())
            return f"Applied the country overlays: {listed}."
        if self.node == "overlay_branch":
            ids = ", ".join(f"{r.id} ({r.source})" for r in update["overlay_requirements"])
            return f"Overlay branch: gathering evidence for {ids}."
        if self.node == "gather":
            calls = update["messages"][-1].tool_calls if update.get("messages") else []
            if any(c["name"] == SUBMIT_ANSWER for c in calls):
                return "Drafted an answer."
            if calls:
                return " ".join(_asking(call, self.profile) for call in calls)
        if self.node == "tools":
            unreadable = update.get("unreadable", [])
            text = (
                "Searched " + "; ".join(update["searched"]) + "." if update.get("searched") else ""
            )
            if unreadable:
                text = f"{text} Could not read {' or '.join(unreadable)}.".strip()
            text = " ".join([text, *update.get("pending_said", [])]).strip()
            return text or "No search ran."
        if self.node == "check_answer":
            word = update.get("rejected_word")
            if word:
                return f"The answer claimed an audit result (“{word}”), so it was asked for again."
            return "Checked the answer: it claims no audit result."
        if self.node == "check_quotes":
            kept = sum(len(r.citations) for r in update["requirements"])
            dropped = len(update["dropped_citations"])
            text = f"Checked each quote against its document: kept {_plural(kept, 'citation')}"
            text += f", dropped {dropped}." if dropped else "."
            changes = update.get("verdict_changes")
            if changes:  # the judge step showed the verdicts before these checks
                judged = [r for r in update["requirements"] if r.verdict != NOT_APPLICABLE]
                evidenced = sum(r.verdict == "evidenced" for r in judged)
                text += (
                    f" Changed by the checks: {', '.join(changes)}."
                    f" Now {evidenced} of {len(judged)} requirements evidenced."
                )
            return text
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
        if self.node == "remediate":
            drafts, pending = update["remediation"], update.get("pending_ids", [])
            waiting = f"the evidence for {', '.join(pending)} is pending"
            if not drafts and pending:
                return f"No remediation item drafted: {waiting}."
            if not drafts:
                return "No requirement is not evidenced, so no remediation item was drafted."
            made = len(drafts) - len(update.get("not_drafted", []))  # not the placeholders
            text = (
                f"Drafted {_plural(made, 'remediation item')}."
                if made
                else "No remediation item drafted by the model."
            )
            if pending:
                text += f" None for {', '.join(pending)}: {waiting}."
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


def _loaded_step(control: IsaControl, profile: ComplianceProfile) -> Step:
    """The first step: the control, loaded before the graph runs, and what applies of it."""
    update = {
        "control": control,
        "control_requirements": profile.requirements_for(control),
        "company": profile.company,
    }
    return Step(LOAD_CONTROL, update, profile)


def _waiting_step() -> Step:
    """The step a paused run ends with: the review node, interrupted, made no update."""
    return Step("review", {})


def _guard_steps(update: dict[str, Any]) -> list[Step]:
    """The guard steps a node's update made, after its own step: one per refused tool call or
    flagged passage, then one per citation the quote check dropped."""
    return [Step(GUARD, {"text": text}) for text in update.get("guards", [])] + [
        Step(GUARD, {"dropped_citation": d}) for d in update.get("dropped_citations", [])
    ]


def _by_overlay(requirements: Iterable[Requirement]) -> dict[str, list[str]]:
    """Each overlay's name and the ids of the requirements it adds, in order."""
    added: dict[str, list[str]] = {}
    for r in requirements:
        added.setdefault(r.source, []).append(r.id)
    return added


def _apply_rules(requirement: RequirementVerdict, about_doing: bool) -> RequirementVerdict:
    """The judge's rules on a requirement whose citations are checked (spec #2, ticket #22).

    Stale evidence caps it at partly-evidenced, and the reason names each stale document and
    why. A requirement about doing something (as the judge said) needs implemented evidence to
    be evidenced; with documented evidence only it is capped too, and the reason says so.
    """
    if requirement.verdict not in SHOWN_VERDICTS:
        return requirement
    stale = {c.doc_id: c.stale_because for c in requirement.citations if c.stale}
    why = [f"{doc} is stale ({because})" for doc, because in stale.items()]
    documented_only = all(c.kind == DOCUMENTED for c in requirement.citations)
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


# Vouch's own types in the graph's state, which a checkpointer may read back: a new one goes
# here, or the saver refuses to read it back (LangGraph's serializer allow-list).
STATE_TYPES: tuple[type, ...] = (
    IsaControl,
    Requirement,
    ControlRequirements,
    NotApplicableRequirement,
    RequirementVerdict,
    Citation,
    CitationClaim,
    DropReason,
    DroppedCitation,
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
        warnings=list(
            dict.fromkeys([*values["warnings"], *([EVIDENCE_PENDING] if values["pending"] else [])])
        ),
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
    Step saying the control was loaded, a Step per node as it finishes (and a guard Step per dropped citation), each
    RequirementVerdict once its quotes are checked, a Step saying it waits for review, then the
    proposed Assessment, whose `thread_id` resumes it (see `resume`).

    Without a checkpointer the thread is kept in a fresh in-memory saver, so it can't be
    resumed. `note` is the lead's note from a rejected review, when the control is assessed
    again; `today` (default: the date now) is what due dates count from and what staleness is
    judged on."""
    thread_id = uuid.uuid4().hex  # always a new thread: never piled onto an earlier run's state
    graph = build_graph(model, profile, checkpointer or memory_checkpointer())
    loaded = _loaded_step(control, profile)
    initial = {
        "control": control,
        "profile_id": profile.id,
        "model": _model_name(model),
        "prompt_version": PROMPT_VERSION,
        "evidence": [],
        "warnings": [],
        "pending": {},
        "pending_ids": [],
        "searches_run": 0,
        "searches_refused": 0,
        "usage": [],
        "steps": [loaded.to_dict()],  # kept in the thread, so the reviewed Assessment has it
        "remediation": [],
        "today": (today or date.today()).isoformat(),
        "note": note,
        "document_folders": {n: str(s.folder) for n, s in profile.evidence_sources.items()},
    }
    config = _config(thread_id)
    yield loaded
    for node, update in (
        (node, update)
        for chunk in graph.stream(initial, config, stream_mode="updates")
        for node, update in chunk.items()
    ):
        if node == "__interrupt__":
            yield _waiting_step()
            continue
        update = update or {}
        yield Step(node, update, profile)
        yield from _guard_steps(update)
        if node == "check_quotes":
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
    graph = build_graph(None, checkpointer=checkpointer)
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
