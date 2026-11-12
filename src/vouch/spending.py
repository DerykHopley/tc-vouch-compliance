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

Each call's served model is counted too (#185): the model OpenRouter's answer names, which
differs from the requested one only behind a router. The eval runner saves it; the app's usage
answer leaves it out (Usage.to_dict), so the app shows what it showed before.
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
from vouch.model_choice import DEFAULT_MODEL, STREAMED_COST, CallBudgetSpent, KeptIn
from vouch.settings import DATABASE_URL_ENV

log = logging.getLogger(__name__)

DAILY_LIMIT = 30
"""Assessments a user may start in a UTC day (docs/requirements.md, 2026-11-03)."""
MAX_DAILY_LIMIT = 10_000
DAILY_LIMIT_SETTING = "daily_limit"
FAKE = "fake"
"""The model a usage names when the scripted fake ran."""
PROFILE_PASS_DAILY_LIMIT = 3
"""Profile passes a user may run in a UTC day (#371), apart from the assessments' limit. A
developer may change it in the settings table, under PROFILE_PASS_LIMIT_SETTING, with no
deploy."""
PROFILE_PASS_LIMIT_SETTING = "profile_pass_daily_limit"
CHAT_DAILY_LIMIT = 50
"""Chat messages a user may send in a UTC day (#628, owner decision 2026-11-12), apart from the
assessments' limit; a developer changes it on the developer page."""
CHAT_LIMIT_SETTING = "chat_daily_limit"
PROFILE_PASS = "profile-pass"
"""The control a usage record names for a profile pass (#371): one record for its one run."""
CHAT = "chat"
"""The control a usage record names for a chat message (#44): no control was assessed, so the
totals count its tokens and cost, but not as an assessment."""
NOT_ASSESSMENTS = (CHAT, PROFILE_PASS)
"""The usage records the totals count in tokens and cost but not as assessments: a chat message,
and a profile pass (#371), whose assessments came from one run."""
RECENT_RUNS = 10
"""The usage records the developer page lists as its recent runs (#496)."""
NOT_RUNS = (CHAT,)
"""The usage records the recent runs leave out: chat messages; an assessment or a profile pass is
a run."""
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


HAIKU = "anthropic/claude-haiku-5.5"
SONNET = DEFAULT_MODEL
GPT_TERRA = "openai/gpt-6.1-terra"
GPT_LUNA = "openai/gpt-6-luna"
GPT_SOL = "openai/gpt-6.1-sol"
PRICES = {
    HAIKU: Price(0.10, 0.50),
    SONNET: Price(2.00, 10.00),
    GPT_TERRA: Price(1.00, 5.00),
    GPT_LUNA: Price(0.10, 0.50),
    GPT_SOL: Price(2.00, 10.00),
}
"""For a cost OpenRouter doesn't report: each model's list price, its cheapest standard
endpoint (not a discounted "flex" or "batch" one), from OpenRouter's model list
(openrouter.ai/api/v1/models and each model's `/endpoints`). Haiku 5.5 read 2026-11-04, the
others 2026-11-05 (#53; GPT-6 Luna and GPT-6.1 Sol for #55's runs). EU in-region endpoints
charge more: 10% for Haiku ($0.11/$0.55), Sonnet ($2.20/$11), GPT-6 Luna ($0.11/$0.55) and
GPT-6.1 Sol ($2.20/$11), 20% for GPT-6.1 Terra (Azure EU, $1.20/$6). These are the eval
runner's fixed-price hosted models and judge (vouch.eval_models), which the developer page can
also pick; the runner's Jev Router has no fixed price, so it isn't here (a test keeps it out).
A model missing here has no computed cost."""


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
    served_models: dict[str, int] = field(default_factory=dict, hash=False)
    """Calls per model, by the model OpenRouter's answer named as serving each; a call naming
    none isn't counted."""

    @property
    def cost_usd(self) -> float:
        return self.reported_cost_usd + self.estimated_cost_usd

    def to_dict(self) -> dict[str, Any]:
        """The app's usage answer, as it was before the served models were counted."""
        kept = asdict(self)
        del kept["served_models"]
        return kept | {"cost_usd": self.cost_usd}


@dataclass(frozen=True)
class UsageRecord:
    """A usage, kept: whose assessment of which control, and when it ended."""

    user_id: str
    control_id: str
    usage: Usage
    at: datetime

    def to_run(self) -> dict[str, Any]:
        """The record as the developer page's recent runs list it (#496): when it ended, what ran,
        and its usage; no user id."""
        usage = self.usage.to_dict()
        del usage["cost_usd"]
        return {"at": self.at.isoformat(), "control_id": self.control_id} | usage


