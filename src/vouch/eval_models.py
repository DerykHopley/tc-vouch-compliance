"""The models the eval runs on, by name, and what each costs.

`fake` is the scripted fake model (free, offline; vouch.evals plays it). A name starting
`ollama/` is a local model through Ollama's OpenAI-compatible API (docs.ollama.com/api/
openai-compatibility); it costs nothing, and Ollama must be running. Any other name is an
OpenRouter model id, through the app's own OpenRouter client (vouch.model_choice: its call
budget, retries and timeout), with OPENROUTER_API_KEY from the environment (the keyring, through
`.envrc`). Without the key a real model is refused; the fake still runs.

MODELS is the runner's model list (#53 names the set and the judge). A model's cost per call is
OpenRouter's reported `usage.cost` when it gives one, else its price here (vouch.spending's
UsageMeter does the sums); a real model with neither has an unknown cost, never $0.
"""

from collections.abc import Callable
from dataclasses import dataclass

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from vouch.model_choice import DEFAULT_MODEL, OpenRouter
from vouch.settings import OPENROUTER_API_KEY_ENV, Settings
from vouch.spending import PRICES, Price, Usage

FAKE = "fake"
OLLAMA_PREFIX = "ollama/"
OLLAMA_URL = "http://localhost:11434"
OLLAMA_START = "ollama serve"
OLLAMA_CHECK_SECONDS = 3.0
LOCAL_PRICE = Price(0.0, 0.0)


@dataclass(frozen=True)
class EvalModel:
    """A model the eval compares: its id (OpenRouter's, or `ollama/<name>`), its price in US
    dollars per million tokens (None: not known), and why it's in the set."""

    id: str
    price: Price | None
    why: str = ""


MODELS: tuple[EvalModel, ...] = (
    EvalModel(
        DEFAULT_MODEL, PRICES[DEFAULT_MODEL], "the app's default (docs/requirements.md, 2026-11-03)"
    ),
)
"""The models compared; #53 adds the rest, with their prices, source and date."""
JUDGE: str | None = None
"""The faithfulness judge's model; #53 names it. None: faithfulness only with --judge."""


class ModelUnavailable(RuntimeError):
    """The named model can't run: no key for OpenRouter, or Ollama not running."""


class OllamaNotRunning(ModelUnavailable):
    """Ollama doesn't answer: start it and try again."""


def price_of(name: str) -> Price | None:
    """US dollars per million tokens: nothing for a local model, the list's price for a listed
    one, else the app's (vouch.spending.PRICES); None when none is known."""
    if name.startswith(OLLAMA_PREFIX):
        return LOCAL_PRICE
    listed = {m.id: m.price for m in MODELS}
    return listed[name] if name in listed else PRICES.get(name)


def cost_of(usage: Usage) -> float | None:
    """What the calls cost in US dollars: nothing on a local model; None when some call's cost
    isn't known (no reported cost, no price), never $0."""
    if usage.model.startswith(OLLAMA_PREFIX):
        return 0.0
    return usage.cost_usd if usage.cost_complete else None


def check_ollama(url: str) -> None:
    """Raises ModelUnavailable, with the start command, when Ollama doesn't answer at `url`."""
    try:
        httpx.get(f"{url}/api/version", timeout=OLLAMA_CHECK_SECONDS).raise_for_status()
    except httpx.HTTPError as error:
        raise OllamaNotRunning(
            f"Ollama isn't running at {url} ({type(error).__name__}). "
            f"Start it with `{OLLAMA_START}`, then run the eval again."
        ) from None


ChatModels = Callable[[list[BaseCallbackHandler]], BaseChatModel]
"""Makes a fresh chat model for one assessment, its calls seen by the callbacks given."""


def chat_models(name: str, settings: Settings, *, ollama_url: str = OLLAMA_URL) -> ChatModels:
    """What makes `name`'s chat models, checked before any case runs. Raises ModelUnavailable."""
    if name.startswith(OLLAMA_PREFIX):
        check_ollama(ollama_url)
        # The same client as OpenRouter's (its call budget too); Ollama ignores the key.
        local = OpenRouter(SecretStr("ollama"), base_url=f"{ollama_url}/v1/")
        return lambda callbacks: local.chat_model(name.removeprefix(OLLAMA_PREFIX), callbacks)
    if settings.openrouter_api_key is None:
        raise ModelUnavailable(
            f"{name} runs on OpenRouter, and {OPENROUTER_API_KEY_ENV} isn't set (it comes from "
            "the keyring through .envrc). The fake model needs no key: run with --model fake."
        )
    router = OpenRouter(settings.openrouter_api_key)
    return lambda callbacks: router.chat_model(name, callbacks)
