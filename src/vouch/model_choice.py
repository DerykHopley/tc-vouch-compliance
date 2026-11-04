"""Which model an assessment runs on, and the developer's choice of it (#34).

The order, as the CLI's: ASSESS_FAKE_SCRIPT's scripted fake when set (tests, development); else
OpenRouter when OPENROUTER_API_KEY is set; else the bundled demo script, so a missing key costs
nothing. On Cloud Run every assessment route needs a signed-in user (clerk mode, vouch.api), so
only signed-in users reach the paid model; locally a key in the environment is the opt-in.

The OpenRouter model is the one a developer chose on the developer page, else ASSESS_MODEL, else
DEFAULT_MODEL. The choice is kept in the database (vouch.database's settings table) when
VOUCH_DATABASE_URL is set, else in memory until the server restarts. A chosen id is checked
against OpenRouter's model list before it is kept, so an unknown id is refused, not stored.

OpenRouter failures: the openai SDK retries a timeout, a dropped connection, 408, 409, 429 and
5xx MAX_RETRIES times with exponential backoff (0.5 s, 1 s, ... less up to 25% jitter, or the
server's Retry-After); what still fails raises, and the API turns it into one `error` event.
"""

import logging
import re
from typing import Any, Literal, Protocol

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from vouch.database import Database, DatabaseUnavailable
from vouch.settings import DEMO_SCRIPT, Settings, fake_model

log = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
"""docs/requirements.md, Decisions (2026-11-03)."""
USAGE_URL = "https://openrouter.ai/activity"
"""OpenRouter's own usage and cost per request, until #35 shows them in Vouch."""
MAX_RETRIES = 2
"""Retries after the first try; with the backoff, about 1.5 s more before an error event."""
TIMEOUT_SECONDS = 60.0
"""One request's limit: a model call that never answers is a timeout, retried as above."""
CATALOGUE_TIMEOUT_SECONDS = 10.0
CATALOGUE_PAGES = 10
"""At most this many pages of the model list are read (500 ids a page by default)."""
MODEL_SETTING = "model"
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
"""An OpenRouter model id's shape, `author/slug` (a variant such as `:free` allowed)."""

RunsOn = Literal["openrouter", "fake"]


class UnknownModel(ValueError):
    """OpenRouter lists no model with this id."""


class CatalogueUnavailable(RuntimeError):
    """OpenRouter's model list couldn't be read, so an id can't be checked."""


class NoKey(RuntimeError):
    """No OpenRouter key is set: there's nothing to choose, and no way to check an id."""


class ModelStore(Protocol):
    """Where the developer's model choice is kept. Both methods may raise DatabaseUnavailable."""

    kept_in: str

    def get(self) -> str | None: ...

    def set(self, model: str) -> None: ...


class MemoryModelStore:
    """No database: the choice lasts until the server restarts."""

    kept_in = "memory"

    def __init__(self) -> None:
        self._model: str | None = None

    def get(self) -> str | None:
        return self._model

    def set(self, model: str) -> None:
        self._model = model


class DatabaseModelStore:
    """The choice in the database's settings table, shared by every instance and restart."""

    kept_in = "database"

    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self) -> str | None:
        return self.db.setting(MODEL_SETTING)

    def set(self, model: str) -> None:
        self.db.set_setting(MODEL_SETTING, model)


class OpenRouter:
    """The app's two uses of OpenRouter: a chat model, and its model list to check an id.

    `chat_client` (an httpx2.Client, the openai SDK's HTTP library) and `catalogue_client` (an
    httpx.Client) are the seams tests fill with fakes.
    """

    def __init__(
        self,
        api_key: SecretStr,
        *,
        chat_client: Any = None,
        catalogue_client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self._chat_client = chat_client
        self._catalogue = catalogue_client or httpx.Client(timeout=CATALOGUE_TIMEOUT_SECONDS)

    def __repr__(self) -> str:
        return f"OpenRouter(api_key={self.api_key!r})"

    def close(self) -> None:
        self._catalogue.close()

    def chat_model(self, model: str) -> BaseChatModel:
        from langchain_openai import ChatOpenAI  # only the real path needs the provider

        return ChatOpenAI(
            model=model,
            api_key=self.api_key,
            base_url=OPENROUTER_BASE_URL,
            max_retries=MAX_RETRIES,
            timeout=TIMEOUT_SECONDS,
            http_client=self._chat_client,
        )

    def knows(self, model: str) -> bool:
        """Whether OpenRouter's model list has `model`. Raises CatalogueUnavailable."""
        url: str | None = f"{OPENROUTER_BASE_URL}/models"
        headers = {"Authorization": f"Bearer {self.api_key.get_secret_value()}"}
        try:
            for _ in range(CATALOGUE_PAGES):
                if url is None:
                    break
                response = self._catalogue.get(url, headers=headers)
                response.raise_for_status()
                page = response.json()
                if any(entry.get("id") == model for entry in page["data"]):
                    return True
                next_page = (page.get("links") or {}).get("next")
                url = str(httpx.URL(OPENROUTER_BASE_URL).join(next_page)) if next_page else None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            log.warning("OpenRouter's model list failed: %s %s", type(error).__name__, status or "")
            raise CatalogueUnavailable() from None
        return False


class ModelChoice:
    """The model an assessment runs on, for the API; one per app."""

    def __init__(
        self,
        settings: Settings,
        store: ModelStore,
        openrouter: OpenRouter | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        key = settings.openrouter_api_key
        self.openrouter = openrouter or (OpenRouter(key) if key else None)

    def __repr__(self) -> str:
        return f"ModelChoice(runs_on={self.runs_on!r}, kept_in={self.store.kept_in!r})"

    @property
    def default(self) -> str:
        return self.settings.model or DEFAULT_MODEL

    @property
    def runs_on(self) -> RunsOn:
        """Where an assessment runs: the fake when a script is set or no key is."""
        if self.settings.fake_script is None and self.openrouter is not None:
            return "openrouter"
        return "fake"

    def current(self) -> str:
        """The chosen model, else the default. Raises DatabaseUnavailable."""
        return self.store.get() or self.default

    def change(self, model: str) -> str:
        """Keep `model` as the choice, once OpenRouter knows it. Raises NoKey, UnknownModel,
        CatalogueUnavailable or DatabaseUnavailable."""
        if self.openrouter is None:
            raise NoKey()
        if not self.openrouter.knows(model):
            raise UnknownModel(model)
        self.store.set(model)
        log.info("the developer model is now %s", model)
        return model

    def for_assessment(self) -> BaseChatModel:
        """A fresh model for one assessment (the fake keeps its place in the script)."""
        if self.runs_on == "fake" or self.openrouter is None:
            return fake_model(self.settings, DEMO_SCRIPT)
        try:
            model = self.current()
        except DatabaseUnavailable:
            # The assessment still runs, on the default; the developer page shows the 503.
            log.warning("the stored model can't be read; using the default %s", self.default)
            model = self.default
        return self.openrouter.chat_model(model)

    def close(self) -> None:
        if self.openrouter is not None:
            self.openrouter.close()


def model_store(db: Database | None) -> ModelStore:
    return DatabaseModelStore(db) if db is not None else MemoryModelStore()
