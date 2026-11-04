"""Country overlays in the graph: `apply overlays` adds their requirements and branches (#25)."""

from typing import Any

from assess_steps import in_order, step_texts
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from vouch import ScriptedChatModel, assess, load_named_profile
from vouch.profile import CountryOverlay

NORDLICHT = load_named_profile("nordlicht")
OVERLAY = "AT NIS2 (illustrative)"
WITH_OVERLAY = NORDLICHT.model_copy(
    update={
        "overlays": (
            CountryOverlay.model_validate(
                {
                    "name": OVERLAY,
                    "requirements": [
                        {
                            "control": "1.6.2",
                            "id": "O1",
                            "text": "An early warning goes out within a day.",
                        },
                    ],
                }
            ),
        )
    }
)
NO_BRANCH = "No country overlay adds a requirement to this control, so no overlay branch is taken."
TRUE_QUOTE = {
    "doc_id": "authentication-standard",
    "quote": "Passwords have at least 12 characters.",
}


class RecordingModel(ScriptedChatModel):
    """The scripted model, keeping the messages each gather call and judge call was given."""

    gathered: list[list[BaseMessage]] = Field(default_factory=list)
    judged: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        self.gathered.append(list(messages))
        return super()._generate(messages, *args, **kwargs)

    def with_structured_output(self, schema, **kwargs):
        inner = super().with_structured_output(schema, **kwargs)

        def judge(messages: list[BaseMessage]):
            self.judged.append(list(messages))
            return inner.invoke(messages)

        return RunnableLambda(judge)


def judging(verdicts: dict[str, str]) -> RecordingModel:
    """A script that searches once, answers, and gives these verdicts, each citing a true quote."""
    return RecordingModel(
        script={
            "gather": [
                {"tool": "search_policies", "args": {"query": "security events"}},
                {"answer": "Draft answer."},
            ],
            "judge": {
                "requirements": [
                    {
                        "requirement_id": r,
                        "verdict": v,
                        "reason": "Shown.",
                        "citations": [TRUE_QUOTE],
                    }
                    for r, v in verdicts.items()
                ]
            },
        }
    )


def text_of(messages: list[BaseMessage]) -> str:
    return "\n".join(str(m.content) for m in messages)


def test_an_overlay_requirement_is_judged_counted_in_the_roll_up_and_shown_as_a_branch():
    base = {r: "evidenced" for r in ("M1", "M2", "M3", "S1", "S2", "S3")}
    model = judging({**base, "O1": "not-evidenced"})

    result = assess("1.6.2", WITH_OVERLAY, model)

    o1 = result.requirements[-1]
    assert (o1.id, o1.column, o1.verdict) == ("O1", "overlay", "not-evidenced")
    assert result.verdict == "partly-evidenced"  # O1 counts like a must requirement
    assert in_order([s["node"] for s in result.steps], "apply_overlays", "overlay_branch", "gather")
    assert step_texts(result, "apply_overlays") == [
        f"Applied the country overlays: {OVERLAY} adds O1."
    ]
    assert step_texts(result, "overlay_branch") == [
        f"Overlay branch: gathering evidence for O1 ({OVERLAY})."
    ]
    first_gather = text_of(model.gathered[0])
    assert "O1" in first_gather and OVERLAY in first_gather
    assert "O1 (overlay" in text_of(model.judged[0])


def test_an_overlay_requirement_judged_evidenced_lets_the_control_roll_up_evidenced():
    model = judging({r: "evidenced" for r in ("M1", "M2", "M3", "S1", "S2", "S3", "O1")})

    result = assess("1.6.2", WITH_OVERLAY, model)

    assert result.verdict == "evidenced"
    assert step_texts(result, "judge") == ["Judged 7 of 7 requirements evidenced."]


def test_without_an_overlay_no_branch_is_taken_and_the_step_says_so():
    model = judging({r: "evidenced" for r in ("M1", "M2", "M3", "S1", "S2", "S3")})

    result = assess("1.6.2", NORDLICHT, model)

    nodes = [s["node"] for s in result.steps]
    assert in_order(nodes, "apply_overlays", "gather")
    assert "overlay_branch" not in nodes
    assert step_texts(result, "apply_overlays") == [NO_BRANCH]
    assert "overlay" not in text_of(model.gathered[0]).lower()


def test_an_overlay_on_another_control_takes_no_branch():
    model = judging({})

    result = assess("1.6.1", WITH_OVERLAY, model)

    assert "overlay_branch" not in [s["node"] for s in result.steps]
    assert step_texts(result, "apply_overlays") == [NO_BRANCH]


def test_nordlichts_5_1_2_s3_is_listed_not_applicable_with_its_reason_and_not_judged():
    every = ("M1", "M2", "M3", "S1", "S2", "S3")
    model = judging({r: "evidenced" for r in every})  # the model's S3 verdict is ignored

    result = assess("5.1.2", NORDLICHT, model)

    s3 = next(r for r in result.requirements if r.id == "S3")
    assert s3.verdict == "not-applicable"
    assert s3.reason.startswith("Nordlicht allows no remote access to its company network")
    assert s3.citations == ()
    assert "S3" not in text_of(model.judged[0])
    assert step_texts(result, "judge") == ["Judged 5 of 5 requirements evidenced."]
    assert result.verdict == "evidenced"
