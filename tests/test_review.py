"""Remediation drafts, the review's two interrupts (Confirm findings, then Accept, #372), and
resuming with the compliance lead's findings and decision."""

import threading
from datetime import date

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from vouch import ScriptedChatModel, assess, audit, load_named_profile
from vouch.graph import (
    NO_OWNER,
    NOT_DRAFTED,
    ConfirmFindings,
    Decision,
    InvalidReviewError,
    NotWaitingForReviewError,
    OutputCheckError,
    RemediationPlan,
    ReviewDecision,
    UnknownAssessmentError,
    WrongStepError,
    assess_steps,
    build_graph,
    confirm_findings,
    load_assessment,
    memory_checkpointer,
    resume,
)
from vouch.isa import load_isa_control

NORDLICHT = load_named_profile("nordlicht")
TODAY = date(2026, 11, 4)


def drafted(*ids: str, days: int = 30, missing: str = "A tested backup concept.") -> list[dict]:
    return [
        {
            "requirement_id": r,
            "missing": missing,
            "owner_role": "IT operations lead",
            "due_in_days": days,
        }
        for r in ids
    ]


def script_5_2_9(remediate: list[dict] | list[list[dict]] | None = None) -> dict:
    """5.2.9 (backup), where Nordlicht has nothing: all three requirements not evidenced."""
    judge = [
        {"requirement_id": r, "verdict": "not-evidenced", "reason": "No document covers it."}
        for r in ("M1", "M2", "S1")
    ]
    if remediate is None:
        remediate = drafted("M1", "M2", "S1")
    if remediate and isinstance(remediate[0], list):
        plans: dict | list[dict] = [{"items": r} for r in remediate]
    else:
        plans = {"items": remediate}
    return {
        "gather": [
            {"tool": "search_policies", "args": {"query": "backup recovery"}},
            {"answer": "No backup or recovery concept was found."},
        ],
        "judge": {"requirements": judge},
        "remediate": plans,
    }


# A quote that is in Nordlicht's authentication standard, so an evidenced verdict keeps it.
TRUE_QUOTE = {
    "doc_id": "authentication-standard",
    "quote": "Passwords have at least 12 characters.",
}


def mixed_4_1_2() -> dict:
    """4.1.2 with M1 evidenced, M2 not evidenced: the roll-up is partly-evidenced."""
    verdicts = {
        "M1": "evidenced",
        "M2": "not-evidenced",
        "S1": "evidenced",
        "S2": "not-evidenced",
        "S3": "evidenced",
    }
    return {
        "gather": [
            {"tool": "search_policies", "args": {"query": "authentication"}},
            {"answer": "Sign-in methods are set per system."},
        ],
        "judge": {
            "requirements": [
                {
                    "requirement_id": r,
                    "verdict": v,
                    "reason": "From the standard.",
                    "citations": [TRUE_QUOTE] if v == "evidenced" else [],
                }
                for r, v in verdicts.items()
            ]
        },
        "remediate": {"items": drafted("M2", "S2")},
    }


def proposed(control_id: str, script: dict, checkpointer=None, **kwargs):
    model = ScriptedChatModel(script=script)
    saver = checkpointer or memory_checkpointer()
    return assess(control_id, NORDLICHT, model, saver, today=TODAY, **kwargs), model, saver


def test_5_2_9_gets_one_remediation_draft_per_not_evidenced_requirement():
    result, _, _ = proposed("5.2.9", script_5_2_9())

    assert [d.requirement_id for d in result.remediation] == ["M1", "M2", "S1"]
    first = result.remediation[0]
    assert first.missing == "A tested backup concept."
    assert first.owner_role == "IT operations lead"
    assert first.due_date == "2026-12-04"  # 30 days after TODAY


def test_a_skipped_draft_is_listed_not_drafted_and_drafts_for_other_requirements_ignored():
    result, _, _ = proposed("5.2.9", script_5_2_9(drafted("M2", "M7")))

    assert [(d.requirement_id, d.missing) for d in result.remediation] == [
        ("M1", "not drafted"),
        ("M2", "A tested backup concept."),
        ("S1", "not drafted"),
    ]
    assert result.remediation[0].due_date == "2027-02-02"  # the default 90 days
    remediate = next(s for s in result.steps if s["node"] == "remediate")
    assert remediate["text"] == "Drafted 1 remediation item. Not drafted: M1, S1."


