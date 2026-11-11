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
import textwrap
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, TypedDict, get_args

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, StateSnapshot, interrupt
from pydantic import BaseModel, Field, StringConstraints, model_validator

from vouch.added_documents import AddedDocument, added_index
from vouch.citations import (
    LEAD_STATEMENT,
    Citation,
    CitationClaim,
    DropReason,
    LeadStatement,
    QuoteChecker,
)
from vouch.documents import (
    DOCUMENTED,
    DocumentChoice,
    DocumentFacts,
    EvidenceKind,
    choice_in,
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
from vouch.isa import Column, IsaControl, Requirement
from vouch.language import (
    CAP_WORDS,
    audit_claim,
    audit_words_in,
    cap_note,
    kept_in_other_language,
    language_name,
    language_refusal,
    lead_statement_label,
    mismatch,
    written_in_not,
)
from vouch.patch import EVIDENCE_PENDING, TIMED_OUT, vulnerability_status
from vouch.profile import (
    ComplianceProfile,
    ControlRequirements,
    EvidenceSourceName,
    NotApplicableRequirement,
    ProfileError,
    UnknownProfileError,
    load_named_profile,
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
from vouch.tracing import tool_span, with_run_config

SUBMIT_ANSWER = "submit_answer"
SEARCH_POLICIES = "search_policies"
SEARCH_RECORDS = "search_records"
VULNERABILITY_STATUS = "vulnerability_status"
GUARD = "guard"  # the node a guard step names: a refusal, a flagged passage, a dropped quote
PROMPT_VERSION = "m7-lead-statements-1"  # the lead's statements as evidence (#422)
"""The version of the prompts below: change it whenever a prompt changes, so a run can say
which prompts drafted it (the developer page, #34; the audit trail, M2)."""

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
CAPPED = CAP_WORDS["en"].capped  # the cap's English words, kept in vouch.language (#354)
DOCUMENTED_ONLY = CAP_WORDS["en"].documented_only
# The columns the roll-up counts: should requirements only give the should score.
ROLL_UP_COLUMNS: frozenset[Column] = frozenset({"must", "high", "very high", "overlay"})
OUTPUT_RETRIES = 1  # an answer or a judgement claiming an audit result is asked for once more
USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")
QUESTION_SHOWN = 90  # the load step shortens a longer control question to this many characters
# A remediation item the model didn't draft, or one for a requirement the lead overrode to
# not evidenced: the lead fills it in.
NOT_DRAFTED = "not drafted"  # its "missing"
NO_OWNER = "no owner suggested"  # its owner role
DEFAULT_DUE_DAYS = 90  # its due date, counted from today
MAX_DUE_DAYS = 365

LEAD_SOURCE = "lead"
"""The source a lead statement's passage gives (#422): the lead's answer, not a document."""
LEAD_RULE = (
    f"Passages from {LEAD_SOURCE} are the compliance lead's own statements, answers to open "
    "questions on this control, each headed by the question it answers: cite one only where "
    "no document shows the requirement, quoting the answer; a statement shows a requirement "
    "partly at most."
)
"""What the prompts say of the lead's statements, only when there are some (#422)."""


def lead_rule(statements: object) -> str:
    """LEAD_RULE to follow a system prompt's rules when there are lead statements; nothing when
    there are none, so such a prompt is as it was before #422."""
    return f" {LEAD_RULE}" if statements else ""


log = logging.getLogger(__name__)


class OutputCheckError(RuntimeError):
    """The answer or a judgement's reason still claimed an audit result after one retry."""


def claims_audit_result(text: str, passages: Iterable[str] = ()) -> str | None:
    """The word with which the text claims an audit result (lower case), or None: in any
    answer language, not negated, outside quotes from the gathered `passages` (vouch.language)."""
    return audit_claim(text, passages)


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

    requirement_id: str = Field(
        description="The id of the requirement not or partly evidenced, such as M1."
    )
    missing: str = Field(description="What evidence or practice is still missing, in one sentence.")
    owner_role: str = Field(description="The role that should own the fix, not a person's name.")
    due_in_days: int = Field(
        ge=1, le=MAX_DUE_DAYS, description="Days from today until the fix is due."
    )


class RemediationPlan(BaseModel):
    """One remediation item for each requirement listed, not or partly evidenced (#212)."""

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


def _search(
    args: dict[str, Any],
    folder: Path,
    documents: DocumentChoice,
    added: Sequence[AddedDocument] = (),
) -> list[Passage] | ErrorResult:
    # Looked up when called, so a test can replace vouch.graph.search_folder.
    return search_folder(
        args.get("query"),
        args.get("limit", DEFAULT_LIMIT),
        folder=folder,
        documents=documents,
        added=added,
    )


class EvidencePassage(Passage):
    """A passage as the judge sees it: where it was found, its document's kind, and whether
    that document is stale on the run's date."""

    source: str
    kind: EvidenceKind
    stale: bool


def statement_passages(statements: Iterable[LeadStatement], language: str) -> list[EvidencePassage]:
    """Each lead statement as one evidence passage (#422): its "Q<id>", headed by the label in
    the answer `language` and the question it answers, the answer as its text (only that is
    quoted), from LEAD_SOURCE, documented, never stale."""
    label = lead_statement_label(language)
    return [
        EvidencePassage(
            doc_id=s.doc_id,
            heading=f"{label}: {s.question}",
            text=s.answer,
            source=LEAD_SOURCE,
            kind=DOCUMENTED,
            stale=False,
        )
        for s in statements
    ]


def statements_text(passages: list[EvidencePassage]) -> str:
    """The lead's statements as data for a prompt, after what it lists; nothing without any."""
    if not passages:
        return ""
    return (
        "\n\nThe compliance lead's statements on this control, answers to open questions:\n"
        f"{as_data(passages)}"
    )


def versions_read(
    passages: Iterable[Passage], added: Sequence[AddedDocument]
) -> list[dict[str, Any]]:
    """The version of each added document the passages came from, once each (#584)."""
    found = {p["doc_id"] for p in passages}
    return [{"doc_id": d.doc_id, "version": d.version} for d in added if d.doc_id in found]


def with_facts(
    passages: list[Passage],
    source: str,
    folder: Path,
    today: date,
    added: Sequence[AddedDocument] = (),
) -> list[EvidencePassage]:
    """Each passage with its document's source, kind and staleness, each document read once: an
    added one's (#584) from what was added. One that can't be read again takes its source's kind
    and counts as stale."""
    added_by_id = {d.doc_id: d for d in added}

    def facts_for(doc_id: str) -> DocumentFacts:
        if doc_id in added_by_id:
            return added_by_id[doc_id].facts(today)
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


def _vulnerability_status(
    _args: dict[str, Any],
    folder: Path,
    documents: DocumentChoice,
    _added: Sequence[AddedDocument] = (),
) -> list[Passage] | ErrorResult:
    return vulnerability_status(folder, documents)


def _for_query(args: dict[str, Any]) -> str:
    """“ for “the query”” in a step, or nothing for a tool called without one."""
    query = args.get("query")
    return f" for “{query}”" if query else ""


@dataclass(frozen=True)
class EvidenceTool:
    """An evidence-source tool: the profile source it reads, what the compliance lead calls
    it, and once built, its spec for the model and how to run it in the folder the guards
    allow, on the documents of it the profile chose (None: every one)."""

    source: EvidenceSourceName
    label: str  # in step text, never the tool's name: "the policies"
    spec: dict[str, Any] | None = None  # None: not built yet, so never offered or run
    run: (
        Callable[
            [dict[str, Any], Path, DocumentChoice, Sequence[AddedDocument]],
            list[Passage] | ErrorResult,
        ]
        | None
    ) = None
    """Called with the call's args, the folder, the profile's choice of its documents and the
    documents added to the source (#584); a source without added documents ignores them."""


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
    """A drafted remediation item for a requirement not or partly evidenced (#212), until the
    lead approves it."""

    requirement_id: str
    missing: str
    owner_role: str
    due_date: str  # ISO 8601

    @classmethod
    def not_drafted(cls, requirement_id: str, today: date) -> "RemediationItem":
        """An item for the lead to fill in."""
        due = (today + timedelta(days=DEFAULT_DUE_DAYS)).isoformat()
        return cls(requirement_id, NOT_DRAFTED, NO_OWNER, due)


NO_NUL_PATTERN = r"^[^\x00]*$"
"""Text the lead writes holds no NUL character: Postgres can't keep one (#39), so it's refused
(a 422) whatever the backend."""


class OverrideRequest(BaseModel):
    """The lead changes one requirement's verdict, with a reason."""

    requirement_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    verdict: JudgedVerdict
    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, pattern=NO_NUL_PATTERN)
    ]


