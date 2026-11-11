"""The profile pass (GLOSSARY.md): every applicable control of a compliance profile not yet
accepted, assessed in one guarded model run, with all the profile's evidence in the prompt (#371).

Which controls: each control of the profile's ISA edition with an applicable requirement, unless
its newest assessment is accepted or its review is in progress (in_review, #437). The prompt holds
the rules, those controls' applicable requirements and every document of every evidence source
the profile allows, each passed as data (vouch.guards) with its kind and whether it is stale. A prompt and expected reply estimated over
the threshold (estimate_tokens, estimate_output_tokens, by the profile's answer language), or an
allowed source that can't be read, refuses the pass before anything is counted or called
(ProfilePassRefused): the per-control agent is the way on.

One structured-output call answers for every included control (ProfilePassReply). The agent's
guards then run on it, per control, in code: verdicts read as the judge step reads them, every
citation through the quote check, the staleness and documented-only rules, the output guard on
every answer, reason and remediation draft (the whole reply asked for again once), and the
remediation items drafted as the agent drafts them. Each control's result is written onto a new
thread of the assessment graph, up to its review interrupt, so it is read and reviewed like any
agent run's.

The lead's statements (#422, answers to open questions) are listed under their own control, as
data, and checked against that control's citations only: one cited for another control is dropped.
"""

import logging
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, Field

from vouch.added_documents import added_index
from vouch.citations import LeadStatement, QuoteChecker
from vouch.documents import escaped, facts_of
from vouch.dossier import applicable_controls
from vouch.front_matter import split_front_matter
from vouch.graph import (
    GUARD,
    MAX_DUE_DAYS,
    Assessment,
    AssessState,
    EvidencePassage,
    Judgement,
    RemediationItemReply,
    RemediationPlan,
    RequirementJudgement,
    Step,
    ask_until_clean,
    assessment_loader,
    build_graph,
    checked_requirements,
    format_requirements,
    guard_steps,
    in_review,
    judged_requirements,
    lead_rule,
    loaded_step,
    model_name_of,
    never_claim,
    new_thread_id,
    overlay_requirements_of,
    overlays_step,
    plural,
    quote_checker,
    remediation_drafts,
    statement_passages,
    statements_text,
    thread_config,
    to_remediate,
    verdict_changes,
)
from vouch.guards import DATA_RULE, as_data, flagged_steps
from vouch.isa import IsaControl
from vouch.language import language_name, lead_statement_label
from vouch.memory import Memory, newest_by_control
from vouch.profile import ComplianceProfile, ControlRequirements
from vouch.search import passages_of
from vouch.settings import PROFILE_PASS_TOKENS

log = logging.getLogger(__name__)

PROFILE_PASS_PROMPT_VERSION = "m7-profile-pass-2"  # the lead's statements (#422)
"""The version of the pass's prompt: change it whenever the prompt changes."""
INPUT_CHARS_PER_TOKEN: dict[str, float] = {"en": 2.5, "de": 1.9}
"""Characters a prompt token, by the profile's answer language (#432): rounded down from the
2.68 (Nordlicht, English) and 1.92 (Alpenwerk, German) measured on Sonnet 5.5 (#430), so the
estimate doesn't undercount. A language without its own rate takes German's (OTHER_LANGUAGE)."""
OUTPUT_TOKENS_PER_REQUIREMENT: dict[str, int] = {"en": 320, "de": 420}
"""Reply tokens an applicable requirement, by the profile's answer language, which the reply is
written in (#432): rounded up from the 311 (Nordlicht) and 415 (Alpenwerk) measured on #430."""
OTHER_LANGUAGE = "de"
"""Whose rates a language without its own takes: German's, the higher estimate of the two."""
PROFILE_PASS_FAILED = "The profile pass could not be completed. Please try again later."
"""The one sentence for a pass whose model run failed, or whose reply claimed an audit result
twice."""
NO_ANSWER = "No answer was drafted for this control in the profile pass."
"""A control's answer when the reply left the control out."""
PASS_NODE = "profile_pass"
"""The step that says an assessment came from a profile pass."""
FALLBACK = "per-control"
"""What a refused pass offers instead: the per-control agent."""