def test_a_model_that_drafts_nothing_says_so_and_lists_the_not_drafted_ones():
    result, _, _ = proposed("5.2.9", script_5_2_9([]))

    assert [d.missing for d in result.remediation] == [NOT_DRAFTED] * 3
    remediate = next(s for s in result.steps if s["node"] == "remediate")
    assert remediate["text"] == (
        "No remediation item drafted by the model. Not drafted: M1, M2, S1."
    )


def test_some_drafts_are_counted_alone():
    result, _, _ = proposed("5.2.9", script_5_2_9(drafted("M1", "S1")))

    remediate = next(s for s in result.steps if s["node"] == "remediate")
    assert remediate["text"] == "Drafted 2 remediation items. Not drafted: M2."


def test_nothing_not_evidenced_means_no_remediation_call():
    script = mixed_4_1_2()
    for item in script["judge"]["requirements"]:
        item.update(verdict="evidenced", citations=[TRUE_QUOTE])

    result, model, _ = proposed("4.1.2", script)

    assert result.remediation == []
    assert RemediationPlan not in model.turns


# Partly evidenced requirements get remediation too (#212, the owner's decision 2026-11-06)

STALE_QUOTE = {
    "doc_id": "encryption-standard",  # approved 2022, reviewed yearly: stale
    "quote": "Every company laptop uses full-disk encryption with AES-256.",
}


def partly_5_1_1(remediate: list[dict] | None = None) -> dict:
    """5.1.1 as in the owner's live test: M1 (must) and S1 (should) partly evidenced, so the
    control rolls up to not evidenced."""
    return {
        "gather": [
            {"tool": "search_policies", "args": {"query": "encryption"}},
            {"answer": "Encryption is defined, but its standard is past review."},
        ],
        "judge": {
            "requirements": [
                {
                    "requirement_id": r,
                    "verdict": "partly-evidenced",
                    "reason": "The standard covers part of it.",
                    "citations": [STALE_QUOTE],
                }
                for r in ("M1", "S1")
            ]
        },
        "remediate": {"items": drafted("M1", "S1") if remediate is None else remediate},
    }


def test_partly_evidenced_requirements_get_one_draft_each():
    result, _, _ = proposed("5.1.1", partly_5_1_1())

    assert result.verdict == "not-evidenced"  # the roll-up rule is unchanged
    assert [r.verdict for r in result.requirements] == ["partly-evidenced"] * 2
    assert [d.requirement_id for d in result.remediation] == ["M1", "S1"]
    remediate = next(s for s in result.steps if s["node"] == "remediate")
    assert remediate["text"] == "Drafted 2 remediation items (2 partly evidenced)."


def test_the_remediation_prompt_names_each_requirements_verdict_and_what_partly_needs():
    _, model, _ = proposed("5.1.1", partly_5_1_1())

    [prompt] = [
        text
        for call in model.prompts
        if "remediation item" in (text := "\n".join(str(m.content) for m in call))
    ]
    assert "- M1 (must, partly evidenced):" in prompt
    assert "For a partly evidenced requirement, say what is still missing" in prompt


def test_only_partly_and_not_evidenced_requirements_get_drafts():
    script = mixed_4_1_2()  # M1, S1, S3 evidenced; M2, S2 not evidenced
    verdicts = {"M1": "evidenced", "M2": "not-evidenced", "S1": "partly-evidenced"}
    for item in script["judge"]["requirements"]:
        if item["requirement_id"] in verdicts:
            item["verdict"] = verdicts[item["requirement_id"]]
    script["remediate"] = {"items": drafted("M2", "S1", "S2")}

    result, _, _ = proposed("4.1.2", script)

    assert [d.requirement_id for d in result.remediation] == ["M2", "S1", "S2"]
    remediate = next(s for s in result.steps if s["node"] == "remediate")
    assert remediate["text"] == "Drafted 3 remediation items (1 partly evidenced)."