class ConfirmFindings(BaseModel):
    """The first step of the lead's review (Confirm findings): the requirements' verdicts
    confirmed, any overridden with a reason; one not overridden is confirmed as Vouch judged it."""

    overrides: list[OverrideRequest] = Field(default_factory=list)


OVERRIDES_AT_CONFIRM = (
    "Overrides are recorded with Confirm findings, not with the decision: confirm the findings "
    "with them first."
)


class ReviewDecision(BaseModel):
    """The compliance lead's decision on one control, the review's last step (Accept), or its
    end at any step (Reject): the decision and a note. Overrides go with Confirm findings: a
    decision carrying them (a page loaded before the three-step review) is refused, so none is
    silently dropped."""

    decision: Decision
    note: Annotated[str, StringConstraints(pattern=NO_NUL_PATTERN)] = ""

    @model_validator(mode="before")
    @classmethod
    def _no_overrides(cls, data: Any) -> Any:
        if isinstance(data, dict) and "overrides" in data:
            raise ValueError(OVERRIDES_AT_CONFIRM)
        return data


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
    """The lead's recorded decision on the control, with who made it and when (#42): the
    reviewer's user id and display name at review time (#154), and the time in ISO 8601. A
    review kept before #42 has them empty."""

    decision: Decision
    note: str
    overrides: list[Override]
    reviewer_id: str = ""
    reviewer_name: str = ""
    reviewed_at: str = ""
    three_step: bool = False
    """Made in the three-step review (Confirm findings, Plan fixes, Accept; #372). A review kept
    before it reads False, its default."""

    @property
    def status(self) -> "Status":
        return REVIEWED[self.decision]


Status = Literal["waiting-for-review", "accepted", "rejected"]
WAITING: Status = "waiting-for-review"
REVIEWED: dict[Decision, Status] = {"accept": "accepted", "reject": "rejected"}
WAITING_TEXT = "Waiting for the compliance lead's review."
WAITING_TO_ACCEPT_TEXT = "Findings confirmed. Waiting for the compliance lead's plan and decision."


class UnknownAssessmentError(LookupError):
    """No assessment has this thread id."""


class NotWaitingForReviewError(RuntimeError):
    """The assessment exists but isn't paused at the review (it was reviewed already)."""


class WrongStepError(NotWaitingForReviewError):
    """The assessment waits at another step of the review than the one asked for: its findings
    confirmed already, or not yet confirmed for Accept (a 409, as a review made already)."""


