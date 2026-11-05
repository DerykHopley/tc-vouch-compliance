"""The spending guard (#35): each user's assessments capped per day, each one's tokens and cost.

Before an assessment starts, the user's count for the day (a UTC day) is taken against the daily
limit, DAILY_LIMIT unless a developer changed it; over it, the request is refused with the time
the count resets. The limit is read and the count taken in one step, so two requests at once
can't both slip under it. Counts, usage and the limit are kept in the database when
VOUCH_DATABASE_URL is set (vouch.database), else in memory until the server restarts. Memory
serves the fake model only: Cloud Run scales to zero, so a count in memory could reset during
the day, and a paid model needs a limit that holds (SpendingGuard.start). When the counter can't
be read, no assessment starts (fail closed): DatabaseUnavailable, a 503.

Each model call's tokens are measured (UsageMeter, a LangChain callback on the assessment's
model). Its cost is OpenRouter's reported `usage.cost` (in credits, one credit a US dollar)
when it gives one, else computed from PRICES and marked estimated. A call whose cost can't be
known (no usage, no price, or a failure carrying none) leaves the assessment's cost incomplete:
the known part stays, and nothing counts as $0. The fake model's tokens are priced as
DEFAULT_MODEL, what the run would have cost there, and totalled apart from real spend.
"""

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from vouch.database import Database, DatabaseUnavailable
from vouch.model_choice import DEFAULT_MODEL, CallBudgetSpent, KeptIn
from vouch.settings import DATABASE_URL_ENV

log = logging.getLogger(__name__)

DAILY_LIMIT = 30
"""Assessments a user may start in a UTC day (docs/requirements.md, 2026-11-03)."""
MAX_DAILY_LIMIT = 10_000
DAILY_LIMIT_SETTING = "daily_limit"
FAKE = "fake"
"""The model a usage names when the scripted fake ran."""
NO_SHARED_COUNTER = (
    "Assessments on OpenRouter need a database to hold each user's daily limit. "
    f"Set {DATABASE_URL_ENV}."
)


class NoSharedCounter(RuntimeError):
    """A paid model, with only memory to count in."""


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input_per_million: float
    output_per_million: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        spent = input_tokens * self.input_per_million + output_tokens * self.output_per_million
        return spent / 1_000_000


SONNET = "anthropic/claude-sonnet-5.5"
MISTRAL_SMALL = "mistralai/mistral-small-2603"
GPT_TERRA = "openai/gpt-6.1-terra"
PRICES = {
    DEFAULT_MODEL: Price(0.10, 0.50),
    SONNET: Price(2.00, 10.00),
    MISTRAL_SMALL: Price(0.15, 0.60),
    GPT_TERRA: Price(1.00, 5.00),
}
"""For a cost OpenRouter doesn't report: each model's list price, its cheapest standard
endpoint (not a discounted "flex" or "batch" one), from OpenRouter's model list
(openrouter.ai/api/v1/models and each model's `/endpoints`). Haiku 5.5 read 2026-11-04, the
others 2026-11-05 (#53). EU in-region endpoints charge more: 10% for Haiku ($0.11/$0.55),
Sonnet ($2.20/$11) and Mistral Small 4 ($0.165/$0.66), 20% for GPT-6.1 Terra (Azure EU,
$1.20/$6). These are the eval runner's hosted models and judge (vouch.eval_models), which the
developer page can also pick. A model missing here has no computed cost."""


@dataclass(frozen=True)
class Usage:
    """One assessment's tokens and cost: OpenRouter's reported part and the computed part.
    `cost_complete` is False when some call's cost couldn't be known."""

    model: str
    input_tokens: int
    output_tokens: int
    reported_cost_usd: float
    estimated_cost_usd: float
    estimated: bool
    cost_complete: bool

    @property
    def cost_usd(self) -> float:
        return self.reported_cost_usd + self.estimated_cost_usd

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"cost_usd": self.cost_usd}


@dataclass(frozen=True)
class UsageRecord:
    """A usage, kept: whose assessment of which control, and when it ended."""

    user_id: str
    control_id: str
    usage: Usage
    at: datetime


@dataclass(frozen=True)
class RunTotals:
    """Some assessments' usage added up; `incomplete` of them have a cost not wholly known."""

    assessments: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reported_cost_usd: float = 0.0
    estimated_cost_usd: float = 0.0
    incomplete: int = 0

    def add(self, usage: Usage) -> "RunTotals":
        return RunTotals(
            self.assessments + 1,
            self.input_tokens + usage.input_tokens,
            self.output_tokens + usage.output_tokens,
            self.reported_cost_usd + usage.reported_cost_usd,
            self.estimated_cost_usd + usage.estimated_cost_usd,
            self.incomplete + (not usage.cost_complete),
        )