def test_a_draft_claiming_an_audit_result_is_asked_for_again_once():
    first = drafted("M1", "M2", "S1", missing="Nothing; backups are compliant.")
    result, model, _ = proposed("5.2.9", script_5_2_9([first, drafted("M1", "M2", "S1")]))

    assert model.turns[RemediationPlan] == 2
    assert result.remediation[0].missing == "A tested backup concept."
    assert "asked for again" in next(s for s in result.steps if s["node"] == "remediate")["text"]


def test_a_draft_claiming_an_audit_result_twice_fails():
    bad = drafted("M1", "M2", "S1", missing="Backups passed.")
    with pytest.raises(OutputCheckError):
        proposed("5.2.9", script_5_2_9([bad, bad]))


def test_the_run_stops_at_the_review_interrupt_and_returns_the_proposed_assessment():
    result, _, _ = proposed("4.1.2", mixed_4_1_2())

    assert result.status == "waiting-for-review"
    assert result.review is None
    assert result.thread_id
    assert result.verdict == "partly-evidenced"
    assert [s["node"] for s in result.steps][-3:] == ["check_quotes", "remediate", "review"]
    assert result.steps[-1]["text"] == "Waiting for the compliance lead's review."
    assert [d.requirement_id for d in result.remediation] == ["M2", "S2"]


def confirmed(result, saver, *overrides: dict):
    """The assessment with its findings confirmed, with `overrides`."""
    return confirm_findings(result.thread_id, ConfirmFindings(overrides=list(overrides)), saver)


def test_confirm_findings_waits_for_the_decision_then_accept_records_it():
    result, _, saver = proposed("4.1.2", mixed_4_1_2())

    waiting = confirmed(result, saver)
    reviewed = resume(result.thread_id, ReviewDecision(decision="accept", note="Fine."), saver)

    assert (waiting.status, waiting.confirmed, waiting.review) == ("waiting-for-review", [], None)
    assert waiting.steps[-2:] == [
        {"node": "review", "text": "The compliance lead confirmed the findings."},
        {
            "node": "accept",
            "text": "Findings confirmed. Waiting for the compliance lead's plan and decision.",
        },
    ]
    assert reviewed.status == "accepted"
    assert reviewed.review.decision == "accept"
    assert reviewed.review.note == "Fine."
    assert reviewed.review.overrides == []
    assert reviewed.review.three_step is True
    assert reviewed.verdict == "partly-evidenced"
    assert reviewed.thread_id == result.thread_id
    assert reviewed.remediation == result.remediation
    assert reviewed.steps[:-2] == result.steps[:-1]  # every step of the run is kept
    assert reviewed.steps[-1] == {
        "node": "accept",
        "text": "The compliance lead accepted the assessment.",
    }


def test_accept_before_the_findings_are_confirmed_is_refused_and_resumes_nothing():
    result, _, saver = proposed("4.1.2", mixed_4_1_2())

    with pytest.raises(WrongStepError, match="Confirm the findings first"):
        resume(result.thread_id, ReviewDecision(decision="accept"), saver)
    with pytest.raises(WrongStepError, match="Confirm the findings first"):
        build_graph(None, checkpointer=saver).invoke(
            Command(resume={"decision": "accept"}),
            {"configurable": {"thread_id": result.thread_id}},
        )
    assert load_assessment(result.thread_id, saver).confirmed is None


def test_a_second_confirm_findings_is_refused():
    result, _, saver = proposed("4.1.2", mixed_4_1_2())
    confirmed(result, saver)

    with pytest.raises(WrongStepError, match="confirmed already"):
        confirmed(result, saver, override("M2", "evidenced"))
    assert load_assessment(result.thread_id, saver).confirmed == []