CONFIRM_FIRST = "Confirm the findings first."
CONFIRMED_ALREADY = "The findings of this assessment were confirmed already."
DRAFTS_LEFT = "Approve or discard the remediation items of {ids} first."
NO_DRAFTS: "Callable[[Assessment], Sequence[str]]" = lambda _: ()
"""No remediation item left a draft: what `resume` assumes unless told (vouch.remediation keeps
the decisions)."""


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
    answer_language: str = "en"  # the profile's: the answer, reasons and drafts are in it
    remediation: list[RemediationItem] = field(default_factory=list)
    thread_id: str = ""  # resume this thread with the lead's review decision
    status: Status = WAITING
    review: Review | None = None
    confirmed: list[Override] | None = None
    """The overrides recorded with Confirm findings, once the findings are confirmed (#372); None
    before, and on a review kept before the three-step review."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def in_review(assessment: Assessment) -> bool:
    """Whether the assessment's review is in progress (#437): waiting for review with its
    findings confirmed, at Plan fixes or Accept. A profile pass never sets such a review aside,
    and the app warns before Assess again does."""
    return assessment.status == WAITING and assessment.confirmed is not None


def counts_towards_roll_up(requirement: RequirementVerdict) -> bool:
    """Whether the requirement's verdict counts towards the control's (`roll_up`): a must,
    high, very high or overlay requirement that applies."""
    return requirement.column in ROLL_UP_COLUMNS and requirement.verdict != NOT_APPLICABLE


def roll_up(requirements: Iterable[RequirementVerdict]) -> Verdict:
    """The control's verdict, over must, high, very high and overlay requirements, leaving out
    not applicable ones: all evidenced gives evidenced; none evidenced (only partly or not
    evidenced) gives not-evidenced; otherwise partly-evidenced. With none of those left, the
    control is not applicable."""
    counted = [r.verdict for r in requirements if counts_towards_roll_up(r)]
    if not counted:
        return NOT_APPLICABLE
    if all(v == "evidenced" for v in counted):
        return "evidenced"
    if not any(v == "evidenced" for v in counted):
        return "not-evidenced"
    return "partly-evidenced"


def roll_up_words(
    requirements: Iterable[RequirementVerdict], verdict: Verdict | None = None
) -> str | None:
    """Why the control has its verdict (the one shown, else `roll_up`'s), when a row's verdict
    differs from it, as when partly evidenced musts roll up to not evidenced (#212); None when
    every applicable row matches. English; the working paper's copy (review.ts rollUpWords) says
    it in the profile's language too, and both are checked against
    tests/roll_up_words_cases.json."""
    requirements = list(requirements)
    verdict = verdict or roll_up(requirements)
    if all(r.verdict == verdict for r in requirements if r.verdict != NOT_APPLICABLE):
        return None
    counted = [r for r in requirements if counts_towards_roll_up(r)]
    n = len(counted)
    evidenced = sum(r.verdict == "evidenced" for r in counted)
    partly = sum(r.verdict == "partly-evidenced" for r in counted)
    musts_only = bool(counted) and all(r.column == "must" for r in counted)
    noun = "must requirement" if musts_only else "must, protection-level or overlay requirement"
    shoulds = "should requirements count towards the should score only"
    if verdict == "evidenced":
        if n == 1:
            return f"Evidenced: its one {noun} is fully evidenced; {shoulds}."
        return f"Evidenced: all {n} {noun}s are fully evidenced; {shoulds}."
    if verdict == "partly-evidenced":
        agree = _verb(evidenced, "is", "are")
        return f"Partly evidenced: {evidenced} of {n} {noun}s {agree} fully evidenced."
    if verdict == "not-evidenced":
        if partly:
            return (
                f"Not evidenced: no {noun} is fully evidenced ({partly} of {n} partly evidenced)."
            )
        return f"Not evidenced: no {noun} is evidenced; {shoulds}."
    return f"Not applicable: no {noun} applies; {shoulds}."


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
    # The last step's guard sentences: the tools step's refusals and flagged passages, or the
    # apply_overlays step's flagged lead statements (#422).
    guards: list[str]
    searched: list[str]  # the last tools step's searches that ran, one sentence each
    # The tools pending in this run, by label: EVIDENCE_PENDING or TIMED_OUT. An update of ""
    # clears one (it answered later).
    pending: Annotated[dict[str, str], _merge_pending]
    pending_said: list[str]  # the last tools step's sentences about pending sources
    pending_ids: list[str]  # requirements left not evidenced while a source was pending
    # Judged on the lead's statements though no source could be read (#422): that reason, and the
    # requirements citing no statement, left not evidenced with it and no remediation item.
    unread_reason: str | None
    unread_ids: list[str]
    unreadable: list[str]  # the last tools step's sources that couldn't be read, by label
    searches_run: Annotated[int, operator.add]
    searches_refused: Annotated[int, operator.add]
    usage: Annotated[list[dict[str, int]], operator.add]
    answer: str | None
    answer_retries: int
    rejected_word: str | None  # the audit-result word that made check_answer refuse the answer
    language_retries: int
    wrong_language: str | None  # the language that made check_answer ask for the answer again
    kept_language: str | None  # the wrong language of an answer kept after that retry
    judge_rejected_word: str | None  # the one that made the judge ask for a new judgement
    requirements: list[RequirementVerdict]
    not_judged: list[str]
    claims: dict[str, list[CitationClaim]]  # the judge's citations per requirement, unchecked
    document_folders: dict[str, str]  # source name to folder: where cited documents are found
    # Source name to the documents of its folder the profile chose (EvidenceSource.documents); a
    # source using its whole folder is absent. A state saved before #384 has none.
    document_choices: dict[str, list[str]]
    about_doing: list[str]  # the requirements the judge said ask for something to be done
    dropped_citations: list[DroppedCitation]
    verdict_changes: list[str]  # "M1 to partly evidenced": verdicts the quote check changed
    messages: Annotated[list[AnyMessage], add_messages]
    today: str  # ISO 8601: due dates count from it
    note: str  # the lead's note from a rejected review, when this run was asked for again
    remediation: list[RemediationItem]
    remediate_rejected_word: str | None
    partly_drafted: int  # of the drafts made, those for partly evidenced requirements (#212)
    not_drafted: list[str]
    confirmed: list[Override]  # the overrides confirmed with the findings (#372)
    review: Review
    steps: Annotated[list[dict[str, str]], operator.add]  # each node's described step
    model: str  # the model that drafted it, and its prompt version
    prompt_version: str
    answer_language: str  # the profile's, ISO 639-1
    lead_statements: list[LeadStatement]  # the lead's on this control (#422)
    # Each added document's version a search returned (#584), {"doc_id", "version"}: the quote
    # check reads those versions, so a quote is checked against what the model read.
    added_read: Annotated[list[dict[str, Any]], operator.add]


def format_requirements(requirements: Iterable[Requirement]) -> str:
    return "\n".join(f"- {r.id} ({r.column}): {r.text}" for r in requirements)


def never_claim(profile: ComplianceProfile) -> str:
    """The rule against claiming an audit result, naming the words in the answer language."""
    words = audit_words_in(profile.answer_language) if profile.answer_language != "en" else ""
    in_language = f" ({words})" if words else ""
    return f"never call the company {audit_words_in('en')}{in_language}."


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


def gather_prompt(
    control: IsaControl,
    control_requirements: ControlRequirements,
    profile: ComplianceProfile,
    note: str = "",
    statements: list[EvidencePassage] | None = None,
) -> list[AnyMessage]:
    """The gather step's prompt: the system rules and the control with its requirements, the
    lead's `statements` and a rejection `note` when there are some (the developer page shows it,
    vouch.prompts)."""
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
            f"{language_name(profile.answer_language)}; quote a document only unchanged, in its "
            f"own language. Say what the evidence shows; {never_claim(profile)} {DATA_RULE}"
            f"{lead_rule(statements)}"
        ),
        HumanMessage(
            f"Control {control.id}: {control.question}\n"
            f"Requirements:\n{format_requirements(control_requirements.applicable)}"
            f"{statements_text(statements or [])}"
            f"{_note_text(note)}"
        ),
    ]


def overlay_prompt(overlay_requirements: Iterable[Requirement]) -> HumanMessage:
    """Asks the gathering for evidence for each country-overlay requirement too."""
    listed = "\n".join(f"- {r.id} ({r.source}): {r.text}" for r in overlay_requirements)
    return HumanMessage(
        "These requirements come from a country overlay, national rules added to this "
        f"control. Gather evidence for each of them too:\n{listed}"
    )


def _statements_of(state: AssessState, profile: ComplianceProfile) -> list[EvidencePassage]:
    """The lead's statements on the control, as the prompts list them (#422)."""
    return statement_passages(state.get("lead_statements", []), profile.answer_language)


def judge_prompt(state: AssessState, profile: ComplianceProfile) -> list[AnyMessage]:
    """The judge's prompt: the requirements, the evidence found, the lead's statements, the draft
    answer and any rejection note, from the run's state."""
    control = state["control"]
    pending = [
        f"{label} could not be read, so its evidence is pending" for label in state["pending"]
    ]
    passages = [*state["evidence"], *_statements_of(state, profile)]
    evidence = as_data(passages, pending) if passages or pending else "(none)"
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
            "evidence. Stale evidence shows a requirement partly at most. Write each reason in "
            f"{language_name(profile.answer_language)}; copy each quote unchanged, in its "
            f"document's language, never translated. {never_claim(profile)} {DATA_RULE}"
            f"{lead_rule(state.get('lead_statements'))}"
        ),
        HumanMessage(
            f"Control {control.id}: {control.question}\n"
            f"Requirements:\n{format_requirements(state['control_requirements'].applicable)}\n\n"
            f"Evidence:\n{evidence}\n\n"
            f"The earlier model's draft answer:\n{draft_as_data(state.get('answer') or '')}"
            f"{_note_text(state.get('note', ''))}"
        ),
    ]


TO_FIX: frozenset[Verdict] = frozenset({"not-evidenced", "partly-evidenced"})
"""The verdicts a remediation item is drafted for: every applicable requirement that isn't
evidenced, partly evidenced too (#212, the owner's decision 2026-11-06)."""