@dataclass(frozen=True)
class UsageTotals:
    """Every assessment's usage, as the developer page shows it: real spend on OpenRouter, and
    the fake model's estimates apart, so they never blur the spending figure."""

    openrouter: RunTotals = field(default_factory=RunTotals)
    fake: RunTotals = field(default_factory=RunTotals)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class UsageMeter(BaseCallbackHandler):
    """Adds up the tokens and cost of every model call of one assessment."""

    def __init__(self, model: str | None, price: Price | None = None) -> None:
        """`model`: the OpenRouter id the assessment runs on; None for the fake. `price`: the
        model's price when PRICES doesn't list it (the eval runner's model list)."""
        self.model = model or FAKE
        self._price = PRICES.get(model or DEFAULT_MODEL) or price
        self._lock = threading.Lock()
        self._input = self._output = 0
        self._reported = self._computed = 0.0
        self._estimated = False
        self._complete = True

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        tokens = _tokens(response)
        reported = ((response.llm_output or {}).get("token_usage") or {}).get("cost")
        self._add(tokens, reported)

    def on_llm_error(
        self, error: BaseException, *, response: LLMResult | None = None, **kwargs: Any
    ) -> None:
        """A failed call may still have been billed: what its error's body carries is counted;
        carrying nothing, the assessment's cost is incomplete. A call the call budget stopped
        was never sent."""
        if isinstance(error, CallBudgetSpent):
            return
        usage = _error_usage(response)
        if usage is None:
            with self._lock:
                self._complete = False
            return
        tokens = (usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0)
        self._add(tokens, usage.get("cost"))

    def _add(self, tokens: tuple[int, int] | None, reported: Any) -> None:
        with self._lock:
            if tokens is not None:
                self._input += tokens[0]
                self._output += tokens[1]
            if isinstance(reported, int | float) and not isinstance(reported, bool):
                self._reported += reported
            elif tokens is not None and self._price is not None:
                self._estimated = True
                self._computed += self._price.cost(*tokens)
            else:  # no cost reported, and no tokens or no price to compute one
                self._complete = False

    def usage(self) -> Usage:
        with self._lock:
            return Usage(
                self.model,
                self._input,
                self._output,
                self._reported,
                self._computed,
                self._estimated,
                self._complete,
            )


def _tokens(response: LLMResult) -> tuple[int, int] | None:
    """The call's input and output tokens; None when it reports none."""
    found = None
    for generations in response.generations:
        for generation in generations:
            meta = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if meta:
                found = found or (0, 0)
                found = (found[0] + meta["input_tokens"], found[1] + meta["output_tokens"])
    return found


def _error_usage(response: LLMResult | None) -> dict[str, Any] | None:
    """The `usage` in a failed call's response body (langchain-core puts it in the
    generation's response_metadata), if it carries one."""
    for generations in response.generations if response else []:
        for generation in generations:
            metadata = getattr(getattr(generation, "message", None), "response_metadata", {})
            body = metadata.get("body")
            usage = body.get("usage") if isinstance(body, dict) else None
            if isinstance(usage, dict):
                return usage
    return None


class SpendingStore(Protocol):
    """Where counts, usage and the daily limit are kept. Every method may raise
    DatabaseUnavailable."""

    kept_in: KeptIn

    def daily_limit(self) -> int: ...

    def set_daily_limit(self, limit: int) -> None: ...

    def take(self, user_id: str, day: date) -> tuple[bool, int]:
        """Count one assessment for the user's day if they're under the daily limit, the limit
        read in the same step: (counted, the limit)."""
        ...

    def record(self, record: UsageRecord) -> None: ...

    def totals(self) -> UsageTotals: ...


class MemorySpendingStore:
    """No database: kept until the server restarts, for the fake model only."""

    kept_in: KeptIn = "memory"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._limit = DAILY_LIMIT
        self._day: date | None = None
        self._counts: dict[str, int] = {}
        self._records: list[UsageRecord] = []

    def daily_limit(self) -> int:
        return self._limit

    def set_daily_limit(self, limit: int) -> None:
        self._limit = limit

    def take(self, user_id: str, day: date) -> tuple[bool, int]:
        with self._lock:
            if day != self._day:  # a new day: yesterday's counts go
                self._day, self._counts = day, {}
            count = self._counts.get(user_id, 0)
            if count >= self._limit:
                return False, self._limit
            self._counts[user_id] = count + 1
            return True, self._limit

    def record(self, record: UsageRecord) -> None:
        with self._lock:
            self._records.append(record)

    def records(self) -> list[UsageRecord]:
        with self._lock:
            return list(self._records)

    def totals(self) -> UsageTotals:
        openrouter, fake = RunTotals(), RunTotals()
        for record in self.records():
            if record.usage.model == FAKE:
                fake = fake.add(record.usage)
            else:
                openrouter = openrouter.add(record.usage)
        return UsageTotals(openrouter, fake)


