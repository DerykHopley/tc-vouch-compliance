"""The spending guard (#35): each user's assessments capped per day, each one's tokens and cost.

Before an assessment starts, the user's count for the day (a UTC day) is taken against the daily
limit, DAILY_LIMIT unless a developer changed it; over it, the request is refused with the time
the count resets. The count is taken in one step, so two requests at once can't both slip under
the limit. Counts, usage and the limit are kept in the database when VOUCH_DATABASE_URL is set
(vouch.database), else in memory until the server restarts, as the model choice is. When the
counter can't be read, no assessment starts (fail closed): DatabaseUnavailable, a 503.

Each model call's tokens are measured (UsageMeter, a LangChain callback on the assessment's
model). Its cost is OpenRouter's reported `usage.cost` (in credits, one credit a US dollar)
when it gives one, else computed from PRICES and marked estimated. The fake model's tokens are
priced as DEFAULT_MODEL: what the run would have cost there.
"""

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from vouch.database import Database, DatabaseUnavailable
from vouch.model_choice import DEFAULT_MODEL, KeptIn

log = logging.getLogger(__name__)

DAILY_LIMIT = 30
"""Assessments a user may start in a UTC day (docs/requirements.md, 2026-11-03)."""
MAX_DAILY_LIMIT = 10_000
DAILY_LIMIT_SETTING = "daily_limit"
FAKE = "fake"
"""The model a usage names when the scripted fake ran."""


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input_per_million: float
    output_per_million: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        spent = input_tokens * self.input_per_million + output_tokens * self.output_per_million
        return spent / 1_000_000


PRICES = {DEFAULT_MODEL: Price(0.10, 0.50)}
"""For a cost OpenRouter doesn't report. From openrouter.ai/anthropic/claude-haiku-5.5,
2026-11-04: $0.10/M input, $0.50/M output. A model missing here has no computed cost."""


@dataclass(frozen=True)
class Usage:
    """One assessment's tokens and cost; `cost_usd` None when no price is known."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    estimated: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Spent:
    """A usage, kept: whose assessment of which control, and when it ended."""

    user_id: str
    control_id: str
    usage: Usage
    at: datetime


class UsageMeter(BaseCallbackHandler):
    """Adds up the tokens and cost of every model call of one assessment."""

    def __init__(self, model: str | None) -> None:
        """`model`: the OpenRouter id the assessment runs on; None for the fake."""
        self.model = model or FAKE
        self._price = PRICES.get(model or DEFAULT_MODEL)
        self._lock = threading.Lock()
        self._input = self._output = 0
        self._cost = 0.0
        self._estimated = self._unpriced = False

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        input_tokens = output_tokens = 0
        for generations in response.generations:
            for generation in generations:
                meta = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if meta:
                    input_tokens += meta["input_tokens"]
                    output_tokens += meta["output_tokens"]
        reported = ((response.llm_output or {}).get("token_usage") or {}).get("cost")
        with self._lock:
            self._input += input_tokens
            self._output += output_tokens
            if isinstance(reported, int | float) and not isinstance(reported, bool):
                self._cost += reported
                return
            self._estimated = True
            if self._price is None:
                self._unpriced = True
            else:
                self._cost += self._price.cost(input_tokens, output_tokens)

    def usage(self) -> Usage:
        with self._lock:
            cost = None if self._unpriced else self._cost
            return Usage(self.model, self._input, self._output, cost, self._estimated)


def no_usage() -> dict[str, Any]:
    return {
        "assessments": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "estimated": False,
        "unpriced": 0,
    }


class SpendingStore(Protocol):
    """Where counts, usage and the daily limit are kept. Every method may raise
    DatabaseUnavailable."""

    kept_in: KeptIn

    def daily_limit(self) -> int: ...

    def set_daily_limit(self, limit: int) -> None: ...

    def take(self, user_id: str, day: date, limit: int) -> bool:
        """Count one assessment for the user's day, if they're under `limit`; else False."""
        ...

    def record(self, user_id: str, control_id: str, usage: Usage, at: datetime) -> None: ...

    def totals(self) -> dict[str, Any]:
        """Every user's usage added up, as the developer page shows it."""
        ...


class MemorySpendingStore:
    """No database: kept until the server restarts, which only ever lets a user in sooner. One
    Cloud Run instance at most (spec #3), so one process holds every count."""

    kept_in: KeptIn = "memory"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._limit: int | None = None
        self._counts: dict[tuple[str, date], int] = {}
        self._spent: list[Spent] = []

    def daily_limit(self) -> int:
        return self._limit or DAILY_LIMIT

    def set_daily_limit(self, limit: int) -> None:
        self._limit = limit

    def take(self, user_id: str, day: date, limit: int) -> bool:
        with self._lock:
            self._counts = {k: v for k, v in self._counts.items() if k[1] >= day}  # past days
            count = self._counts.get((user_id, day), 0)
            if count >= limit:
                return False
            self._counts[(user_id, day)] = count + 1
            return True

    def record(self, user_id: str, control_id: str, usage: Usage, at: datetime) -> None:
        with self._lock:
            self._spent.append(Spent(user_id, control_id, usage, at))

    def records(self) -> list[Spent]:
        with self._lock:
            return list(self._spent)

    def totals(self) -> dict[str, Any]:
        totals = no_usage()
        for spent in self.records():
            usage = spent.usage
            totals["assessments"] += 1
            totals["input_tokens"] += usage.input_tokens
            totals["output_tokens"] += usage.output_tokens
            totals["estimated"] |= usage.estimated
            if usage.cost_usd is None:
                totals["unpriced"] += 1
            else:
                totals["cost_usd"] += usage.cost_usd
        return totals


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

    def take(self, user_id: str, day: date, limit: int) -> bool:
        return self.db.take_assessment(user_id, day, limit)

    def record(self, user_id: str, control_id: str, usage: Usage, at: datetime) -> None:
        self.db.record_usage(user_id=user_id, control_id=control_id, at=at, **usage.to_dict())

    def totals(self) -> dict[str, Any]:
        return self.db.usage_totals()


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

    def start(self, user_id: str) -> None:
        """Count one assessment for the user. Raises DailyLimitReached, or DatabaseUnavailable
        (then no assessment starts)."""
        now = self.clock().astimezone(UTC)
        limit = self.store.daily_limit()
        if not self.store.take(user_id, now.date(), limit):
            resets_at = datetime.combine(now.date() + timedelta(days=1), time(), tzinfo=UTC)
            log.info("a user reached the daily limit of %d assessments", limit)
            raise DailyLimitReached(limit, resets_at, now)

    def record(self, user_id: str, control_id: str, usage: Usage) -> None:
        """Keep the assessment's usage; when the database can't take it, the log says so and
        the assessment's answer still stands."""
        try:
            self.store.record(user_id, control_id, usage, self.clock())
        except DatabaseUnavailable:
            log.warning("the usage of an assessment of %s could not be kept", control_id)

    def settings(self) -> dict[str, Any]:
        """What the developer page shows. Raises DatabaseUnavailable."""
        return {"daily_limit": self.store.daily_limit(), "usage": self.store.totals()}
