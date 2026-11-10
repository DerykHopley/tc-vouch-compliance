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
    gather_prompt,
    judge_prompt,
    overlay_prompt,
    overlay_requirements_of,
    remediation_prompt,
    statement_passages,
)
from vouch.isa import IsaControl
from vouch.profile import ComplianceProfile
from vouch.profile_pass import PROFILE_PASS_PROMPT_VERSION, IncludedControl, profile_pass_prompt


def from_the_run(what: str) -> str:
    """A placeholder for what only a run knows."""
    return f"[from the run: {what}]"


NOTE = from_the_run("the lead's rejection note, on a run asked for again")
DRAFT = from_the_run("the gather step's draft answer")
REASON = from_the_run("the judge's reason")


@dataclass(frozen=True)
class PromptMessage:
    role: Literal["system", "human"]
    text: str


@dataclass(frozen=True)
class PromptPreview:
    """One prompt as the developer page shows it: `sent` is False for a prompt this control
    never gets (the overlay with no overlay requirement, remediation with nothing to fix),
    which then has no messages and says why in its note."""

    name: str
    title: str
    version: str
    sent: bool
    note: str
    messages: tuple[PromptMessage, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Row:
    """One prompt of the table: what it is, its messages built for the control (none: not
    sent), when it is sent and what a run changes, and why it isn't sent for this control."""

    name: str
    title: str
    version: str
    messages: list[AnyMessage]
    note: str
    not_sent: str = ""

    def preview(self) -> PromptPreview:
        return PromptPreview(
            self.name,
            self.title,
            self.version,
            sent=bool(self.messages),
            note=self.note if self.messages else self.not_sent,
            messages=tuple(
                PromptMessage("system" if isinstance(m, SystemMessage) else "human", str(m.content))
                for m in self.messages
            ),
        )


@dataclass(frozen=True)
class _PlaceholderStatement(LeadStatement):
    """A lead statement whose id, like its question and answer, only a run knows."""

    @property
    def doc_id(self) -> str:
        return from_the_run("the question's id, such as Q12")


def _statement(control: IsaControl) -> LeadStatement:
    """One lead statement on the control, its id, question and answer placeholders."""
    return _PlaceholderStatement(
        id=0,  # not shown: its doc_id is a placeholder
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
    to_fix = [
        RequirementVerdict(r.id, r.column, r.text, "not-evidenced", REASON)
        for r in requirements.applicable
    ]
    included = IncludedControl(control, requirements, [statement], statements)
    document = _passage(from_the_run("the document's whole text"), "")
    rows = [
        _Row(
            "gather",
            "Gather evidence",
            PROMPT_VERSION,
            gather_prompt(control, requirements, profile, NOTE, statements),
            "Sent first on every assessment. The rejection note appears only on a run asked for "
            "again, and the lead's statements, with the rule on citing them, only when there are "
            "some on the control.",
        ),
        _Row(
            "overlay",
            "Country overlay",
            PROMPT_VERSION,
            [overlay_prompt(overlay)] if overlay else [],
            "Sent after the first prompt when the control has overlay requirements, asking for "
            "evidence for each of them too.",
            "Not sent for this control: it has no overlay requirement.",
        ),
        _Row(
            "judge",
            "Judge",
            PROMPT_VERSION,
            judge_prompt(judged, profile),
            "Sent once gathering ends, with the evidence the run found. The rejection note "
            "appears only on a run asked for again, and the lead's statements, with the rule on "
            "citing them, only when there are some on the control.",
        ),
        _Row(
            "remediation",
            "Remediation",
            PROMPT_VERSION,
            remediation_prompt(control, to_fix, profile) if to_fix else [],
            "Sent after the judge when a requirement isn't fully evidenced. A run lists only the "
            "requirements not or partly evidenced, each with its verdict and the judge's reason.",
            "Not sent for this control: it has no requirement to fix.",
        ),
        _Row(
            "profile_pass",
            "Profile pass",
            PROFILE_PASS_PROMPT_VERSION,
            profile_pass_prompt(profile, [included], [document]),
            "Sent once per profile pass. A run lists every applicable control not yet accepted "
            "or in review, and every document of every allowed source, whole.",
        ),
        _Row(
            "chat",
            "Chat",
            CHAT_PROMPT_VERSION,
            [chat_prompt(profile, PageContext("paper", control))],
            "Sent with every chat message. The page named is the one the lead is on; here, the "
            "chosen control's working paper.",
        ),
    ]
    return [row.preview() for row in rows]