def to_remediate(
    requirements: list[RequirementVerdict], pending_ids: Iterable[str]
) -> list[RequirementVerdict]:
    """The requirements a remediation item is for, not or partly evidenced: not those whose
    evidence is pending, which would show a gap that may not be there."""
    pending = set(pending_ids)
    return [r for r in requirements if r.verdict in TO_FIX and r.id not in pending]


def remediation_prompt(
    control: IsaControl, requirements: list[RequirementVerdict], profile: ComplianceProfile
) -> list[AnyMessage]:
    """The remediation step's prompt: one item asked for each requirement listed."""
    to_fix = "\n".join(
        f"- {r.id} ({r.column}, {verdict_words(r.verdict)}): {r.text}\n  Why: {r.reason}"
        for r in requirements
    )
    return [
        SystemMessage(
            "Draft one remediation item for each requirement listed, which the company's "
            "evidence does not fully show: what is missing, the role that should own the fix (a "
            f"role, never a person's name), and how many days from today (1 to {MAX_DUE_DAYS}) "
            f"until it is due. Write each item in {language_name(profile.answer_language)}. "
            "For a partly evidenced requirement, say what is still missing for it to be fully "
            "evidenced, such as a stale document to review and approve again, or implemented "
            "records for steps that are only documented. "
            f"Say what is missing; {never_claim(profile)} {DATA_RULE}"
        ),
        HumanMessage(f"Control {control.id}: {control.question}\nNot fully evidenced:\n{to_fix}"),
    ]


def remediation_drafts(
    to_fix: list[RequirementVerdict], plan: RemediationPlan | None, today: date
) -> tuple[list[RemediationItem], list[str]]:
    """One remediation item per requirement to fix, in the requirements' order, and the
    ids the model didn't draft.

    A skipped requirement gets an item saying "not drafted" (RemediationItem.not_drafted).
    Items for other requirements are ignored; of two for one requirement, the first counts.
    """
    given: dict[str, RemediationItemReply] = {}
    for item in plan.items if plan else []:
        given.setdefault(item.requirement_id, item)
    drafts, not_drafted = [], []
    for req in to_fix:
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
    """The remediation items after overrides: one per requirement still not or partly
    evidenced, except those still pending. A requirement overridden to evidenced loses its item;
    one overridden from evidenced to not or partly (pending or not) gets a "not drafted" item
    for the lead."""
    by_id = {item.requirement_id: item for item in drafted}
    return [
        by_id.get(r.id) or RemediationItem.not_drafted(r.id, today)
        for r in to_remediate(requirements, pending_ids)
    ]