class ControlAssessmentReply(BaseModel):
    """One control assessed: a draft answer, a verdict per requirement, and a remediation item
    per requirement not or partly evidenced."""

    control_id: str = Field(description="The control's id, such as 1.1.1.")
    answer: str = Field(description="A short draft answer for an auditor.")
    requirements: list[RequirementJudgement] = Field(default_factory=list)
    remediation: list[RemediationItemReply] = Field(default_factory=list)


class ProfilePassReply(BaseModel):
    """Every control listed, each assessed from the evidence alone."""

    controls: list[ControlAssessmentReply] = Field(default_factory=list)


class ProfilePassRefused(Exception):
    """The pass won't run: nothing is counted and no model is called. The message says why, in
    one sentence; the per-control agent is the way on (FALLBACK)."""


SkipReason = Literal["accepted", "in-review"]


@dataclass(frozen=True)
class SkippedControl:
    """A control the pass left out, and why: its newest assessment is accepted, or its review is
    in progress (in_review, #437)."""

    control_id: str
    reason: SkipReason


@dataclass(frozen=True)
class ProfilePassResult:
    """The new assessments, each waiting for review, and the controls skipped, in profile
    order."""

    assessments: list[Assessment]
    skipped: list[SkippedControl]


def _skip_reason(newest: Assessment | None) -> SkipReason | None:
    if newest is None:
        return None
    if newest.status == "accepted":
        return "accepted"
    return "in-review" if in_review(newest) else None


@dataclass(frozen=True)
class IncludedControl:
    """A control the pass assesses, as its prompt lists it."""

    control: IsaControl
    requirements: ControlRequirements
    statements: list[LeadStatement] = field(default_factory=list)  # the lead's on it (#422)
    passages: list[EvidencePassage] = field(default_factory=list)  # those, as the prompt lists them


def _documents(profile: ComplianceProfile, today: date) -> list[EvidencePassage]:
    """Every document of every evidence source the profile allows (only the chosen ones, where
    it chose: EvidenceSource.documents), then the documents added to it (#584: the newest
    version of each not withdrawn), whole, with its source, kind and staleness on `today`.
    ProfilePassRefused for a source whose folder can't be read (missing, unreadable, or
    `escaped` since the profile loaded)."""

    def refused(why: str) -> ProfilePassRefused:
        return ProfilePassRefused(
            f"The profile pass can't run: {why}. Assess the controls one at a time instead."
        )

    found: list[EvidencePassage] = []
    for source, allowed in profile.evidence_sources.items():
        folder = allowed.folder
        if escaped(folder):
            raise refused(f"the {source} folder now points outside its allowed folder")
        try:
            for path in allowed.usable_documents():  # a link out of the folder left out
                data, body = split_front_matter(path.read_text(encoding="utf-8"))
                facts = facts_of(data, source, path.stem, today)
                found.append(
                    EvidencePassage(
                        doc_id=path.stem,
                        heading="",
                        text=body.strip(),
                        source=source,
                        kind=facts.kind,
                        stale=facts.stale_because is not None,
                    )
                )
        except (OSError, UnicodeDecodeError):
            raise refused(f"the {source} folder can't be read") from None
        for added in added_index().searchable(profile.id, source):  # #584
            facts = added.facts(today)
            found.append(
                EvidencePassage(
                    doc_id=added.doc_id,
                    heading="",
                    text=added.body.strip(),
                    source=source,
                    kind=facts.kind,
                    stale=facts.stale_because is not None,
                )
            )
    return found


