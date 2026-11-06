"""Which model an assessment runs on, and the developer's choice of it (#34).

The order, for the API and the CLI alike: ASSESS_FAKE_SCRIPT's scripted fake when set (tests,
development); else OpenRouter when OPENROUTER_API_KEY is set; else the bundled demo script in the
profile's answer language (vouch.settings.DEMO_SCRIPTS: German for a German profile, demo.json
for every other), so a missing key costs nothing. On Cloud Run every assessment route needs a signed-in user (clerk
mode, vouch.api), so only signed-in users reach the paid model; locally a key in the environment
is the opt-in.

The OpenRouter model is the one a developer chose on the developer page, else ASSESS_MODEL, else
DEFAULT_MODEL. The choice is kept in the database (vouch.database's settings table) when
VOUCH_DATABASE_URL is set, else in memory until the server restarts. While assessments run on
OpenRouter, a chosen id is checked against its model list before it is kept, so an unknown id is
refused, not stored. When the stored choice can't be read, no assessment starts: a different
paid model never runs in its place.

Paid calls are bounded per assessment: at most CALL_BUDGET model calls (the graph's gather loop
has no limit of its own), each retried at most MAX_RETRIES times. The openai SDK retries a
timeout, a dropped connection, 408, 409, 429 and 5xx, after its backoff (0.5 s less up to 25%
jitter, or the server's Retry-After). A request that timed out may still have been generated and
billed by OpenRouter, so its retry may be billed again: TIMEOUT_SECONDS is long enough for a slow
but valid answer, and one retry the most. What still fails raises, and the API turns it into one
`error` event.
"""

import logging
import re
import time
from collections.abc import Callable, Iterator
from typing import Any, Literal, Protocol

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from pydantic import PrivateAttr, SecretStr

from vouch.chat import CHAT_PROMPT_VERSION
from vouch.database import Database
from vouch.graph import PROMPT_VERSION
from vouch.settings import Settings, demo_script, fake_model

log = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/"
"""With the trailing slash, so a relative link (`models?offset=500`) resolves under it."""
OPENROUTER_HOST = "openrouter.ai"
"""The one host the key is ever sent to."""
DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
"""The owner's choice from the eval report (#55; docs/requirements.md, Decisions, 2026-11-06)."""
USAGE_URL = "https://openrouter.ai/activity"
"""OpenRouter's own usage and cost per request, beside Vouch's totals (vouch.spending)."""
CALL_BUDGET = 12
"""Model calls one assessment may make (docs/requirements.md, Decisions, 2026-11-05); a run on
the demo script makes 4 (2 gather, 1 judge, 1 remediation draft).
The judge and the remediation drafts are calls too (the drafts only when a requirement is not or
partly evidenced, #212), and each, like the answer, may be asked for once more by the output
check."""
MAX_RETRIES = 1
"""Retries of one call after the first try: each may be billed (a timeout too)."""
TIMEOUT_SECONDS = 120.0
"""One request's limit: long enough that a slow but valid answer isn't cut off and paid twice."""
CATALOGUE_TIMEOUT_SECONDS = 10.0
CATALOGUE_TTL_SECONDS = 600.0
"""How long one reading of OpenRouter's model list answers id checks."""
CATALOGUE_PAGES = 10
"""At most this many pages of the model list are read (500 ids a page by default)."""
MODEL_SETTING = "model"
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
"""An OpenRouter model id's shape, `author/slug` (a variant such as `:free` allowed)."""

RunsOn = Literal["openrouter", "fake"]
FakeReason = Literal["no_key", "fake_script"]
KeptIn = Literal["database", "memory"]


class UnknownModel(ValueError):
    """OpenRouter lists no model with this id."""


class CatalogueUnavailable(RuntimeError):
    """OpenRouter's model list couldn't be read to its end, so an id can't be checked."""


class NoKey(RuntimeError):
    """No OpenRouter key is set: there's nothing to choose, and no way to check an id."""


class CallBudgetSpent(RuntimeError):
    """The assessment asked for more model calls than CALL_BUDGET."""


class ModelStore(Protocol):
    """Where the developer's model choice is kept. Both methods may raise DatabaseUnavailable."""

    kept_in: KeptIn

    def get(self) -> str | None: ...

    def set(self, model: str) -> None: ...