def apply_overrides(
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


UNREAD_REASONS = frozenset({NO_SOURCE, NO_ALLOWED_SEARCH})
"""The reasons no allowed source could be read, which a requirement citing no lead statement
keeps when the control is judged on the statements alone (#422)."""


def _kept_unread(
    requirements: list[RequirementVerdict], reason: str | None
) -> tuple[list[RequirementVerdict], list[str]]:
    """With no source read (`reason`, one of UNREAD_REASONS), each applicable requirement that
    cites no lead statement is not evidenced for that reason, its citations gone, and the ids of
    those requirements: the source may hold what they lack, so no remediation item is drafted
    for them, as for pending ones (#422). Nothing changes without a reason."""
    if reason is None:
        return requirements, []
    kept, ids = [], []
    for r in requirements:
        if r.verdict == NOT_APPLICABLE or any(c.lead_statement for c in r.citations):
            kept.append(r)
            continue
        kept.append(replace(r, verdict="not-evidenced", reason=reason, citations=()))
        ids.append(r.id)
    return kept, ids


def _noted_pending(
    requirements: list[RequirementVerdict], pending: dict[str, str]
) -> tuple[list[RequirementVerdict], list[str]]:
    """With sources pending, the reason of each requirement not or partly evidenced notes it
    (unless it is the pending reason already), and the ids of those requirements: a pending
    source may hold what they lack, so no remediation item is drafted for them (#212)."""
    if not pending:
        return requirements, []
    note = pending_reason(pending)
    noted = [
        replace(r, reason=f"{r.reason} ({note})") if r.verdict in TO_FIX and r.reason != note else r
        for r in requirements
    ]
    return noted, [r.id for r in noted if r.verdict in TO_FIX]


class JudgedRequirements(NamedTuple):
    """What the judge step reads from one judgement."""

    results: list[RequirementVerdict]
    not_judged: list[str]  # ids the model didn't judge validly
    claims: dict[str, list[CitationClaim]]  # the citations given per id, unchecked
    about_doing: list[str]  # ids the model said ask for something to be done


def judged_requirements(
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


def _gathered(state: AssessState) -> list[str]:
    """The texts of the passages this assessment gathered: search results, records, Patch, and
    the lead's statements (#422)."""
    statements = [s.answer for s in state.get("lead_statements", [])]
    return [p["text"] for p in state.get("evidence", [])] + statements


def ask_until_clean[T](
    model: Runnable,
    prompt: list[AnyMessage],
    read: Callable[[Any], tuple[T, list[str]]],
    *,
    what: str,
    refusal: Callable[[str], str],
    passages: list[str],
) -> tuple[T, list[dict[str, int]], str | None]:
    """Ask a structured-output model; `read` turns its parsed reply into a result and the
    texts to check. A text claiming an audit result (quotes from `passages` aside) gets the
    reply asked for again once, with `refusal(word)` added to the prompt, then
    OutputCheckError naming `what`.

    Returns the result, the token usage, and the word that was refused (or None)."""
    usage: list[dict[str, int]] = []
    rejected = None
    for attempt in range(OUTPUT_RETRIES + 1):
        reply = model.invoke(prompt)
        usage += _usage_of(reply["raw"])
        result, texts = read(reply["parsed"])
        word = next((w for text in texts if (w := claims_audit_result(text, passages))), None)
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
        statements = _statements_of(state, profile)
        prompt = gather_prompt(
            control, control_requirements, profile, state.get("note", ""), statements
        )
        return {
            "control_requirements": control_requirements,
            "overlay_requirements": overlay_requirements_of(control_requirements),
            "messages": prompt,
            "guards": flagged_steps(statements),  # an instruction-like answer (#422)
        }

    def overlay_branch(state: AssessState) -> dict[str, Any]:
        """Asks the gathering to find evidence for each overlay requirement too."""
        overlay_requirements = state["overlay_requirements"]
        return {
            # Unchanged; given again only so this step's describe() can name them.
            "overlay_requirements": overlay_requirements,
            "messages": [overlay_prompt(overlay_requirements)],
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
        read: list[dict[str, Any]] = []  # added documents' versions the searches returned
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
                # The state's choice, not the live profile's: the quote check reads the same one.
                documents = choice_in(state.get("document_choices"), tool.source)
                added = added_index().searchable(profile.id, tool.source)
                result = tool_span(name, args, partial(tool.run, args, folder, documents, added))
                if isinstance(result, list):
                    read += versions_read(result, added)
                    result = with_facts(result, tool.source, folder, today, added)
                content = as_data(result) if isinstance(result, list) else json.dumps(result)
                if isinstance(result, list):
                    run += 1
                    found = plural(len(result), "passage")
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
            "added_read": read,
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
        Written in another language than the profile's, it is asked for again once in the
        profile's language, then kept as it is, the step noting the mismatch (#51).

        A refusal answers every tool call of the model's message, as a provider requires
        before the next call; a search asked for alongside the answer is not run.
        """
        answer = state["answer"] or ""
        word = claims_audit_result(answer, _gathered(state))
        if word is not None:
            retries = state.get("answer_retries", 0)
            if retries >= OUTPUT_RETRIES:
                raise OutputCheckError(f"the answer claimed an audit result (“{word}”) again")
            refusal = (
                f"Answer refused: it says “{word}”, an audit result Vouch never claims. "
                f"Say what the evidence shows instead, and call {SUBMIT_ANSWER} again."
            )
            update = {"answer_retries": retries + 1, "rejected_word": word}
            return _refused(state, refusal, update)
        clean = {"rejected_word": None, "wrong_language": None, "kept_language": None}
        found = mismatch(answer, profile.answer_language)
        if found is None:
            return clean
        language_retries = state.get("language_retries", 0)
        if language_retries >= OUTPUT_RETRIES:
            return {**clean, "kept_language": found}
        expected = language_name(profile.answer_language)
        refusal = (
            f"Answer refused: {language_refusal(language_name(found), expected)}, and call "
            f"{SUBMIT_ANSWER} again."
        )
        update = {**clean, "language_retries": language_retries + 1, "wrong_language": found}
        return _refused(state, refusal, update)

    def _refused(state: AssessState, refusal: str, update: dict[str, Any]) -> dict[str, Any]:
        """The answer refused with this reply to its call, and the update to record."""
        not_run = "Not run: the answer in the same message was refused. Call it again if needed."
        replies = [
            ToolMessage(
                refusal if call["name"] == SUBMIT_ANSWER else not_run, tool_call_id=call["id"]
            )
            for call in state["messages"][-1].tool_calls
        ]
        return {**update, "answer": None, "messages": replies}

    def judge(state: AssessState) -> dict[str, Any]:
        """The model judges each requirement; the code keeps only valid verdicts for them.

        A reason claiming an audit result gets the judgement asked for again once, then an error.
        """
        control_requirements = state["control_requirements"]
        reason = no_evidence_reason(state)
        # With the lead's statements there is always something to judge on (#422); where no
        # source could be read, only what a statement shows counts (check_quotes).
        if reason is not None and not state.get("lead_statements"):  # nothing read, none shown
            results = judged_requirements(control_requirements, None, reason).results
            return {
                "requirements": results,
                "not_judged": [],
                "claims": {},
                "about_doing": [],
                "unread_reason": None,
            }
        unread = reason if reason in UNREAD_REASONS else None

        def read(judgement: Judgement | None) -> tuple[JudgedRequirements, list[str]]:
            judged = judged_requirements(control_requirements, judgement)
            return judged, [r.reason for r in judged.results if r.verdict != NOT_APPLICABLE]

        judged, usage, rejected = ask_until_clean(
            judge_model,
            judge_prompt(state, profile),
            read,
            what="a judgement's reason",
            refusal=lambda word: (
                f"Judgement refused: a reason says “{word}”, an audit result Vouch never "
                "claims. Judge again, saying only what the evidence shows."
            ),
            passages=_gathered(state),
        )
        return {
            "requirements": judged.results,
            "not_judged": judged.not_judged,
            "claims": judged.claims,
            "about_doing": judged.about_doing,
            "judge_rejected_word": rejected,
            "usage": usage,
            "unread_reason": unread,
        }

    def check_quotes(state: AssessState) -> dict[str, Any]:
        """Keep each citation whose quote is in the document it cites; drop and log the rest.

        A requirement judged evidenced or partly evidenced with no citation left (none given,
        or every one dropped) becomes not-evidenced: nothing checked shows it. Then the
        judge's rules (`_apply_rules`) cap what the checked citations can show. Each document
        is read once.
        """
        checker = quote_checker(state, label=lead_statement_label(profile.answer_language))
        results, dropped = checked_requirements(
            state["requirements"],
            state.get("claims", {}),
            checker,
            state.get("about_doing", []),
            profile.answer_language,
        )
        results, unread_ids = _kept_unread(results, state.get("unread_reason"))
        results, pending_ids = _noted_pending(results, state["pending"])
        changes = verdict_changes(state["requirements"], results)
        return {
            "requirements": results,
            "dropped_citations": dropped,
            "pending_ids": pending_ids,
            "unread_ids": unread_ids,
            "verdict_changes": changes,
        }

    def remediate(state: AssessState) -> dict[str, Any]:
        """The model drafts one remediation item per requirement not or partly evidenced (#212);
        none of those, no call. A draft claiming an audit result gets the plan asked for again
        once, then an error. Before the interrupt, so it only reads and asks the model."""
        unread = state.get("unread_ids", [])
        to_fix = to_remediate(state["requirements"], [*state["pending_ids"], *unread])
        said_unread = {"unread_ids": unread, "unread_reason": state.get("unread_reason")}
        if not to_fix:
            return {
                "remediation": [],
                "not_drafted": [],
                "remediate_rejected_word": None,
                "partly_drafted": 0,
                "pending_ids": state["pending_ids"],
                **said_unread,
            }
        today = date.fromisoformat(state["today"])

        def read(plan: RemediationPlan | None) -> tuple[Any, list[str]]:
            drafts, not_drafted = remediation_drafts(to_fix, plan, today)
            return (drafts, not_drafted), [f"{d.missing} {d.owner_role}" for d in drafts]

        (drafts, not_drafted), usage, rejected = ask_until_clean(
            remediation_model,
            remediation_prompt(state["control"], to_fix, profile),
            read,
            what="a remediation draft",
            refusal=lambda word: (
                f"Remediation refused: a draft says “{word}”, an audit result Vouch never "
                "claims. Draft again, saying only what is missing."
            ),
            passages=_gathered(state),
        )
        partly = {r.id for r in to_fix if r.verdict == "partly-evidenced"}
        return {
            "remediation": drafts,
            "not_drafted": not_drafted,
            "remediate_rejected_word": rejected,
            "partly_drafted": len(partly - set(not_drafted)),
            "usage": usage,
            "pending_ids": state["pending_ids"],
            **said_unread,
        }

    def review(state: AssessState) -> dict[str, Any]:
        """Pause for the compliance lead's Confirm findings, the review's first step (#372; the
        node keeps its name, so a thread waiting here from before resumes into it). This node
        re-runs from its start on resume, so nothing before the interrupt writes; the lead's
        findings come back as its value: the overrides applied, the roll-up and remediation
        items recalculated, the overrides kept for the review. Or a rejection, which ends the
        review here."""
        answer = interrupt({"control_id": state["control"].id})
        if DECISION in answer:  # Reject (`resume` lets no Accept through here)
            decision = ReviewDecision.model_validate(_fields(answer))
            return {"review": _recorded(decision, [], answer)}
        findings = ConfirmFindings.model_validate(_fields(answer))
        requirements, overrides = apply_overrides(state["requirements"], findings.overrides)
        today = date.fromisoformat(state["today"])
        overridden = {o.requirement_id for o in overrides}
        left_out = [*state["pending_ids"], *state.get("unread_ids", [])]
        still_pending = [i for i in left_out if i not in overridden]
        remediation = _reviewed_remediation(
            requirements, state["remediation"], today, still_pending
        )
        return {"requirements": requirements, "remediation": remediation, "confirmed": overrides}

    def accept(state: AssessState) -> dict[str, Any]:
        """Pause for the lead's decision once the findings are confirmed: Accept (after Plan
        fixes, which happens outside the graph, vouch.remediation) or Reject. The review keeps
        the overrides confirmed with the findings."""
        answer = interrupt({"control_id": state["control"].id})
        decision = ReviewDecision.model_validate(_fields(answer))
        return {"review": _recorded(decision, state["confirmed"], answer)}

    def after_review(state: AssessState) -> str:
        return END if state.get("review") is not None else "accept"

    def after_gather(state: AssessState) -> str:
        return "check_answer" if state.get("answer") is not None else "tools"

    def after_check(state: AssessState) -> str:
        return "judge" if state.get("answer") is not None else "gather"

    def described(node: str, run: Any) -> Any:
        """The node, recording its step in the state, so a resumed thread keeps every step."""

        def run_and_describe(state: AssessState) -> dict[str, Any]:
            update = run(state)
            steps = [Step(node, update, profile), *guard_steps(update)]
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
        "accept": accept,
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
    graph.add_conditional_edges("review", after_review, ["accept", END])
    graph.add_edge("accept", END)
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
        if self.node == "load_control":
            control: IsaControl = update["control"]
            requirements: ControlRequirements = update["control_requirements"]
            question = self.quote()
            applicable, excluded = len(requirements.applicable), len(requirements.not_applicable)
            text = (
                f"Loaded control {control.id}: {question} "
                f"{plural(applicable, 'requirement')} {_verb(applicable, 'applies', 'apply')} "
                f"under {self.profile.company}'s profile"
            )
            if excluded:
                text += f"; {excluded} {_verb(excluded, 'is', 'are')} not applicable"
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
            expected = (
                language_name(self.profile.answer_language)
                if self.profile
                else "the profile's language"
            )
            if wrong := update.get("wrong_language"):
                written = written_in_not(language_name(wrong), expected)
                return f"The answer was {written}, so it was asked for again in {expected}."
            text = "Checked the answer: it claims no audit result."
            if kept := update.get("kept_language"):
                text += f" It is {kept_in_other_language(language_name(kept), expected)}."
            return text
        if self.node == "check_quotes":
            kept = sum(len(r.citations) for r in update["requirements"])
            dropped = len(update["dropped_citations"])
            text = f"Checked each quote against its document: kept {plural(kept, 'citation')}"
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
            unread = update.get("unread_ids", [])
            unread_said = f" None for {', '.join(unread)}: {update.get('unread_reason')}."
            if not drafts and (pending or unread):
                text = f"No remediation item drafted: {waiting}." if pending else ""
                return (text + (unread_said if unread else "")).strip()
            if not drafts:
                return (
                    "Every requirement that applies is evidenced, so no remediation item was "
                    "drafted."
                )
            not_drafted = update.get("not_drafted", [])
            made = len(drafts) - len(not_drafted)  # not the placeholders
            partly = update.get("partly_drafted", 0)
            text = (
                f"Drafted {plural(made, 'remediation item')}"
                + (f" ({partly} partly evidenced)." if partly else ".")
                if made
                else "No remediation item drafted by the model."
            )
            if pending:
                text += f" None for {', '.join(pending)}: {waiting}."
            if unread:
                text += unread_said
            word = update.get("remediate_rejected_word")
            if word:
                text = (
                    f"A draft claimed an audit result (“{word}”), so the drafts were asked for "
                    f"again. {text}"
                )
            if not_drafted:
                text += " Not drafted: " + ", ".join(not_drafted) + "."
            return text
        if self.node in ("review", "accept"):
            review = update.get("review")
            confirmed = update.get("confirmed")
            if review is None and confirmed is None:
                return WAITING_TEXT if self.node == "review" else WAITING_TO_ACCEPT_TEXT
            text = (
                "The compliance lead confirmed the findings"
                if review is None
                else f"The compliance lead {review.status} the assessment"
            )
            overrides = confirmed if review is None else review.overrides
            if overrides and self.node == "review":  # the accept step's were named at confirm
                text += f", overriding {', '.join(o.requirement_id for o in overrides)}"
            return text + "."
        return f"Finished the {self.node} step."

    def quote(self) -> str | None:
        """The ISA edition's words the step's text quotes (the load step's question, shortened),
        so the page marks them in the profile's language (#244); None for a step quoting none."""
        if self.node != "load_control":
            return None
        question: str = self.update["control"].question
        return textwrap.shorten(question, QUESTION_SHOWN, placeholder="…")

    def to_dict(self) -> dict[str, str]:
        """The step as the stream and the Assessment carry it: its node and text, and `quote`,
        the edition's words in the text, when it quotes some."""
        quote = self.quote()
        return {"node": self.node, "text": self.describe(), **({"quote": quote} if quote else {})}


def loaded_step(control: IsaControl, profile: ComplianceProfile) -> Step:
    """The first step: the control, loaded before the graph runs, and what applies of it."""
    update = {"control": control, "control_requirements": profile.requirements_for(control)}
    return Step("load_control", update, profile)


def overlay_requirements_of(requirements: ControlRequirements) -> tuple[Requirement, ...]:
    """The applicable requirements the country overlays add to a control, in order: what the
    apply_overlays node sets (the profile pass too, #390)."""
    applicable = set(requirements.applicable)
    return tuple(r for r in requirements.overlay if r in applicable)


def overlays_step(overlay_requirements: tuple[Requirement, ...]) -> Step:
    """The apply_overlays step for these overlay requirements, in the agent's own words."""
    return Step("apply_overlays", {"overlay_requirements": overlay_requirements})


def _waiting_step(node: str = "review") -> Step:
    """The step a paused run ends with: the review node (or, once the findings are confirmed,
    the accept node), interrupted, made no update."""
    return Step(node, {})


def guard_steps(update: dict[str, Any]) -> list[Step]:
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


def _apply_rules(
    requirement: RequirementVerdict, about_doing: bool, language: str = "en"
) -> RequirementVerdict:
    """The judge's rules on a requirement whose citations are checked (spec #2, ticket #22).

    Stale evidence caps it at partly-evidenced, and the reason names each stale document and
    why; so does a lead statement cited, whatever else is (#422), the reason naming it. A
    requirement about doing something (as the judge said) needs implemented evidence to be
    evidenced; with documented evidence only it is capped too, and the reason says so.
    """
    if requirement.verdict not in SHOWN_VERDICTS:
        return requirement
    stale = {c.doc_id: c.stale_because for c in requirement.citations if c.stale}
    statements = [c.doc_id for c in requirement.citations if c.lead_statement]
    documented_only = all(c.kind == DOCUMENTED for c in requirement.citations)
    doing_unshown = about_doing and documented_only and requirement.verdict == "evidenced"
    # In the profile's answer language, as the rest of the reason is (#354).
    note = cap_note(stale, doing_unshown, language, dict.fromkeys(statements))
    if note is None:
        return requirement
    return replace(requirement, verdict="partly-evidenced", reason=f"{requirement.reason} {note}")


def checked_requirements(
    requirements: Iterable[RequirementVerdict],
    claims: dict[str, list[CitationClaim]],
    checker: QuoteChecker,
    about_doing: Iterable[str],
    language: str,
) -> tuple[list[RequirementVerdict], list[DroppedCitation]]:
    """The quote check and the judge's rules on judged requirements, for the graph's
    check_quotes step and the profile pass (vouch.profile_pass) alike.

    Each citation whose quote is in the document it cites is kept; the rest are dropped and
    logged. A requirement judged evidenced or partly evidenced with no citation left (none
    given, or every one dropped) becomes not-evidenced: nothing checked shows it. Then the
    judge's rules (`_apply_rules`) cap what the checked citations can show, the cap note in
    `language`."""
    doing = set(about_doing)
    results, dropped = [], []
    for requirement in requirements:
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
        results.append(_apply_rules(requirement, requirement.id in doing, language))
    return results, dropped


def verdict_changes(
    judged: Iterable[RequirementVerdict], checked: Iterable[RequirementVerdict]
) -> list[str]:
    """ "M1 to partly evidenced": each verdict the checks changed from the judged one."""
    before = {r.id: r.verdict for r in judged}
    return [f"{r.id} to {verdict_words(r.verdict)}" for r in checked if r.verdict != before[r.id]]


def plural(count: int, noun: str) -> str:
    """The count and the noun agreeing with it: plural(2, "passage") is "2 passages"."""
    return f"{count} {noun}" + ("" if count == 1 else "s")


def verdict_words(verdict: Verdict) -> str:
    """A verdict as words in a sentence: "partly-evidenced" is "partly evidenced"."""
    return verdict.replace("-", " ")


def _verb(count: int, one: str, many: str) -> str:
    """The verb that agrees with this count: _verb(1, "is", "are") is "is"."""
    return one if count == 1 else many


def model_name_of(model: BaseChatModel) -> str:
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
    LeadStatement,
    RemediationItem,
    Review,
    Override,
)


def state_serializer() -> JsonPlusSerializer:
    """How a checkpointer writes the graph's state and reads it back: LangGraph's safe types and
    STATE_TYPES only, not any type with a warning. Every saver (in memory, Postgres) uses it."""
    return JsonPlusSerializer(allowed_msgpack_modules=None).with_msgpack_allowlist(STATE_TYPES)


def quote_checker(state: AssessState, *, label: str = LEAD_STATEMENT) -> QuoteChecker:
    """The quote check for an assessment's state: its folders, limited to the documents it
    chose; the versions of added documents its searches read (#584, `added_read`; one withdrawn
    since is left out); and the lead's statements it was given (#422), shown under `label`. A
    state saved before `document_choices` (#384) is checked against the whole folders."""
    folders = state["document_folders"]
    read = sorted(state.get("added_read", []), key=lambda r: r["version"])  # newest read wins
    found = (added_index().read(state["profile_id"], r["doc_id"], r["version"]) for r in read)
    added = [d for d in found if d is not None and d.source in folders]
    return QuoteChecker(
        folders,
        today=date.fromisoformat(state["today"]),
        choices=state.get("document_choices"),
        statements=state.get("lead_statements", []),
        label=label,
        added=added,
    )


def memory_checkpointer() -> InMemorySaver:
    """LangGraph's in-memory saver, where a paused assessment waits for review when there's no
    database (the API's Postgres saver otherwise, vouch.database)."""
    return InMemorySaver(serde=state_serializer())


def thread_key(thread_id: str, scope: str = "") -> str:
    """The checkpointer's thread: `scope` (whose it is, vouch.memory) before the thread id, so
    one user's thread id names nothing for another."""
    return scope + thread_id


def thread_config(thread_id: str, scope: str = "") -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_key(thread_id, scope)}}


def _profile_language(profile_id: str) -> str:
    """The answer language of the profile a thread saved before #51 ran under (its state
    doesn't say), or English when that profile can't be read."""
    try:
        return load_named_profile(profile_id).answer_language
    except (ProfileError, UnknownProfileError):
        log.warning("thread for profile %s: answer language unknown, so English", profile_id)
        return "en"


def _assessment(values: dict[str, Any], thread_id: str) -> Assessment:
    """The Assessment from the thread's state: proposed while it waits, reviewed after."""
    requirements = values["requirements"]
    review: Review | None = values.get("review")
    steps = list(values["steps"])
    confirmed: list[Override] | None = values.get("confirmed")
    if review is None:
        status: Status = WAITING
        steps.append(_waiting_step("review" if confirmed is None else "accept").to_dict())
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
        answer_language=values.get("answer_language") or _profile_language(values["profile_id"]),
        remediation=values["remediation"],
        thread_id=thread_id,
        status=status,
        review=review,
        confirmed=confirmed,
    )