def profile_pass_prompt(
    profile: ComplianceProfile, included: list[IncludedControl], documents: list[EvidencePassage]
) -> list[AnyMessage]:
    """The profile pass's one prompt: every included control and every document, whole."""
    language = language_name(profile.answer_language)
    controls = "\n\n".join(
        f"Control {i.control.id}: {i.control.question}\n"
        f"Requirements:\n{format_requirements(i.requirements.applicable)}"
        f"{statements_text(i.passages)}"
        for i in included
    )
    evidence = as_data(documents) if documents else "(none)"
    rule = lead_rule(any(i.passages for i in included))
    return [
        SystemMessage(
            f"You assess every {included[0].control.source} control listed for {profile.company} "
            "against the company's own evidence, in one reply, one entry per control by its id. "
            "For each control: a short draft answer for an auditor, written in "
            f"{language}, saying what the evidence shows. Then judge each of its requirements "
            "from the evidence alone: evidenced (the evidence fully shows it), partly-evidenced "
            "(it shows some of it) or not-evidenced (it shows none of it), with one sentence "
            "saying why. For each verdict other than not-evidenced, give the citations it relies "
            "on: the doc_id and a passage copied word for word from that document, at least four "
            "words from one paragraph, list item or table row. A quote that isn't in the "
            "document is dropped, and a verdict with no citation left is not-evidenced. Each "
            "document gives its source, its kind (implemented: shows what was actually done; "
            "documented: says what should be done) and whether it is stale (past its review "
            "date). For a requirement that asks for something to be done, set about_doing and "
            "say so in the reason: it is evidenced only with implemented evidence. Stale "
            "evidence shows a requirement partly at most. Then draft one remediation item for "
            "each requirement not or partly evidenced: what is missing, the role that should own "
            f"the fix (a role, never a person's name), and how many days from today (1 to "
            f"{MAX_DUE_DAYS}) until it is due. Write every answer, reason and item in {language}; "
            "copy each quote unchanged, in its document's language, never translated. "
            f"{never_claim(profile)} {DATA_RULE}{rule}"
        ),
        HumanMessage(f"Controls:\n\n{controls}\n\nEvidence:\n{evidence}"),
    ]


def _rate[T](rates: Mapping[str, T], answer_language: str) -> T:
    return rates.get(answer_language, rates[OTHER_LANGUAGE])


def estimate_tokens(prompt: list[AnyMessage], answer_language: str) -> int:
    """The prompt's size in tokens: its characters at the answer language's
    INPUT_CHARS_PER_TOKEN, rounded up."""
    characters = sum(len(str(m.content)) for m in prompt)
    return math.ceil(characters / _rate(INPUT_CHARS_PER_TOKEN, answer_language))


def estimate_output_tokens(requirements: int, answer_language: str) -> int:
    """The reply's expected size in tokens, for `requirements` applicable requirements (overlay
    ones included): the answer language's OUTPUT_TOKENS_PER_REQUIREMENT each."""
    return requirements * _rate(OUTPUT_TOKENS_PER_REQUIREMENT, answer_language)


@dataclass(frozen=True)
class _Assessed:
    """One control's part of the reply, after the guards: the values its thread is written
    with, and the texts the output guard reads."""

    values: dict[str, Any]
    texts: list[str]