class DatabaseSpendingStore:
    """In the database's tables, shared by every instance and restart (vouch.database)."""

    kept_in: KeptIn = "database"

    def __init__(self, db: Database) -> None:
        self.db = db

    def daily_limit(self) -> int:
        stored = self.db.setting(DAILY_LIMIT_SETTING)
        return int(stored) if stored and stored.isdigit() else DAILY_LIMIT

    def set_daily_limit(self, limit: int) -> None:
        self.db.set_setting(DAILY_LIMIT_SETTING, str(limit))

    def take(self, user_id: str, day: date) -> tuple[bool, int]:
        return self.db.take_assessment(user_id, day, DAILY_LIMIT_SETTING, DAILY_LIMIT)

    def record(self, record: UsageRecord) -> None:
        usage = record.usage
        self.db.record_usage(
            user_id=record.user_id,
            control_id=record.control_id,
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            reported_cost_usd=usage.reported_cost_usd,
            estimated_cost_usd=usage.estimated_cost_usd,
            estimated=usage.estimated,
            cost_complete=usage.cost_complete,
            at=record.at,
        )

    def totals(self) -> UsageTotals:
        rows = self.db.usage_totals(fake_model=FAKE)
        return UsageTotals(
            openrouter=RunTotals(**rows["openrouter"]) if "openrouter" in rows else RunTotals(),
            fake=RunTotals(**rows["fake"]) if "fake" in rows else RunTotals(),
        )


def spending_store(db: Database | None) -> SpendingStore:
    return DatabaseSpendingStore(db) if db is not None else MemorySpendingStore()


class DailyLimitReached(Exception):
    """The user has started `limit` assessments today; the count resets at `resets_at`."""

    def __init__(self, limit: int, resets_at: datetime, now: datetime) -> None:
        super().__init__(f"daily limit of {limit} reached")
        self.limit = limit
        self.resets_at = resets_at
        self.retry_after = max(1, math.ceil((resets_at - now).total_seconds()))

    def message(self) -> str:
        noun = "assessment" if self.limit == 1 else "assessments"
        when = f"{self.resets_at:%H:%M} UTC on {self.resets_at.day} {self.resets_at:%B %Y}"
        return f"You have reached today's limit of {self.limit} {noun}. It resets at {when}."


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SpendingGuard:
    """The daily limit and usage, for the API; one per app. `clock` is the seam tests fix."""

    def __init__(self, store: SpendingStore, clock: Callable[[], datetime] = _utc_now) -> None:
        self.store = store
        self.clock = clock

    def start(self, user_id: str, paid: bool) -> None:
        """Count one assessment for the user; `paid`: it runs on OpenRouter. Raises
        NoSharedCounter (a paid model, counted only in memory), DailyLimitReached, or
        DatabaseUnavailable: then no assessment starts."""
        if paid and self.store.kept_in == "memory":
            raise NoSharedCounter(NO_SHARED_COUNTER)
        now = self.clock().astimezone(UTC)
        taken, limit = self.store.take(user_id, now.date())
        if not taken:
            resets_at = datetime.combine(now.date() + timedelta(days=1), time(), tzinfo=UTC)
            log.info("a user reached the daily limit of %d assessments", limit)
            raise DailyLimitReached(limit, resets_at, now)

    def set_daily_limit(self, limit: int) -> None:
        """Raises DatabaseUnavailable."""
        self.store.set_daily_limit(limit)
        log.info("the daily limit is now %d assessments", limit)

    def record(self, user_id: str, control_id: str, usage: Usage) -> None:
        """Keep the assessment's usage; when the database can't take it, the log says so and
        the assessment's answer still stands."""
        try:
            self.store.record(UsageRecord(user_id, control_id, usage, self.clock()))
        except DatabaseUnavailable:
            log.warning("the usage of an assessment of %s could not be kept", control_id)

    def settings(self) -> dict[str, Any]:
        """What the developer page shows. Raises DatabaseUnavailable."""
        return {"daily_limit": self.store.daily_limit(), "usage": self.store.totals().to_dict()}