def assess_steps(
    control: IsaControl,
    profile: ComplianceProfile,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
    *,
    note: str = "",
    today: date | None = None,
    thread_id: str | None = None,
    scope: str = "",
    statements: Iterable[LeadStatement] = (),
    run_config: RunnableConfig | None = None,
) -> Iterator[Step | RequirementVerdict | Assessment]:
    """Run the graph for one loaded control, on a new thread, up to the review interrupt: a
    Step saying the control was loaded, a Step per node as it finishes (and a guard Step per
    dropped citation), each RequirementVerdict once its quotes are checked, a Step saying it
    waits for review, then the proposed Assessment, whose `thread_id` resumes it (see `resume`).

    Without a checkpointer the thread is kept in a fresh in-memory saver, so it can't be
    resumed. `note` is the lead's note from a rejected review, when the control is assessed
    again; `today` (default: the date now) is what due dates count from and what staleness is
    judged on. `thread_id` (default: a new one) must name a new thread; `scope` says whose
    thread it is (see `thread_config`). `statements` are the lead's on this control (#422): the
    judge may cite them, each showing a requirement partly at most. `run_config` joins the run's
    config: a trace's handler and attributes (vouch.tracing), every step and model call under it.

    The control is assessed as given: a caller picks its ISA edition, normally the profile's
    (`ComplianceProfile.isa_control`)."""
    if thread_id is None:
        thread_id = new_thread_id()
    graph = build_graph(model, profile, checkpointer or memory_checkpointer())
    loaded = loaded_step(control, profile)
    initial = {
        "control": control,
        "profile_id": profile.id,
        "model": model_name_of(model),
        "prompt_version": PROMPT_VERSION,
        "answer_language": profile.answer_language,
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
        "document_choices": profile.document_choices(),
        "added_read": [],
        "lead_statements": list(statements),
    }
    config = thread_config(thread_id, scope)
    yield loaded
    for node, update in (
        (node, update)
        for chunk in graph.stream(
            initial, with_run_config(config, run_config), stream_mode="updates"
        )
        for node, update in chunk.items()
    ):
        if node == "__interrupt__":
            yield _waiting_step()
            continue
        update = update or {}
        yield Step(node, update, profile)
        yield from guard_steps(update)
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
    run_config: RunnableConfig | None = None,
) -> Assessment:
    """Run the graph for one control under a compliance profile, on a new thread, up to the
    review interrupt and return the proposed Assessment; `resume` its `thread_id` with the
    lead's decision. Assessing a rejected control again is a new run, with the lead's `note`;
    `run_config` joins the run's config (a trace's, vouch.tracing).

    The control is read in the ISA edition of the profile's answer language. An unknown
    control raises UnknownControlError before the model is called."""
    *_, assessment = assess_steps(
        profile.isa_control(control_id),
        profile,
        model,
        checkpointer,
        note=note,
        today=today,
        run_config=run_config,
    )
    if not isinstance(assessment, Assessment):
        raise TypeError(f"the graph ended without an Assessment for {control_id}")
    return assessment