@pytest.mark.parametrize("confirm_first", [False, True], ids=["at-confirm", "at-accept"])
def test_reject_resumes_and_records_the_note_at_either_interrupt(confirm_first):
    result, _, saver = proposed("4.1.2", mixed_4_1_2())
    if confirm_first:
        confirmed(result, saver, override("M2", "evidenced"))

    reviewed = resume(
        result.thread_id,
        ReviewDecision(decision="reject", note="The 2025 MFA rollout record was missed."),
        saver,
    )

    assert reviewed.status == "rejected"
    assert reviewed.review.note == "The 2025 MFA rollout record was missed."
    assert reviewed.review.three_step is True
    assert [o.requirement_id for o in reviewed.review.overrides] == (
        ["M2"] if confirm_first else []
    )
    assert reviewed.steps[-1]["text"] == "The compliance lead rejected the assessment."


def test_an_override_changes_the_verdict_keeps_the_agents_and_recalculates_the_roll_up():
    result, _, saver = proposed("4.1.2", mixed_4_1_2())
    waiting = confirmed(
        result,
        saver,
        {
            "requirement_id": "M2",
            "verdict": "evidenced",
            "reason": "The MFA rollout record shows it.",
        },
    )

    reviewed = resume(result.thread_id, ReviewDecision(decision="accept"), saver)

    m2 = next(r for r in reviewed.requirements if r.id == "M2")
    assert (m2.verdict, m2.reason) == ("evidenced", "The MFA rollout record shows it.")
    assert result.verdict == "partly-evidenced"
    assert waiting.verdict == reviewed.verdict == "evidenced"
    (override_kept,) = reviewed.review.overrides
    assert waiting.confirmed == reviewed.review.overrides
    assert (override_kept.agent_verdict, override_kept.agent_reason) == (
        "not-evidenced",
        "From the standard.",
    )
    assert waiting.steps[-2]["text"] == "The compliance lead confirmed the findings, overriding M2."


def test_an_override_on_a_should_requirement_changes_the_should_score_not_the_verdict():
    result, _, saver = proposed("4.1.2", mixed_4_1_2())

    waiting = confirmed(
        result,
        saver,
        {"requirement_id": "S2", "verdict": "evidenced", "reason": "Shown in the export."},
    )

    assert (result.should_score.evidenced, waiting.should_score.evidenced) == (2, 3)
    assert waiting.verdict == "partly-evidenced"


def test_a_re_run_after_reject_receives_the_note():
    note = "Look at the MFA rollout record in records/."
    result, model, saver = proposed("4.1.2", mixed_4_1_2())
    resume(result.thread_id, ReviewDecision(decision="reject", note=note), saver)

    again, model_again, _ = proposed("4.1.2", mixed_4_1_2(), saver, note=note)

    first_gather, judge_prompt = model_again.prompts[0], model_again.prompts[2]
    assert note in first_gather[-1].content
    assert note in judge_prompt[-1].content
    assert not any(note in str(m.content) for p in model.prompts for m in p)  # not the first run
    assert again.thread_id != result.thread_id


def thread_values(saver, thread_id) -> dict:
    return (
        build_graph(None, checkpointer=saver)
        .get_state({"configurable": {"thread_id": thread_id}})
        .values
    )


def test_resuming_runs_nothing_before_the_interrupt_again_and_a_second_resume_is_refused():
    result, model, saver = proposed("5.2.9", script_5_2_9())
    calls_before = len(model.prompts)  # gather twice, judge, remediate
    assert calls_before == 4
    assert "review" not in thread_values(saver, result.thread_id)  # nothing written yet

    confirmed(result, saver)
    reviewed = resume(result.thread_id, ReviewDecision(decision="accept"), saver)

    assert len(model.prompts) == calls_before  # no model call on resume: no node re-drafted
    assert reviewed.remediation == result.remediation
    with pytest.raises(NotWaitingForReviewError):
        resume(result.thread_id, ReviewDecision(decision="reject"), saver)
    with pytest.raises(NotWaitingForReviewError):
        confirmed(result, saver)
    values = thread_values(saver, result.thread_id)
    assert values["review"].decision == "accept"  # the refused second resume wrote nothing
    nodes = [s["node"] for s in values["steps"]]
    assert (nodes.count("review"), nodes.count("accept"), nodes.count("remediate")) == (1, 1, 1)


