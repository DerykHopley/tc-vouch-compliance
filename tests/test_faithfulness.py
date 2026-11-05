"""Faithfulness: DeepEval's metric, judged by a chat model, on an assessment's answer and the
passages it cites. The judge here gives fixed outputs, so nothing leaves the machine."""

import json

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import BaseMessage
from pydantic import Field

from vouch.faithfulness import Faithfulness, measure_faithfulness

QUESTION = "To what extent are information security policies available?"
ANSWER = "The policy was approved in July 2026 and is published on the intranet."
PASSAGES = ("Approved by management on 20 July 2026.", "Published on the intranet.")


class FixedJudge(FakeListChatModel):
    """Plays its replies in turn and keeps every prompt it was given."""

    prompts: list[str] = Field(default_factory=list)

    def _call(self, messages: list[BaseMessage], *args, **kwargs) -> str:
        self.prompts.append(str(messages[-1].content))
        return super()._call(messages, *args, **kwargs)


def judge(*replies: dict) -> FixedJudge:
    return FixedJudge(responses=[json.dumps(r) for r in replies] or ["{}"], prompts=[])


def test_the_score_is_the_share_of_the_answers_claims_its_passages_back():
    fixed = judge(
        {"truths": ["The policy was approved on 20 July 2026.", "It is on the intranet."]},
        {
            "claims": [
                "The policy was approved in July 2026.",
                "It is on the intranet.",
                "It is reviewed every month.",
            ]
        },
        {
            "verdicts": [
                {"verdict": "yes"},
                {"verdict": "yes"},
                {"verdict": "no", "reason": "No passage says monthly."},
            ]
        },
        {"reason": "One claim, a monthly review, has no passage behind it."},
    )

    result = measure_faithfulness(QUESTION, ANSWER, PASSAGES, fixed, "judge/fixed")

    assert result == Faithfulness(
        score=2 / 3, reason="One claim, a monthly review, has no passage behind it."
    )
    truths_prompt, claims_prompt = fixed.prompts[:2]
    assert all(p in truths_prompt for p in PASSAGES)  # the cited passages are the context
    assert ANSWER in claims_prompt  # the claims come from the answer
    assert len(fixed.prompts) == 4  # truths, claims, verdicts, reason


def test_an_answer_citing_nothing_is_not_judged():
    fixed = judge()

    result = measure_faithfulness(QUESTION, ANSWER, (), fixed, "judge/fixed")

    assert result.score is None
    assert result.reason is not None and "nothing cited" in result.reason
    assert fixed.prompts == []


def test_a_judge_that_errors_leaves_no_score_and_says_why():
    fixed = judge({"truths": []}, {"not claims": True})  # the claims reply doesn't fit

    result = measure_faithfulness(QUESTION, ANSWER, PASSAGES, fixed, "judge/fixed")

    assert result.score is None
    assert result.error