def new_thread_id() -> str:
    """A new assessment's thread id: always a new thread, never piled onto an earlier run's."""
    return uuid.uuid4().hex


def _reached_review(snapshot: StateSnapshot) -> bool:
    """Whether a thread holds an assessment: its run reached the review (waiting at it, or
    reviewed). A run that failed on the way has state, but no assessment."""
    return bool(snapshot.values) and bool(snapshot.interrupts or snapshot.values.get("review"))


def assessment_loader(
    checkpointer: BaseCheckpointSaver, *, scope: str = ""
) -> Callable[[str], Assessment | None]:
    """Reads Assessments from `scope`'s threads, the graph compiled once: proposed while one
    waits for review, reviewed after; None for a thread that holds no assessment."""
    graph = build_graph(None, checkpointer=checkpointer)

    def load(thread_id: str) -> Assessment | None:
        snapshot = graph.get_state(thread_config(thread_id, scope))
        return _assessment(snapshot.values, thread_id) if _reached_review(snapshot) else None

    return load


def load_assessment(
    thread_id: str, checkpointer: BaseCheckpointSaver, *, scope: str = ""
) -> Assessment:
    """The Assessment on a thread: proposed while it waits for review, reviewed after.

    Raises UnknownAssessmentError for a thread with no state, or one whose run never reached the
    review (it failed on the way): there's no assessment to show."""
    assessment = assessment_loader(checkpointer, scope=scope)(thread_id)
    if assessment is None:
        raise UnknownAssessmentError(f"no assessment {thread_id}")
    return assessment


