import json
import shutil
from pathlib import Path

import pytest
from assess_steps import step_texts
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from vouch import ScriptedChatModel, UnknownControlError, assess, load_named_profile, load_profile
from vouch.graph import NOT_JUDGED, OutputCheckError, ShouldScore, claims_audit_result
from vouch.profile import NORDLICHT_PROFILE

SCRIPTS = Path(__file__).parent / "scripts"
NORDLICHT = load_named_profile("nordlicht")


def scripted(
    judge: list[dict] | list[list[dict]], answers: tuple[str, ...] = ("Draft answer.",)
) -> ScriptedChatModel:
    """A script that searches once, answers (once per entry), and judges as given: one
    judgement's requirements, or a list of them played in turn."""
    gather: list[dict] = [{"tool": "search_policies", "args": {"query": "policy"}}]
    gather += [{"answer": answer} for answer in answers]
    if judge and isinstance(judge[0], list):
        judgements: dict | list[dict] = [{"requirements": j} for j in judge]
    else:
        judgements = {"requirements": judge}
    return ScriptedChatModel(script={"gather": gather, "judge": judgements})


def judged(*ids: str, verdict: str = "evidenced") -> list[dict]:
    """Judgements for these ids, each citing a true quote (a verdict needs one to stand)."""
    quote = {"doc_id": "authentication-standard", "quote": "Passwords have at least 12 characters."}
    return [
        {"requirement_id": r, "verdict": verdict, "reason": "Shown.", "citations": [quote]}
        for r in ids
    ]


def test_a_scripted_run_on_4_1_2_judges_each_requirement_and_rolls_up_partly_evidenced():
    model = ScriptedChatModel.from_file(SCRIPTS / "isa_4_1_2.json")

    result = assess("4.1.2", NORDLICHT, model)

    assert result.control_id == "4.1.2"
    assert result.profile_id == "nordlicht"
    assert [(r.id, r.column, r.verdict) for r in result.requirements] == [
        ("M1", "must", "partly-evidenced"),
        ("M2", "must", "evidenced"),
        ("S1", "should", "evidenced"),
        ("S2", "should", "evidenced"),
        ("S3", "should", "partly-evidenced"),
    ]  # normal protection: H1 and V1 don't apply
    assert result.requirements[0].reason.startswith("The standard says methods follow")
    assert result.verdict == "partly-evidenced"
    assert result.should_score == ShouldScore(evidenced=2, total=3)
    assert result.answer.startswith("Sign-in methods follow each system's exposure")
    assert {c.doc_id for r in result.requirements for c in r.citations} == {
        "authentication-standard"
    }
    assert result.model == "scripted-fake"
    assert result.prompt_version
    assert [s["node"] for s in result.steps] == [
        "apply_overlays", "gather", "tools", "gather", "check_answer", "judge", "check_quotes"
    ]
    assert step_texts(result, "gather")[-1] == "Drafted an answer."
    assert step_texts(result, "judge") == ["Judged 3 of 5 requirements evidenced."]
    assert step_texts(result, "check_quotes") == [
        "Checked each quote against its document: kept 6 citations."
    ]


def test_the_assessment_reports_the_models_token_usage():
    result = assess("4.1.2", NORDLICHT, ScriptedChatModel.from_file(SCRIPTS / "isa_4_1_2.json"))

    usage = result.token_usage
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]


def test_the_assessment_is_plain_json():
    result = assess("4.1.2", NORDLICHT, ScriptedChatModel.from_file(SCRIPTS / "isa_4_1_2.json"))

    data = json.loads(json.dumps(result.to_dict()))
    assert data["verdict"] == "partly-evidenced"
    assert data["should_score"] == {"evidenced": 2, "total": 3}
    assert data["requirements"][0] == {
        "id": "M1",
        "column": "must",
        "text": result.requirements[0].text,
        "verdict": "partly-evidenced",
        "reason": result.requirements[0].reason,
        "citations": [
            {
                "doc_id": "authentication-standard",
                "version": "3.1",
                "section": "How we chose our sign-in methods",
                "quote": "We decide how people sign in to a system by looking at what could go "
                "wrong with it.",
            }
        ],
    }


def test_a_not_applicable_requirement_is_listed_with_its_reason_and_left_out_of_the_roll_up():
    model = scripted(judged("M1", "M2", "M3", "M5", "S1", "S2", "S3"))

    result = assess("5.2.4", NORDLICHT, model)

    m4 = next(r for r in result.requirements if r.id == "M4")
    assert m4.verdict == "not-applicable"
    assert "on premises" in m4.reason
    assert result.verdict == "evidenced"


@pytest.mark.parametrize(
    "bad",
    [
        {"requirement_id": "M1", "verdict": "found", "reason": "The trial's word."},
        {"requirement_id": "M1", "verdict": "not-applicable", "reason": "Only the profile says."},
        None,  # M1 skipped
    ],
    ids=["unknown-verdict", "not-applicable-from-the-model", "skipped"],
)
def test_an_unknown_verdict_or_a_skipped_requirement_is_not_evidenced_not_judged(bad):
    judge = judged("M2", "S1", "S2", "S3")
    if bad is not None:
        judge.insert(0, bad)

    result = assess("4.1.2", NORDLICHT, scripted(judge))

    m1 = result.requirements[0]
    assert (m1.id, m1.verdict, m1.reason) == ("M1", "not-evidenced", "not judged")
    assert NOT_JUDGED == "not judged"
    assert result.verdict == "partly-evidenced"
    assert step_texts(result, "judge")[-1] == "Judged 4 of 5 requirements evidenced. Not judged: M1."