def _assessed(
    included: IncludedControl,
    reply: ControlAssessmentReply | None,
    checker: QuoteChecker,
    profile: ComplianceProfile,
    today: date,
) -> _Assessed:
    """The agent's guards on one control's reply (None: the reply left it out): its verdicts
    read as the judge step reads them, the quote check and the judge's rules, then one
    remediation item per requirement not or partly evidenced."""
    judgement = Judgement(requirements=reply.requirements) if reply else None
    judged = judged_requirements(included.requirements, judgement)
    requirements, dropped = checked_requirements(
        judged.results, judged.claims, checker, judged.about_doing, profile.answer_language
    )
    to_fix = to_remediate(requirements, [])
    plan = RemediationPlan(items=reply.remediation) if reply else None
    drafts, not_drafted = remediation_drafts(to_fix, plan, today)
    answer = reply.answer if reply else NO_ANSWER
    partly = {r.id for r in to_fix if r.verdict == "partly-evidenced"}
    updates: list[tuple[str, dict[str, Any]]] = [
        ("judge", {"requirements": judged.results, "not_judged": judged.not_judged}),
        (
            "check_quotes",
            {
                "requirements": requirements,
                "dropped_citations": dropped,
                "verdict_changes": verdict_changes(judged.results, requirements),
            },
        ),
        (
            "remediate",
            {
                "remediation": drafts,
                "not_drafted": not_drafted,
                "partly_drafted": len(partly - set(not_drafted)),
                "pending_ids": [],
            },
        ),
    ]
    flagged = [Step(GUARD, {"text": t}) for t in flagged_steps(included.passages)]
    steps = list(flagged)
    for node, update in updates:
        steps += [Step(node, update, profile), *guard_steps(update)]
    values = {
        "control": included.control,
        "control_requirements": included.requirements,
        "overlay_requirements": overlay_requirements_of(included.requirements),
        "answer": answer,
        "requirements": requirements,
        "not_judged": judged.not_judged,
        "about_doing": judged.about_doing,
        "dropped_citations": dropped,
        "remediation": drafts,
        "not_drafted": not_drafted,
        "steps": [step.to_dict() for step in steps],
    }
    texts = [
        answer,
        *(r.reason for r in judged.results if r.reason),
        *(f"{d.missing} {d.owner_role}" for d in drafts),
    ]
    return _Assessed(values, texts)


def _pass_step(controls: int, documents: int, rejected: str | None) -> dict[str, str]:
    text = (
        f"Assessed in a profile pass: one model run over {plural(controls, 'control')}, with "
        f"all of the profile's evidence ({plural(documents, 'document')}) in the prompt."
    )
    if rejected:
        text += (
            f" A reply claimed an audit result (“{rejected}”), so the whole reply was asked for "
            "again."
        )
    return {"node": PASS_NODE, "text": text}


def _write(
    assessed: dict[str, _Assessed],
    head: list[dict[str, str]],
    shared: AssessState,
    profile: ComplianceProfile,
    memory: Memory,
    saver: BaseCheckpointSaver,
) -> list[str]:
    """Each control's assessment on a new thread under the user's scope, remembered in the
    index, written as if the graph had run up to just before its review, then run to the review
    interrupt. Whatever fails, every entry made is forgotten again. The new thread ids, in
    order."""
    graph = build_graph(None, checkpointer=saver)
    made: list[str] = []
    try:
        for control_id, part in assessed.items():
            thread_id = new_thread_id()
            memory.remember_assessment(thread_id, control_id)
            made.append(thread_id)
            config = thread_config(thread_id, memory.scope)
            control = part.values["control"]
            loaded = loaded_step(control, profile).to_dict()
            # The agent's own overlay step; no overlay_branch step, as the pass gathers nothing.
            overlays = overlays_step(part.values["overlay_requirements"]).to_dict()
            values = {
                **shared,
                **part.values,
                "steps": [loaded, overlays, *head, *part.values["steps"]],
            }
            graph.update_state(config, values, as_node="remediate")
            graph.invoke(None, config)  # stops at the review interrupt
    except Exception:
        for thread_id in made:
            try:
                memory.forget_assessment(thread_id)
            except Exception:
                log.exception("a profile pass's assessment that failed stays in the index")
        raise
    return made


