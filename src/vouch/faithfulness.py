"""Faithfulness of an assessment's answer to the passages it cites, with DeepEval and a judge.

DeepEval's FaithfulnessMetric (deepeval.com/docs/metrics-faithfulness) has the judge list the
truths in the passages and the claims in the answer, then decide for each claim whether the
truths back it; the score is the share that do (an ambiguous claim counts as backed, the
library's default). The judge is any chat model (ChatJudge), so an OpenRouter or local model
judges, and a fixed one in tests. An answer citing nothing isn't judged: there's no passage to
hold it to. A judge that fails leaves no score and says why; the assessment's scores stand.

DeepEval is a dev dependency, imported only when a judgement runs. Its telemetry is turned off
before the import: evidence passages never go anywhere but the judge.
"""

import functools
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

TELEMETRY_OPT_OUT = "DEEPEVAL_TELEMETRY_OPT_OUT"
NOTHING_CITED = "The answer cites nothing, so nothing cited to check it against."


@dataclass(frozen=True)
class Faithfulness:
    """The share of the answer's claims its passages back (None: not judged), and why."""

    score: float | None
    reason: str | None = None
    error: str | None = None  # why the judge gave no score


@functools.cache
def _chat_judge() -> type:
    """DeepEval's custom-model shape around a LangChain chat model. The judge's reply is
    returned as text, and DeepEval reads the JSON its prompts ask for."""
    from deepeval.models import DeepEvalBaseLLM

    class ChatJudge(DeepEvalBaseLLM):
        def __init__(self, chat: BaseChatModel, name: str) -> None:
            self.chat = chat
            self.name = name
            self.model = chat

        def load_model(self) -> BaseChatModel:
            return self.chat

        def generate(self, prompt: str, schema: Any = None) -> str:
            return str(self.chat.invoke(prompt).content)

        async def a_generate(self, prompt: str, schema: Any = None) -> str:
            return self.generate(prompt, schema)

        def get_model_name(self) -> str:
            return self.name

    return ChatJudge


def measure_faithfulness(
    question: str, answer: str, passages: Sequence[str], judge: BaseChatModel, judge_name: str
) -> Faithfulness:
    """How faithful `answer` (to `question`) is to `passages`, judged by `judge`."""
    if not passages:
        return Faithfulness(None, NOTHING_CITED)
    os.environ.setdefault(TELEMETRY_OPT_OUT, "1")
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase

    metric = FaithfulnessMetric(
        model=_chat_judge()(judge, judge_name),
        eval_mode="llm",  # the judge decides every claim; no other service is asked
        async_mode=False,
        include_reason=True,
    )
    case = LLMTestCase(input=question, actual_output=answer, retrieval_context=list(passages))
    try:
        score = metric.measure(case, _show_indicator=False)
    # Any exception: a judge's bad JSON, a provider's error; the eval goes on without a score.
    except Exception as error:  # noqa: BLE001
        return Faithfulness(None, error=f"{type(error).__name__}: {error}")
    return Faithfulness(score, metric.reason)