REVIEWED_BY = "reviewed_by"
REVIEWED_AT = "reviewed_at"
"""Who decided and when: sent to the review node with the decision, beside its fields."""
DECISION = "decision"
"""The field a decision (Accept or Reject) carries and the findings don't: how the review node
tells a rejection at Confirm findings from the findings."""


def _fields(answer: dict[str, Any]) -> dict[str, Any]:
    """What the lead sent, without who and when (sent beside it)."""
    return {k: v for k, v in answer.items() if k not in (REVIEWED_BY, REVIEWED_AT)}


def _recorded(
    decision: ReviewDecision, overrides: list[Override], answer: dict[str, Any]
) -> Review:
    """The lead's decision as the thread keeps it, with who made it and when."""
    return Review(
        decision.decision,
        decision.note,
        overrides,
        reviewer_id=answer.get(REVIEWED_BY, {}).get("id", ""),
        reviewer_name=answer.get(REVIEWED_BY, {}).get("name", ""),
        reviewed_at=answer.get(REVIEWED_AT, ""),
        three_step=True,
    )


def _waiting(graph: Any, config: dict[str, Any], thread_id: str) -> StateSnapshot:
    """The thread's snapshot, waiting at a step of the review; UnknownAssessmentError or
    NotWaitingForReviewError when it isn't."""
    snapshot: StateSnapshot = graph.get_state(config)
    if not _reached_review(snapshot):
        raise UnknownAssessmentError(f"no assessment {thread_id}")
    if not snapshot.interrupts:
        raise NotWaitingForReviewError(f"assessment {thread_id} is not waiting for review")
    return snapshot


def confirm_findings(
    thread_id: str,
    findings: ConfirmFindings,
    checkpointer: BaseCheckpointSaver,
    *,
    scope: str = "",
) -> Assessment:
    """Resume a thread waiting at Confirm findings with the lead's findings, and return the
    assessment waiting for the decision (#372): overrides applied, the roll-up and remediation
    items recalculated. No model is called.

    Raises UnknownAssessmentError for an unknown thread (or one whose run never reached the
    review), NotWaitingForReviewError for one not waiting (reviewed already) and WrongStepError
    for one whose findings were confirmed already, and InvalidReviewError for an override
    `_check_overrides` refuses. Nothing is resumed when any is raised. `scope` says whose thread
    it is (see `thread_config`). As `resume`, it doesn't keep two steps of one thread apart: the
    caller does (vouch.audit)."""
    graph = build_graph(None, checkpointer=checkpointer)
    config = thread_config(thread_id, scope)
    snapshot = _waiting(graph, config, thread_id)
    if "confirmed" in snapshot.values:
        raise WrongStepError(CONFIRMED_ALREADY)
    _check_overrides(snapshot.values["requirements"], findings.overrides)
    values = graph.invoke(Command(resume=findings.model_dump()), config)
    return _assessment(values, thread_id)


def resume(
    thread_id: str,
    decision: ReviewDecision,
    checkpointer: BaseCheckpointSaver,
    *,
    scope: str = "",
    reviewer_id: str = "",
    reviewer_name: str = "",
    reviewed_at: datetime | None = None,
    drafts: Callable[[Assessment], Sequence[str]] = NO_DRAFTS,
) -> Assessment:
    """Resume a waiting thread with the lead's decision and return the reviewed Assessment: the
    decision recorded with who made it and when (the review's `reviewer_*` and `reviewed_at`),
    with the overrides confirmed with the findings. Accept is the review's last step, once the
    findings are confirmed (`confirm_findings`); Reject ends it at any step. No model is called.

    Raises UnknownAssessmentError for an unknown thread (or one whose run never reached the
    review), NotWaitingForReviewError for one not waiting (reviewed already), and WrongStepError
    for an Accept before the findings are confirmed, or while `drafts` names requirements whose
    remediation items are still drafts. Nothing is resumed when any is raised.
    `scope` says whose thread it is (see `thread_config`): another user's thread id is unknown.

    It doesn't keep two reviews of one thread apart: the caller does (the API through
    vouch.audit, whose trail holds a lock on the thread while it reviews and records)."""
    graph = build_graph(None, checkpointer=checkpointer)
    config = thread_config(thread_id, scope)
    snapshot = _waiting(graph, config, thread_id)
    if decision.decision == "accept":
        if "confirmed" not in snapshot.values:
            raise WrongStepError(CONFIRM_FIRST)
        left = drafts(_assessment(snapshot.values, thread_id))
        if left:
            raise WrongStepError(DRAFTS_LEFT.format(ids=", ".join(left)))
    answer = decision.model_dump() | {
        REVIEWED_BY: {"id": reviewer_id, "name": reviewer_name},
        REVIEWED_AT: reviewed_at.isoformat() if reviewed_at else "",
    }
    values = graph.invoke(Command(resume=answer), config)
    return _assessment(values, thread_id)