def run_profile_pass(
    profile: ComplianceProfile,
    memory: Memory,
    saver: BaseCheckpointSaver,
    model: BaseChatModel,
    *,
    today: date | None = None,
    max_tokens: int = PROFILE_PASS_TOKENS,
    count: Callable[[], None] = lambda: None,
    statements: Iterable[LeadStatement] = (),
) -> ProfilePassResult:
    """Assess every applicable control of the profile not yet accepted, and not in review, in one
    model run (see the module's doc).

    With every applicable control accepted or in review, nothing is counted or called.
    Otherwise the sources are read and the prompt and reply estimated: an unreadable source, or a
    prompt and expected reply over `max_tokens`, raises ProfilePassRefused. Then `count` is called (it raises when the pass can't be counted: the
    daily limit), then the model, once (twice when the output guard asks again). A second audit
    claim raises OutputCheckError; a failed model call raises its error. Either way, nothing is
    kept in the index. `today` (default: now) is what staleness is judged on and due dates count
    from. `statements` are the lead's (#422): each is listed under its own control, and may be
    cited for that control only."""
    today = today or date.today()
    statements = list(statements)
    newest = newest_by_control(memory, saver)
    included: list[IncludedControl] = []
    skipped: list[SkippedControl] = []
    for control in applicable_controls(profile, profile.isa_edition().controls):
        requirements = profile.requirements_for(control)
        reason = _skip_reason(newest.get(control.id))
        if reason is not None:
            skipped.append(SkippedControl(control.id, reason))
        else:
            mine = [s for s in statements if s.control_id == control.id]
            passages = statement_passages(mine, profile.answer_language)
            included.append(IncludedControl(control, requirements, mine, passages))
    if not included:
        return ProfilePassResult([], skipped)
    documents = _documents(profile, today)
    prompt = profile_pass_prompt(profile, included, documents)
    tokens_in = estimate_tokens(prompt, profile.answer_language)
    tokens_out = estimate_output_tokens(
        sum(len(i.requirements.applicable) for i in included), profile.answer_language
    )
    if tokens_in + tokens_out > max_tokens:
        raise ProfilePassRefused(
            f"The profile pass can't run: its prompt and reply are about "
            f"{tokens_in + tokens_out:,} tokens ({tokens_in:,} in, {tokens_out:,} out), over the "
            f"threshold of {max_tokens:,}. Assess the controls one at a time instead."
        )
    count()
    shared: AssessState = {
        "profile_id": profile.id,
        "model": model_name_of(model),
        "prompt_version": PROFILE_PASS_PROMPT_VERSION,
        "answer_language": profile.answer_language,
        "evidence": [],
        "warnings": [],
        "pending": {},
        "pending_ids": [],
        "searches_run": 0,
        "searches_refused": 0,
        "usage": [],
        "today": today.isoformat(),
        "note": "",
        "document_folders": {n: str(s.folder) for n, s in profile.evidence_sources.items()},
        "document_choices": profile.document_choices(),
        "claims": {},
    }
    checker = quote_checker(shared, label=lead_statement_label(profile.answer_language))
    by_id = {i.control.id: i for i in included}
    paragraphs = [p["text"] for d in documents for p in passages_of(d["doc_id"], d["text"])]
    paragraphs += [p["text"] for i in included for p in i.passages]

    def read(reply: ProfilePassReply | None) -> tuple[dict[str, _Assessed], list[str]]:
        given: dict[str, ControlAssessmentReply] = {}
        for item in reply.controls if reply else []:
            if item.control_id in by_id:  # a control not included (unknown, skipped) is ignored
                given.setdefault(item.control_id, item)
        assessed = {
            cid: _assessed(i, given.get(cid), checker.for_statements(i.statements), profile, today)
            for cid, i in by_id.items()
        }
        return assessed, [text for part in assessed.values() for text in part.texts]

    assessed, usage, rejected = ask_until_clean(
        model.with_structured_output(ProfilePassReply, include_raw=True),
        prompt,
        read,
        what="the profile pass's reply",
        refusal=lambda word: (
            f"Reply refused: it says “{word}”, an audit result Vouch never claims. Assess "
            "again, saying only what the evidence shows."
        ),
        passages=paragraphs,
    )
    head = [
        _pass_step(len(included), len(documents), rejected),
        *(Step(GUARD, {"text": text}).to_dict() for text in flagged_steps(documents)),
    ]
    log.info("a profile pass of %d controls used %s", len(included), usage)
    thread_ids = _write(assessed, head, shared, profile, memory, saver)
    load = assessment_loader(saver, scope=memory.scope)
    loaded = [load(thread_id) for thread_id in thread_ids]
    return ProfilePassResult([a for a in loaded if a is not None], skipped)