def test_resuming_an_unknown_thread_is_refused():
    with pytest.raises(UnknownAssessmentError):
        resume(
            "no-such-thread",
            ReviewDecision(decision="accept"),
            memory_checkpointer(),
        )


def test_a_run_that_failed_before_the_review_is_unknown_to_open_and_to_review():
    """#38: it has state in the checkpointer, but no assessment: 404 either way, never 409."""

    class Unreachable(ScriptedChatModel):
        def _generate(self, *args, **kwargs):
            raise RuntimeError("the model is unreachable")

    saver = memory_checkpointer()
    model = Unreachable(script=script_5_2_9())
    with pytest.raises(RuntimeError, match="unreachable"):
        list(assess_steps(load_isa_control("5.2.9"), NORDLICHT, model, saver, thread_id="failed"))
    assert saver.get_tuple({"configurable": {"thread_id": "failed"}}) is not None

    with pytest.raises(UnknownAssessmentError):
        load_assessment("failed", saver)
    with pytest.raises(UnknownAssessmentError):
        resume("failed", ReviewDecision(decision="accept"), saver)
    with pytest.raises(UnknownAssessmentError):
        confirm_findings("failed", ConfirmFindings(), saver)


@pytest.mark.parametrize(
    "overrides",
    [
        [{"requirement_id": "M9", "verdict": "evidenced", "reason": "Shown."}],
        [{"requirement_id": "H1", "verdict": "evidenced", "reason": "Not applied at normal."}],
        [
            {"requirement_id": "M2", "verdict": "evidenced", "reason": "Shown."},
            {"requirement_id": "M2", "verdict": "not-evidenced", "reason": "Not shown."},
        ],
    ],
    ids=["unknown", "not-in-the-profile", "named-twice"],
)
def test_an_override_naming_a_requirement_the_assessment_did_not_judge_is_refused(overrides):
    result, _, saver = proposed("4.1.2", mixed_4_1_2())

    with pytest.raises(InvalidReviewError):
        confirm_findings(result.thread_id, ConfirmFindings(overrides=overrides), saver)
    assert confirmed(result, saver).confirmed == []  # the refused findings resumed nothing


def test_an_override_on_a_not_applicable_requirement_is_refused():
    script = mixed_4_1_2()
    script["judge"]["requirements"] = [
        {"requirement_id": r, "verdict": "evidenced", "reason": "Shown."}
        for r in ("M1", "M2", "M3", "S1", "S2")
    ]
    result, _, saver = proposed("5.1.2", script)
    assert any(r.id == "S3" and r.verdict == "not-applicable" for r in result.requirements)

    with pytest.raises(InvalidReviewError):
        confirmed(
            result,
            saver,
            {"requirement_id": "S3", "verdict": "evidenced", "reason": "We do run it."},
        )


@pytest.mark.parametrize(
    "body",
    [
        {"overrides": [{"requirement_id": "M1", "verdict": "evidenced"}]},
        {"overrides": [{"requirement_id": "M1", "verdict": "evidenced", "reason": "  "}]},
        {"overrides": [{"requirement_id": "M1", "verdict": "compliant", "reason": "Shown."}]},
        {"overrides": [{"requirement_id": "M1", "verdict": "not-applicable", "reason": "Shown."}]},
    ],
    ids=["no-reason", "blank-reason", "invalid-verdict", "not-applicable"],
)
def test_malformed_findings_do_not_validate(body):
    with pytest.raises(ValidationError):
        ConfirmFindings.model_validate(body)


@pytest.mark.parametrize(
    "body",
    [{"decision": "maybe"}, {"decision": "accept", "overrides": []}],
    ids=["bad-decision", "with-overrides"],
)
def test_a_malformed_review_decision_does_not_validate(body):
    with pytest.raises(ValidationError, match="bad-decision|Input should be|Confirm findings"):
        ReviewDecision.model_validate(body)


def test_the_reviewed_assessment_names_the_model_that_drafted_it_without_one_to_resume():
    result, _, saver = proposed("4.1.2", mixed_4_1_2())

    confirmed(result, saver)
    reviewed = resume(result.thread_id, ReviewDecision(decision="accept"), saver)

    assert (reviewed.model, reviewed.prompt_version) == ("scripted-fake", result.prompt_version)


