"""Every prompt the agent gets, as the developer page shows it (#496, story 13).

Built for one company and one control by the agent's own builders (vouch.graph, vouch.profile_pass,
vouch.chat_agent), never a copy of their text, so a prompt changed there shows changed here. What
only a run knows (the evidence found, the draft answer, the lead's statements, a rejection note,
the judge's reasons) goes in as a labelled placeholder, `[from the run: …]`."""

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

from langchain_core.messages import AnyMessage, SystemMessage

from vouch.chat import CHAT_PROMPT_VERSION, PageContext
from vouch.chat_agent import chat_prompt
from vouch.citations import LeadStatement
from vouch.documents import DOCUMENTED
from vouch.graph import (
    PROMPT_VERSION,
    AssessState,
    EvidencePassage,
    RequirementVerdict,
    _judge_prompt,
    _overlay_prompt,
    _prompt,
    _remediation_prompt,
    overlay_requirements_of,
    statement_passages,
)
from vouch.isa import IsaControl
from vouch.profile import ComplianceProfile
from vouch.profile_pass import PROFILE_PASS_PROMPT_VERSION, _Included
from vouch.profile_pass import _prompt as _profile_pass_prompt


def from_the_run(what: str) -> str:
    """A placeholder for what only a run knows."""
    return f"[from the run: {what}]"


NOTE = from_the_run("the lead's rejection note, on a run asked for again")
DRAFT = from_the_run("the gather step's draft answer")
REASON = from_the_run("the judge's reason")
NOT_SENT = "Not sent for this control: it has no overlay requirement."

NOTES = {
    "gather": (
        "Sent first on every assessment. The rejection note appears only on a run asked for "
        "again, and the lead's statements, with the rule on citing them, only when there are "
        "some on the control."
    ),
    "overlay": (
        "Sent after the first prompt when the control has overlay requirements, asking for "
        "evidence for each of them too."
    ),
    "judge": (
        "Sent once gathering ends, with the evidence the run found. The rejection note appears "
        "only on a run asked for again, and the lead's statements, with the rule on citing them, "
        "only when there are some on the control."
    ),
    "remediation": (
        "Sent after the judge when a requirement isn't fully evidenced. A run lists only the "
        "requirements not or partly evidenced, each with its verdict and the judge's reason."
    ),
    "profile_pass": (
        "Sent once per profile pass. A run lists every applicable control not yet accepted or in "
        "review, and every document of every allowed source, whole."
    ),
    "chat": (
        "Sent with every chat message. The page named is the one the lead is on; here, the "
        "chosen control's working paper."
    ),
}


@dataclass(frozen=True)
class PromptMessage:
    role: Literal["system", "human"]
    text: str


@dataclass(frozen=True)
class PromptPreview:
    """One prompt as the developer page shows it: `sent` is False only for the overlay prompt
    of a control with no overlay requirement, which then has no messages."""

    name: str
    title: str
    version: str
    sent: bool
    note: str
    messages: tuple[PromptMessage, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _messages(prompt: list[AnyMessage]) -> tuple[PromptMessage, ...]:
    return tuple(
        PromptMessage("system" if isinstance(m, SystemMessage) else "human", str(m.content))
        for m in prompt
    )


def _statement(control: IsaControl) -> LeadStatement:
    """One lead statement on the control, its question and answer placeholders."""
    return LeadStatement(
        id=1,
        control_id=control.id,
        answer=from_the_run("the lead's answer"),
        answered_on=date(2000, 1, 1),  # not shown in any prompt
        question=from_the_run("the open question the lead answered"),
    )


def _passage(text: str, heading: str) -> EvidencePassage:
    """An evidence passage of placeholders, as a run would find one."""
    return EvidencePassage(
        doc_id=from_the_run("a document's id"),
        heading=heading,
        text=text,
        source=from_the_run("the source it was found in"),
        kind=DOCUMENTED,
        stale=False,
    )


def prompt_previews(profile: ComplianceProfile, control: IsaControl) -> list[PromptPreview]:
    """The six prompts the agent uses, in its order: gather evidence, the country overlay, the
    judge, remediation, the profile pass and the chat, built for `profile` and `control` (one
    of the profile's applicable controls, in its ISA edition)."""
    requirements = profile.requirements_for(control)
    statement = _statement(control)
    statements = statement_passages([statement], profile.answer_language)
    overlay = overlay_requirements_of(requirements)
    judged: AssessState = {
        "control": control,
        "control_requirements": requirements,
        "pending": {},
        "evidence": [
            _passage(from_the_run("the passage found"), from_the_run("the passage's heading"))
        ],
        "lead_statements": [statement],
        "answer": DRAFT,
        "note": NOTE,
    }
    not_evidenced = [
        RequirementVerdict(r.id, r.column, r.text, "not-evidenced", REASON)
        for r in requirements.applicable
    ]
    included = _Included(control, requirements, [statement], statements)
    document = _passage(from_the_run("the document's whole text"), "")
    prompts: list[tuple[str, str, str, list[AnyMessage]]] = [
        (
            "gather",
            "Gather evidence",
            PROMPT_VERSION,
            _prompt(control, requirements, profile, NOTE, statements),
        ),
        (
            "overlay",
            "Country overlay",
            PROMPT_VERSION,
            [_overlay_prompt(overlay)] if overlay else [],
        ),
        ("judge", "Judge", PROMPT_VERSION, _judge_prompt(judged, profile)),
        (
            "remediation",
            "Remediation",
            PROMPT_VERSION,
            _remediation_prompt(control, not_evidenced, profile),
        ),
        (
            "profile_pass",
            "Profile pass",
            PROFILE_PASS_PROMPT_VERSION,
            _profile_pass_prompt(profile, [included], [document]),
        ),
        (
            "chat",
            "Chat",
            CHAT_PROMPT_VERSION,
            [chat_prompt(profile, PageContext("paper", control))],
        ),
    ]
    return [
        PromptPreview(
            name,
            title,
            version,
            sent=bool(messages),
            note=NOTES[name] if messages else NOT_SENT,
            messages=_messages(messages),
        )
        for name, title, version, messages in prompts
    ]