def test_a_judgement_for_a_requirement_the_control_lacks_is_ignored():
    result = assess("4.1.2", NORDLICHT, scripted(judged("M1", "M2", "H1", "M9")))

    assert [r.id for r in result.requirements] == ["M1", "M2", "S1", "S2", "S3"]
    assert result.verdict == "evidenced"
    assert result.should_score == ShouldScore(evidenced=0, total=3)


def test_an_answer_claiming_compliant_is_retried_once_and_the_new_answer_kept():
    model = scripted([], answers=("Nordlicht is compliant.", "Sign-in is evidenced."))

    result = assess("4.1.2", NORDLICHT, model)

    assert result.answer == "Sign-in is evidenced."
    checks = [s["text"] for s in result.steps if s["node"] == "check_answer"]
    assert checks == [
        "The answer claimed an audit result (“compliant”), so it was asked for again.",
        "Checked the answer: it claims no audit result.",
    ]


@pytest.mark.parametrize("word", ["compliant", "Certified", "PASSED"])
def test_an_answer_claiming_an_audit_result_twice_fails(word):
    model = scripted([], answers=(f"Nordlicht is {word}.", f"Still {word}."))

    with pytest.raises(OutputCheckError, match="audit result"):
        assess("4.1.2", NORDLICHT, model)


def test_missing_policy_folder_gives_not_evidenced_and_a_warning(tmp_path):
    shutil.copytree(NORDLICHT_PROFILE.parent, tmp_path / "nordlicht")
    profile = load_profile(tmp_path / "nordlicht" / "profile.yaml")
    shutil.rmtree(profile.evidence_sources["policies"].folder)  # gone since the profile loaded
    model = ScriptedChatModel.from_file(SCRIPTS / "isa_4_1_2.json")

    result = assess("4.1.2", profile, model)

    assert result.verdict == "not-evidenced"
    assert {r.verdict for r in result.requirements} == {"not-evidenced"}
    assert "source_unavailable" in result.warnings


def test_an_unknown_control_raises_before_the_model_is_called():
    model = scripted([])

    with pytest.raises(UnknownControlError, match="9.9.9"):
        assess("9.9.9", NORDLICHT, model)
    assert model.turn == 0


@pytest.mark.parametrize(
    "text",
    [
        "Nordlicht is compliant.",
        "The company is fully Compliant with ISA.",
        "Nordlicht is certified.",
        "Nordlicht is TISAX-certified.",
        "Nordlicht passed the audit.",
        "Nordlicht has PASSED.",
        "The TISAX audit passed on 3 March with no findings.",
    ],
)
def test_a_claim_of_an_audit_result_is_caught(text):
    assert claims_audit_result(text)


@pytest.mark.parametrize(
    "text",
    [
        "The review lists three non-compliant supplier accounts.",
        "Nordlicht is not compliant with S2 yet.",
        "The supplier is not certified.",
        "Reports are passed to the CISO within a day.",
        "The finding was passed on to IT operations.",
        "The standard is evidenced in practice.",
    ],
)
def test_negations_and_handing_on_are_not_audit_claims(text):
    assert claims_audit_result(text) is None


class RecordingModel(ScriptedChatModel):
    """Plays fixed AI messages, recording the messages each call received."""

    replies: list[AIMessage]
    received: list[list]

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


def tool_call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_a_refused_answer_answers_every_tool_call_in_its_message():
    first = AIMessage(
        "",
        tool_calls=[
            tool_call("search_policies", {"query": "password"}, "s1"),
            tool_call("submit_answer", {"answer": "We are compliant."}, "a1"),
            tool_call("submit_answer", {"answer": "Also certified."}, "a2"),
        ],
    )
    second = AIMessage(
        "", tool_calls=[tool_call("submit_answer", {"answer": "Shown."}, "a3")]
    )
    model = RecordingModel(
        script={"gather": [{"answer": "unused"}]}, replies=[first, second], received=[]
    )

    result = assess("4.1.2", NORDLICHT, model)

    assert result.answer == "Shown."
    answered = {m.tool_call_id for m in model.received[1] if isinstance(m, ToolMessage)}
    assert answered == {"s1", "a1", "a2"}


def test_a_judgement_reason_claiming_an_audit_result_is_asked_for_again_once():
    bad = judged("M1", "M2", "S1", "S2", "S3")
    bad[1]["reason"] = "Nordlicht is compliant here."
    model = scripted([bad, judged("M1", "M2", "S1", "S2", "S3")])

    result = assess("4.1.2", NORDLICHT, model)

    assert {r.reason for r in result.requirements} == {"Shown."}
    assert step_texts(result, "judge")[-1] == (
        "A reason claimed an audit result (“compliant”), so the judgement was asked for again. "
        "Judged 5 of 5 requirements evidenced."
    )


def test_a_judgement_reason_claiming_an_audit_result_twice_fails():
    bad = judged("M1", "M2", "S1", "S2", "S3")
    bad[0]["reason"] = "Certified."
    model = scripted(bad)  # the one judgement is played again on the retry

    with pytest.raises(OutputCheckError, match="audit result"):
        assess("4.1.2", NORDLICHT, model)