class MemoryModelStore:
    """No database: the choice lasts until the server restarts."""

    kept_in: KeptIn = "memory"

    def __init__(self) -> None:
        self._model: str | None = None

    def get(self) -> str | None:
        return self._model

    def set(self, model: str) -> None:
        self._model = model


class DatabaseModelStore:
    """The choice in the database's settings table, shared by every instance and restart."""

    kept_in: KeptIn = "database"

    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self) -> str | None:
        return self.db.setting(MODEL_SETTING)

    def set(self, model: str) -> None:
        self.db.set_setting(MODEL_SETTING, model)


def _budgeted_chat_openai() -> type[BaseChatModel]:
    """ChatOpenAI counting its calls: past `call_budget`, a call raises CallBudgetSpent before
    any request. One instance serves one assessment (ModelChoice.for_assessment)."""
    from langchain_openai import ChatOpenAI  # only the real path needs the provider

    class BudgetedChatOpenAI(ChatOpenAI):
        call_budget: int = CALL_BUDGET
        _calls: int = PrivateAttr(default=0)

        def _spend(self) -> None:
            if self._calls >= self.call_budget:
                log.error(
                    "the assessment spent its call budget of %d model calls", self.call_budget
                )
                raise CallBudgetSpent(f"more than {self.call_budget} model calls")
            self._calls += 1

        def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
            self._spend()
            return super()._generate(*args, **kwargs)

        def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
            self._spend()
            yield from super()._stream(*args, **kwargs)

    return BudgetedChatOpenAI


