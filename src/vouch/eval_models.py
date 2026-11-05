"""The models the eval runs on, by name, and what each costs.

`fake` (vouch.spending.FAKE) is the scripted fake model (free, offline; vouch.evals plays it;
it can't judge). A name starting
`ollama/` is a local model through Ollama's OpenAI-compatible API (docs.ollama.com/api/
openai-compatibility); it costs nothing, Ollama must be running with the model pulled, and each
request may take OLLAMA_TIMEOUT_SECONDS (or --ollama-timeout). Any other name is an
OpenRouter model id, through the app's own OpenRouter client (vouch.model_choice: its call
budget, retries and timeout), with OPENROUTER_API_KEY from the environment (the keyring, through
`.envrc`). Without the key a real model is refused; the fake still runs.

MODELS is the runner's model list and JUDGE its faithfulness judge, chosen in #53
(docs/requirements.md, Decisions, 2026-11-05: the reasons, prices and the full run's estimate).
A model's cost per call is OpenRouter's reported `usage.cost` when it gives one, else its price
here (vouch.spending's UsageMeter does the sums); a real model with neither has an unknown cost,
never $0.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from vouch.model_choice import DEFAULT_MODEL, OpenRouter
from vouch.settings import OPENROUTER_API_KEY_ENV, Settings
from vouch.spending import FAKE, GPT_TERRA, MISTRAL_SMALL, PRICES, SONNET, Price, Usage

OLLAMA_PREFIX = "ollama/"
OLLAMA_URL = "http://localhost:11434"
OLLAMA_START = "ollama serve"
OLLAMA_CHECK_SECONDS = 3.0
OLLAMA_TIMEOUT_SECONDS = 600.0
"""One request's limit on a local model: a large one loading for its first call is slow."""
LOCAL_PRICE = Price(0.0, 0.0)


def price_of(name: str) -> Price | None:
    """US dollars per million tokens: nothing for a local model, else the app's price
    (vouch.spending.PRICES, which holds every hosted model in the list); None when none is
    known."""
    if name.startswith(OLLAMA_PREFIX):
        return LOCAL_PRICE
    return PRICES.get(name)


@dataclass(frozen=True)
class EvalModel:
    """A model the eval runs: its id (OpenRouter's, or `ollama/<name>`), why it's in the set,
    and where its price (or for a local model, its size) was read, with the date."""

    id: str
    why: str
    source: str

    @property
    def price(self) -> Price | None:
        """US dollars per million tokens, read from price_of (None: not known)."""
        return price_of(self.id)


OPENROUTER_SOURCE = "https://openrouter.ai/api/v1/models, 2026-11-05"
"""Where the hosted models' prices were read (vouch.spending.PRICES), and when."""
LOCAL_MODEL = "ollama/gpt-oss:20b"

MODELS: tuple[EvalModel, ...] = (
    EvalModel(
        DEFAULT_MODEL, "the app's default (docs/requirements.md, 2026-11-03)", OPENROUTER_SOURCE
    ),
    EvalModel(SONNET, "Anthropic's next size up: what more quality costs", OPENROUTER_SOURCE),
    EvalModel(
        MISTRAL_SMALL,
        "another provider at Haiku's price, EU-based (Mistral Small 4, tools and structured "
        "outputs)",
        OPENROUTER_SOURCE,
    ),
    EvalModel(
        LOCAL_MODEL,
        "local through Ollama: 14 GB, fits a 20 GB RX 7900 on ROCm, with tool calling",
        "https://ollama.com/library/gpt-oss, 2026-11-05",
    ),
)
"""The models compared: Haiku, Sonnet, one other provider, one local model (#53)."""
JUDGE = EvalModel(
    GPT_TERRA,
    "a mid-tier judge from a provider none of the compared models share, so it never judges "
    "its own answers",
    OPENROUTER_SOURCE,
)
"""The faithfulness judge, on real models unless --judge names another; on the fake, only
with --judge."""


class ModelUnavailable(RuntimeError):
    """The named model can't run: no key for OpenRouter, or Ollama not running."""