def test_each_assessment_gets_a_fresh_thread_even_on_a_shared_checkpointer():
    first, _, saver = proposed("4.1.2", mixed_4_1_2())
    second, _, _ = proposed("4.1.2", mixed_4_1_2(), saver)

    assert second.thread_id != first.thread_id
    assert second.steps == first.steps  # nothing piled onto an earlier run's state
    assert second.token_usage == first.token_usage
    with pytest.raises(TypeError):  # a caller can't pick a thread that may hold a run already
        proposed("4.1.2", mixed_4_1_2(), saver, thread_id=first.thread_id)


def override(requirement_id: str, verdict: str) -> dict:
    return {"requirement_id": requirement_id, "verdict": verdict, "reason": "The lead's call."}


def test_an_override_to_evidenced_drops_its_draft_and_to_partly_keeps_it():
    # Partly evidenced still needs a fix (#212): only an override to evidenced drops the draft.
    result, _, saver = proposed("4.1.2", mixed_4_1_2())

    reviewed = confirmed(
        result, saver, override("M2", "evidenced"), override("S2", "partly-evidenced")
    )

    assert [d.requirement_id for d in result.remediation] == ["M2", "S2"]
    assert [d.requirement_id for d in reviewed.remediation] == ["S2"]
    assert reviewed.remediation[0] == result.remediation[1]


def test_an_override_to_not_evidenced_adds_a_not_drafted_item_for_the_lead():
    result, _, saver = proposed("4.1.2", mixed_4_1_2())

    reviewed = confirmed(result, saver, override("M1", "not-evidenced"))

    assert [d.requirement_id for d in reviewed.remediation] == ["M1", "M2", "S2"]
    m1 = reviewed.remediation[0]
    assert (m1.missing, m1.owner_role, m1.due_date) == (NOT_DRAFTED, NO_OWNER, "2027-02-02")
    assert reviewed.remediation[1:] == result.remediation


def test_the_review_step_refuses_an_override_confirm_findings_did_not_check():
    result, _, saver = proposed("4.1.2", mixed_4_1_2())
    twice = {"overrides": [override("M2", "evidenced"), override("M2", "not-evidenced")]}

    with pytest.raises(InvalidReviewError):
        build_graph(None, checkpointer=saver).invoke(
            Command(resume=twice), {"configurable": {"thread_id": result.thread_id}}
        )


class BarrierSaver(InMemorySaver):
    """Holds each review's first read of the thread until the other review reads it too (or
    half a second passes), so two reviews race as they would on two server threads."""

    def __init__(self, saver: InMemorySaver):
        super().__init__(serde=saver.serde)
        self.barrier = threading.Barrier(2, timeout=0.5)
        self.local = threading.local()

    def get_tuple(self, config):
        if getattr(self.local, "armed", False):
            self.local.armed = False
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                pass  # the other review was held off, so it reads after this one resumed
        return super().get_tuple(config)


def test_two_reviews_at_once_resume_once_and_the_other_is_refused():
    """Through the audit trail, which keeps two reviews of one thread apart (resume doesn't)."""
    saver = BarrierSaver(memory_checkpointer())
    result, _, _ = proposed("4.1.2", mixed_4_1_2(), saver)
    trail = audit.ProcessTrail(lambda: saver)
    outcomes: dict[str, str] = {}

    confirmed(result, saver)

    def review(decision: Decision) -> None:
        saver.local.armed = True
        try:
            reviewed = audit.review(
                trail,
                result.thread_id,
                ReviewDecision(decision=decision),
                reviewer=audit.Reviewer("lead", "Lead"),
            )
            assert reviewed.review is not None
            outcomes[decision] = reviewed.review.decision
        except NotWaitingForReviewError:
            outcomes[decision] = "refused"

    threads = [threading.Thread(target=review, args=(d,)) for d in ("accept", "reject")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes.values()).count("refused") == 1, outcomes
    recorded = thread_values(saver, result.thread_id)["review"].decision
    assert outcomes[recorded] == recorded  # the review that resumed got its own decision back