@dataclass(frozen=True)
class RunTotals:
    """Some assessments' and chat messages' usage added up: `assessments` counts the
    assessments alone; `incomplete` is how many of the two have a cost not wholly known."""

    assessments: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reported_cost_usd: float = 0.0
    estimated_cost_usd: float = 0.0
    incomplete: int = 0

    def add(self, usage: Usage, assessment: bool = True) -> "RunTotals":
        return RunTotals(
            self.assessments + assessment,
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
        self._served: dict[str, int] = {}

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        self._add(_tokens(response), _reported_cost(response))
        served = _served_model(response)
        if served is not None:
            with self._lock:
                self._served[served] = self._served.get(served, 0) + 1

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
                dict(self._served),
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


def _reported_cost(response: LLMResult) -> Any:
    """The cost OpenRouter reported for the call: in `llm_output["token_usage"]`, or on a
    streamed call (which has no llm_output) in its generation's info, where BudgetedChatOpenAI
    keeps it from the usage chunk (#553). None when it reports none."""
    if response.llm_output:
        return (response.llm_output.get("token_usage") or {}).get("cost")
    for generations in response.generations:
        for generation in generations:
            reported = (generation.generation_info or {}).get(STREAMED_COST)
            if reported is not None:
                return reported
    return None


def _served_model(response: LLMResult) -> str | None:
    """The model the call's answer names as serving it (OpenRouter's `model` field), if any.
    langchain-openai puts it in `llm_output["model_name"]`, or on a streamed call in the
    message's `response_metadata["model_name"]`."""
    served = (response.llm_output or {}).get("model_name")
    if isinstance(served, str) and served:
        return served
    first = next((g for generations in response.generations for g in generations), None)
    metadata = getattr(getattr(first, "message", None), "response_metadata", None) or {}
    served = metadata.get("model_name")
    return served if isinstance(served, str) and served else None


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

    def under_limit(self, user_id: str, day: date) -> tuple[bool, int]:
        """Whether the user started fewer assessments that day than the daily limit, counting
        nothing: (under it, the limit)."""
        ...

    def chat_daily_limit(self) -> int: ...

    def set_chat_daily_limit(self, limit: int) -> None: ...

    def take_chat_turn(self, user_id: str, day: date) -> tuple[bool, int]:
        """Count one chat message for the user's day if they're under the chat's own limit, the
        limit read in the same step: (counted, the limit)."""
        ...

    def take_profile_pass(self, user_id: str, day: date) -> tuple[bool, int]:
        """Count one profile pass for the user's day if they're under the passes' own limit, the
        limit read in the same step: (counted, the limit)."""
        ...

    def record(self, record: UsageRecord) -> None: ...

    def totals(self) -> UsageTotals: ...

    def recent(self, limit: int) -> list[UsageRecord]:
        """The last `limit` usage records that are runs (an assessment or a profile pass, not a
        chat message, NOT_RUNS), newest first by when each ended."""
        ...


class _DailyCounter:
    """Each user's count for one UTC day against a limit, in memory; yesterday's counts go on
    the first count of a new day. The caller holds the store's lock."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._day: date | None = None
        self._counts: dict[str, int] = {}

    def take(self, user_id: str, day: date) -> tuple[bool, int]:
        if day != self._day:
            self._day, self._counts = day, {}
        count = self._counts.get(user_id, 0)
        if count >= self.limit:
            return False, self.limit
        self._counts[user_id] = count + 1
        return True, self.limit

    def under(self, user_id: str, day: date) -> tuple[bool, int]:
        count = self._counts.get(user_id, 0) if day == self._day else 0
        return count < self.limit, self.limit


class MemorySpendingStore:
    """No database: kept until the server restarts, for the fake model only."""

    kept_in: KeptIn = "memory"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._assessments = _DailyCounter(DAILY_LIMIT)
        self._passes = _DailyCounter(PROFILE_PASS_DAILY_LIMIT)
        self._chat = _DailyCounter(CHAT_DAILY_LIMIT)
        self._records: list[UsageRecord] = []

    def daily_limit(self) -> int:
        return self._assessments.limit

    def set_daily_limit(self, limit: int) -> None:
        self._assessments.limit = limit

    def take(self, user_id: str, day: date) -> tuple[bool, int]:
        with self._lock:
            return self._assessments.take(user_id, day)

    def under_limit(self, user_id: str, day: date) -> tuple[bool, int]:
        with self._lock:
            return self._assessments.under(user_id, day)

    def chat_daily_limit(self) -> int:
        return self._chat.limit

    def set_chat_daily_limit(self, limit: int) -> None:
        self._chat.limit = limit

    def take_chat_turn(self, user_id: str, day: date) -> tuple[bool, int]:
        with self._lock:
            return self._chat.take(user_id, day)

    def take_profile_pass(self, user_id: str, day: date) -> tuple[bool, int]:
        with self._lock:
            return self._passes.take(user_id, day)

    def record(self, record: UsageRecord) -> None:
        with self._lock:
            self._records.append(record)

    def records(self) -> list[UsageRecord]:
        with self._lock:
            return list(self._records)

    def totals(self) -> UsageTotals:
        openrouter, fake = RunTotals(), RunTotals()
        for record in self.records():
            assessment = record.control_id not in NOT_ASSESSMENTS
            if record.usage.model == FAKE:
                fake = fake.add(record.usage, assessment)
            else:
                openrouter = openrouter.add(record.usage, assessment)
        return UsageTotals(openrouter, fake)

    def recent(self, limit: int) -> list[UsageRecord]:
        # Newest kept first, then sorted (a stable sort): runs ending at the same moment list the
        # last kept first, as the database's `ORDER BY at DESC, id DESC` does.
        runs = [r for r in reversed(self.records()) if r.control_id not in NOT_RUNS]
        return sorted(runs, key=lambda r: r.at, reverse=True)[:limit]


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

    def under_limit(self, user_id: str, day: date) -> tuple[bool, int]:
        return self.db.under_assessment_limit(user_id, day, DAILY_LIMIT_SETTING, DAILY_LIMIT)

    def chat_daily_limit(self) -> int:
        stored = self.db.setting(CHAT_LIMIT_SETTING)
        return int(stored) if stored and stored.isdigit() else CHAT_DAILY_LIMIT

    def set_chat_daily_limit(self, limit: int) -> None:
        self.db.set_setting(CHAT_LIMIT_SETTING, str(limit))

    def take_chat_turn(self, user_id: str, day: date) -> tuple[bool, int]:
        return self.db.take_chat_turn(user_id, day, CHAT_LIMIT_SETTING, CHAT_DAILY_LIMIT)

    def take_profile_pass(self, user_id: str, day: date) -> tuple[bool, int]:
        return self.db.take_profile_pass(
            user_id, day, PROFILE_PASS_LIMIT_SETTING, PROFILE_PASS_DAILY_LIMIT
        )

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
        rows = self.db.usage_totals(fake_model=FAKE, not_assessments=NOT_ASSESSMENTS)
        return UsageTotals(
            openrouter=RunTotals(**rows["openrouter"]) if "openrouter" in rows else RunTotals(),
            fake=RunTotals(**rows["fake"]) if "fake" in rows else RunTotals(),
        )

    def recent(self, limit: int) -> list[UsageRecord]:
        return [
            UsageRecord(
                row["user_id"],
                row["control_id"],
                Usage(
                    row["model"],
                    row["input_tokens"],
                    row["output_tokens"],
                    row["reported_cost_usd"],
                    row["estimated_cost_usd"],
                    row["estimated"],
                    row["cost_complete"],
                ),
                row["at"],
            )
            for row in self.db.recent_usage(limit, leave_out=NOT_RUNS)
        ]


def spending_store(db: Database | None) -> SpendingStore:
    return DatabaseSpendingStore(db) if db is not None else MemorySpendingStore()


class DailyLimitReached(Exception):
    """The user has started `limit` assessments today; the count resets at `resets_at`."""

    noun = "assessment"
    nouns = "assessments"
    limit_name = "daily"
    """The 429's `limit`, telling the limits apart."""

    def __init__(self, limit: int, resets_at: datetime, now: datetime) -> None:
        super().__init__(f"daily limit of {limit} reached")
        self.limit = limit
        self.resets_at = resets_at
        self.retry_after = max(1, math.ceil((resets_at - now).total_seconds()))

    def message(self) -> str:
        noun = self.noun if self.limit == 1 else self.nouns
        when = f"{self.resets_at:%H:%M} UTC on {self.resets_at.day} {self.resets_at:%B %Y}"
        return f"You have reached today's limit of {self.limit} {noun}. It resets at {when}."


class ProfilePassLimitReached(DailyLimitReached):
    """The user has run `limit` profile passes today (#371); the count resets at `resets_at`."""

    noun = "profile pass"
    nouns = "profile passes"
    limit_name = "profile-pass"


class ChatLimitReached(DailyLimitReached):
    """The user has sent `limit` chat messages today (#628); the count resets at `resets_at`."""

    noun = "chat message"
    nouns = "chat messages"
    limit_name = "chat"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _reached(
    limit: int, now: datetime, kind: type[DailyLimitReached] = DailyLimitReached
) -> DailyLimitReached:
    """The limit reached at `now`; it resets at the next UTC midnight."""
    resets_at = datetime.combine(now.date() + timedelta(days=1), time(), tzinfo=UTC)
    return kind(limit, resets_at, now)


class SpendingGuard:
    """The daily limit and usage, for the API; one per app. `clock` is the seam tests fix."""

    def __init__(self, store: SpendingStore, clock: Callable[[], datetime] = _utc_now) -> None:
        self.store = store
        self.clock = clock

    def start(self, user_id: str, paid: bool) -> None:
        """Count one assessment for the user; `paid`: it runs on OpenRouter. Raises
        NoSharedCounter (a paid model, counted only in memory), DailyLimitReached, or
        DatabaseUnavailable: then no assessment starts."""
        self._take(self.store.take, user_id, paid, DailyLimitReached)

    def start_profile_pass(self, user_id: str, paid: bool) -> None:
        """Count one profile pass for the user against the passes' own daily limit (#371), never
        against the assessments'; `paid`: it runs on OpenRouter. Raises NoSharedCounter,
        ProfilePassLimitReached, or DatabaseUnavailable: then the pass doesn't run."""
        self._take(self.store.take_profile_pass, user_id, paid, ProfilePassLimitReached)

    def _take(
        self,
        take: Callable[[str, date], tuple[bool, int]],
        user_id: str,
        paid: bool,
        reached: type[DailyLimitReached],
    ) -> None:
        """Count one for the user's UTC day with `take`; over its limit, `reached`. Raises
        NoSharedCounter (a paid model, counted only in memory) before anything is taken."""
        if paid and self.store.kept_in == "memory":
            raise NoSharedCounter(NO_SHARED_COUNTER)
        now = self.clock().astimezone(UTC)
        taken, limit = take(user_id, now.date())
        if not taken:
            log.info("a user reached the daily limit of %d %s", limit, reached.nouns)
            raise _reached(limit, now, reached)

    def check(self, user_id: str, paid: bool) -> None:
        """Before a chat message (#44), whose model calls go through the spending guard too:
        refused at the assessments' limit, but nothing counted there, as chat isn't an
        assessment. Then the chat's own limit (#628): every turn, the greeting too, counts one
        against it, as each calls the model. Raises NoSharedCounter, DailyLimitReached,
        ChatLimitReached, or DatabaseUnavailable."""
        if paid and self.store.kept_in == "memory":
            raise NoSharedCounter(NO_SHARED_COUNTER)
        now = self.clock().astimezone(UTC)
        under, limit = self.store.under_limit(user_id, now.date())
        if not under:
            log.info("a user at the daily limit of %d assessments was refused a chat", limit)
            raise _reached(limit, now)
        taken, limit = self.store.take_chat_turn(user_id, now.date())
        if not taken:
            log.info("a user reached the daily limit of %d chat messages", limit)
            raise _reached(limit, now, ChatLimitReached)

    def set_daily_limit(self, limit: int) -> None:
        """Raises DatabaseUnavailable."""
        self.store.set_daily_limit(limit)
        log.info("the daily limit is now %d assessments", limit)

    def set_chat_daily_limit(self, limit: int) -> None:
        """Raises DatabaseUnavailable."""
        self.store.set_chat_daily_limit(limit)
        log.info("the chat's daily limit is now %d messages", limit)

    def record(self, user_id: str, control_id: str, usage: Usage) -> None:
        """Keep the assessment's usage, a chat message's (`control_id` CHAT), or a profile
        pass's (PROFILE_PASS); when the database can't take it, the log says so and the answer
        still stands."""
        try:
            self.store.record(UsageRecord(user_id, control_id, usage, self.clock()))
        except DatabaseUnavailable:
            log.warning("the usage of an assessment of %s could not be kept", control_id)

    def settings(self) -> dict[str, Any]:
        """What the developer page shows: the daily limits, the usage totalled, each priced
        model's list price and the recent runs (#496). Raises DatabaseUnavailable."""
        return {
            "daily_limit": self.store.daily_limit(),
            "chat_daily_limit": self.store.chat_daily_limit(),
            "usage": self.store.totals().to_dict(),
            "prices": [{"model": model} | asdict(price) for model, price in PRICES.items()],
            "recent_runs": [r.to_run() for r in self.store.recent(RECENT_RUNS)],
        }


def count_assessment(spending: SpendingGuard, user_id: str, paid: bool) -> None:
    """Count one assessment against the user's daily limit, wherever it was started (the
    working paper, the chat); `paid`: it runs on OpenRouter. Raises DailyLimitReached,
    NoSharedCounter (logged: a paid model needs the database's counter) or DatabaseUnavailable:
    then no assessment starts."""
    try:
        spending.start(user_id, paid=paid)
    except NoSharedCounter:
        log.error("assessments on OpenRouter need %s for the daily limit", DATABASE_URL_ENV)
        raise