class OllamaNotRunning(ModelUnavailable):
    """Ollama doesn't answer: start it and try again."""


def cost_of(usage: Usage) -> float | None:
    """What the calls cost in US dollars: nothing on a local model; None when some call's cost
    isn't known (no reported cost, no price), never $0."""
    if usage.model.startswith(OLLAMA_PREFIX):
        return 0.0
    return usage.cost_usd if usage.cost_complete else None


def _ollama_url(url: str) -> str:
    """The URL without a trailing slash; raises ModelUnavailable when it isn't an http(s) URL
    with a host."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError):
        parsed = None
    if parsed is None or parsed.scheme not in ("http", "https") or not parsed.host:
        raise ModelUnavailable(
            f"--ollama-url {url!r} isn't a URL like {OLLAMA_URL} (http or https, with a host)."
        )
    return url.rstrip("/")


def _pulled(name: str, models: list[str]) -> bool:
    """Whether Ollama has `name`; a name without a tag means its `latest`."""
    return name in models or (":" not in name and f"{name}:latest" in models)


def check_ollama(url: str, model: str, client: httpx.Client | None = None) -> None:
    """Raises OllamaNotRunning (with the start command) when Ollama doesn't answer at `url`,
    and ModelUnavailable (with the pull command) when it hasn't pulled `model`."""
    http = client or httpx.Client(timeout=OLLAMA_CHECK_SECONDS)
    try:
        http.get(f"{url}/api/version").raise_for_status()
    except httpx.HTTPError as error:
        raise OllamaNotRunning(
            f"Ollama isn't running at {url} ({type(error).__name__}). "
            f"Start it with `{OLLAMA_START}`, then run the eval again."
        ) from None
    try:
        tags = http.get(f"{url}/api/tags")
        tags.raise_for_status()
        pulled = [m["name"] for m in tags.json()["models"]]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        raise ModelUnavailable(
            f"Ollama at {url} didn't list its models ({type(error).__name__})."
        ) from None
    if not _pulled(model, pulled):
        raise ModelUnavailable(
            f"Ollama has no model {model}. Pull it with `ollama pull {model}`, then run the "
            "eval again."
        )


ChatModels = Callable[[list[BaseCallbackHandler]], BaseChatModel]
"""Makes a fresh chat model for one assessment, its calls seen by the callbacks given."""
Role = Literal["model", "judge"]


def chat_models(
    name: str,
    settings: Settings,
    *,
    role: Role = "model",
    ollama_url: str = OLLAMA_URL,
    ollama_timeout: float = OLLAMA_TIMEOUT_SECONDS,
) -> ChatModels:
    """What makes `name`'s chat models, as the run's model or its judge, checked before any
    case runs: the key for OpenRouter; Ollama running, with the model pulled. The fake can't
    judge. Raises ModelUnavailable (OllamaNotRunning)."""
    if name == FAKE:
        raise ModelUnavailable(
            "The scripted fake model can't judge faithfulness: leave out --judge to run without it."
        )
    if name.startswith(OLLAMA_PREFIX):
        url = _ollama_url(ollama_url)
        local_name = name.removeprefix(OLLAMA_PREFIX)
        check_ollama(url, local_name)
        # The same client as OpenRouter's (its call budget too); Ollama ignores the key.
        local = OpenRouter(SecretStr("ollama"), base_url=f"{url}/v1/")
        return lambda callbacks: local.chat_model(local_name, callbacks, timeout=ollama_timeout)
    if settings.openrouter_api_key is None:
        instead = (
            "The fake model needs no key: run with --model fake."
            if role == "model"
            else "Leave out --judge to run without faithfulness."
        )
        raise ModelUnavailable(
            f"The {role} {name} runs on OpenRouter, and {OPENROUTER_API_KEY_ENV} isn't set (it "
            f"comes from the keyring through .envrc). {instead}"
        )
    router = OpenRouter(settings.openrouter_api_key)
    return lambda callbacks: router.chat_model(name, callbacks)