class OpenRouter:
    """The app's two uses of OpenRouter: a chat model, and its model list to check an id.

    `chat_client` (an httpx2.Client, the openai SDK's HTTP library) and `catalogue_client` (an
    httpx.Client, made on first use otherwise) are the seams tests fill with fakes.
    """

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        chat_client: Any = None,
        catalogue_client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api_key = api_key
        self.base_url = httpx.URL(base_url)
        self.clock = clock
        self._chat_client = chat_client
        self._catalogue = catalogue_client
        self._ids: tuple[float, frozenset[str]] | None = None

    def __repr__(self) -> str:
        return f"OpenRouter(api_key={self.api_key!r})"

    def close(self) -> None:
        if self._catalogue is not None:
            self._catalogue.close()

    def chat_model(
        self,
        model: str,
        callbacks: list[BaseCallbackHandler] | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> BaseChatModel:
        """A chat model for one assessment, within CALL_BUDGET calls. `timeout`: one request's
        limit (a local model through the eval runner may need longer)."""
        return _budgeted_chat_openai()(
            callbacks=callbacks,
            model=model,
            api_key=self.api_key,
            base_url=str(self.base_url),
            max_retries=MAX_RETRIES,
            timeout=timeout,
            http_client=self._chat_client,
        )

    def knows(self, model: str) -> bool:
        """Whether OpenRouter's model list has `model`; the list is kept CATALOGUE_TTL_SECONDS.
        Raises CatalogueUnavailable."""
        now = self.clock()
        if self._ids is None or now - self._ids[0] >= CATALOGUE_TTL_SECONDS:
            self._ids = (now, self._read_ids())
        return model in self._ids[1]

    def _read_ids(self) -> frozenset[str]:
        """Every id in the list, page by page, on the base URL's origin only."""
        if self._catalogue is None:
            self._catalogue = httpx.Client(timeout=CATALOGUE_TIMEOUT_SECONDS)
        headers = {}
        if self.base_url.host == OPENROUTER_HOST and self.base_url.scheme == "https":
            headers["Authorization"] = f"Bearer {self.api_key.get_secret_value()}"
        url: httpx.URL | None = self.base_url.join("models")
        ids: set[str] = set()
        try:
            for _ in range(CATALOGUE_PAGES):
                if url is None:
                    return frozenset(ids)
                response = self._catalogue.get(url, headers=headers)
                response.raise_for_status()
                page = response.json()
                ids |= {entry["id"] for entry in page["data"]}
                next_page = (page.get("links") or {}).get("next")
                url = self.base_url.join(next_page) if next_page else None
                if url is not None and not self._same_origin(url):
                    log.warning("OpenRouter's model list links to another origin: not followed")
                    raise CatalogueUnavailable()
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            log.warning("OpenRouter's model list failed: %s %s", type(error).__name__, status or "")
            raise CatalogueUnavailable() from None
        log.warning("OpenRouter's model list has more than %d pages", CATALOGUE_PAGES)
        raise CatalogueUnavailable()

    def _same_origin(self, url: httpx.URL) -> bool:
        base = self.base_url
        return (url.scheme, url.host, url.port) == (base.scheme, base.host, base.port)


class ModelChoice:
    """The model an assessment runs on, for the API and the CLI; one per app."""

    def __init__(
        self,
        settings: Settings,
        store: ModelStore,
        openrouter: OpenRouter | None = None,
    ) -> None:
        self._settings = settings
        self.store = store
        key = settings.openrouter_api_key
        self.openrouter = openrouter or (OpenRouter(key) if key else None)

    def __repr__(self) -> str:
        return f"ModelChoice(runs_on={self.runs_on!r}, kept_in={self.kept_in!r})"

    @property
    def default(self) -> str:
        return self._settings.model or DEFAULT_MODEL

    @property
    def fake_reason(self) -> FakeReason | None:
        """Why assessments run on the fake, if they do: a script is set, or no key is."""
        if self._settings.fake_script is not None:
            return "fake_script"
        return "no_key" if self.openrouter is None else None

    @property
    def runs_on(self) -> RunsOn:
        return "fake" if self.fake_reason else "openrouter"

    @property
    def kept_in(self) -> KeptIn:
        return self.store.kept_in

    def current(self) -> str:
        """The chosen model, else the default. Raises DatabaseUnavailable."""
        return self.store.get() or self.default

    def settings(self, model: str | None = None) -> dict[str, Any]:
        """What the developer page shows. Raises DatabaseUnavailable."""
        return {
            "model": model or self.current(),
            "runs_on": self.runs_on,
            "fake_reason": self.fake_reason,
            "prompt_version": PROMPT_VERSION,
            "chat_prompt_version": CHAT_PROMPT_VERSION,
            "usage_url": USAGE_URL,
            "kept_in": self.kept_in,
        }

    def change(self, model: str) -> str:
        """Keep `model` as the choice: checked with OpenRouter while assessments run on it, kept
        unchecked while a fake script runs them. Raises NoKey, UnknownModel,
        CatalogueUnavailable or DatabaseUnavailable."""
        if self.openrouter is None:
            raise NoKey()
        if self.runs_on == "openrouter" and not self.openrouter.knows(model):
            raise UnknownModel(model)
        self.store.set(model)
        log.info("the developer model is now %s", model)
        return model

    def chosen(self) -> str | None:
        """The model id the next assessment runs on; None on the fake. Read before an
        assessment starts. Raises DatabaseUnavailable: then no assessment runs."""
        return self.current() if self.runs_on == "openrouter" else None

    def for_assessment(
        self,
        chosen: str | None,
        callbacks: list[BaseCallbackHandler] | None = None,
        *,
        answer_language: str = "en",
    ) -> BaseChatModel:
        """A fresh model for one assessment, on `chosen` (from `chosen()`): its own call budget,
        or the fake at the start of its script. `callbacks` see every call (the usage meter).
        On the fake: ASSESS_FAKE_SCRIPT when set, else the demo script in the profile's
        `answer_language` (vouch.settings.demo_script, #234)."""
        if chosen is None or self.openrouter is None:  # None: runs_on is "fake"
            fake = fake_model(self._settings, demo_script(answer_language))
            fake.callbacks = callbacks
            return fake
        return self.openrouter.chat_model(chosen, callbacks)

    def for_chat(
        self, chosen: str | None, callbacks: list[BaseCallbackHandler] | None = None
    ) -> BaseChatModel:
        """A fresh model for one chat message (#44), on `chosen` (from `chosen()`), within
        CALL_BUDGET calls; on the fake, the chat's canned answer. `callbacks` see every call
        (the usage meter), as for_assessment's do."""
        if chosen is None or self.openrouter is None:  # None: runs_on is "fake"
            # Imported here: the fake knows the chat agent's tools, and the chat agent's
            # modules (vouch.spending among them) import this one.
            from vouch.chat_fake import CannedChatModel

            return CannedChatModel(callbacks=callbacks)
        return self.openrouter.chat_model(chosen, callbacks)

    def close(self) -> None:
        if self.openrouter is not None:
            self.openrouter.close()


def model_store(db: Database | None) -> ModelStore:
    return DatabaseModelStore(db) if db is not None else MemoryModelStore()
