"""Security layer 4: no secrets or personal data in answers or logs, outside the model.

`redact` removes the app's own secret values (remember_secrets), key and token shapes, a
password in a URL, and e-mail addresses other than the profile's own listed ones. The API runs
every streamed event through it, with a guard step naming what was removed; log lines go
through it as they are written (RedactingFormatter, install_log_redaction).
"""

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from vouch.database import _secrets_in
from vouch.graph import GUARD
from vouch.settings import Settings

REMOVED = "[removed]"
PROFILE_EMAILS = frozenset({"security@nordlicht.example"})
"""The profile's own listed addresses, kept because they are evidence (the security desk's, in
the reporting procedure). Any other address is removed, a personal one on the company's own
domain too. The compliance profile (vouch.profile) has no field for a company's listed
addresses yet, and redaction runs on every event without one; when it gets one, this list
moves there."""
MIN_SECRET_CHARS = 8
"""A remembered secret shorter than this is left to the patterns: removing every match of a
short value would remove ordinary words."""

KEY = "a key"
TOKEN = "a token"
PASSWORD = "a password"
EMAIL = "an e-mail address"


def _removed(_: re.Match[str]) -> str:
    return REMOVED


def _never(_: re.Match[str]) -> bool:
    return False


@dataclass(frozen=True)
class Redaction:
    """One kind of thing removed: its label in a guard step, what finds it, what replaces it,
    and which matches it keeps after all."""

    label: str
    pattern: re.Pattern[str]
    replacement: Callable[[re.Match[str]], str] = _removed
    keep: Callable[[re.Match[str]], bool] = _never


def _profile_email(match: re.Match[str]) -> bool:
    return match.group().lower() in PROFILE_EMAILS


REDACTIONS = (
    # A URL's password first, so `user:pass@host` isn't taken for an e-mail address.
    Redaction(
        PASSWORD,
        re.compile(r"\b([A-Za-z][A-Za-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)@"),
        lambda m: f"{m.group(1)}{REMOVED}@",
    ),
    # Bearer before the JWT, so a bearer JWT is one token removed, "Bearer" kept as written. The
    # scheme name is case-insensitive. After "Authorization:" anything is a token; elsewhere only
    # a token-like word (a digit, or 16+ characters), so prose ("Bearer instruments") is kept.
    Redaction(
        TOKEN,
        re.compile(r"(?i)\b(authorization:\s*bearer)\s+[\w.~+/=-]{4,}"),
        lambda m: f"{m.group(1)} {REMOVED}",
    ),
    Redaction(
        TOKEN,
        re.compile(r"(?i)\b(bearer)\s+(?:(?=[\w.~+/=-]*\d)[\w.~+/=-]{4,}|[\w.~+/=-]{16,})"),
        lambda m: f"{m.group(1)} {REMOVED}",
    ),
    Redaction(TOKEN, re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+")),
    Redaction(
        KEY,
        re.compile(
            r"(?<![A-Za-z0-9])(?:"
            r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9_]*"  # Stripe and Clerk shapes, any length
            r"|(?:sk|pk)[-_][A-Za-z0-9_-]{16,}"  # OpenRouter, OpenAI, Anthropic and the like
            r"|sk-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{6,}"  # a shorter sk- key, with a digit (#115)
            r"|gh[pousr]_[A-Za-z0-9]{20,}"  # GitHub tokens: classic, OAuth, user, server, refresh
            r"|github_pat_[A-Za-z0-9_]{20,}"  # GitHub fine-grained tokens
            r"|whsec_[A-Za-z0-9+/=_-]+"  # webhook signing secrets
            r")"
        ),
    ),
    # AWS access key ids: upper case as issued, matched in any case like the other non-key rows.
    Redaction(KEY, re.compile(r"(?i)(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])")),
    Redaction(
        EMAIL,
        re.compile(r"[\w.%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
        keep=_profile_email,
    ),
)

_secrets: set[str] = set()
_secrets_lock = threading.Lock()
_known: Redaction | None = None
"""The app's own secret values as one Redaction; None until remember_secrets has one."""


def remember_secrets(settings: Settings) -> None:
    """Remove `settings`' own secret values exactly from now on, whatever their shape: the
    Clerk secret and JWT keys, the OpenRouter key, the database URL and its password (as
    vouch.database finds it, written and decoded). The app does this when it's made."""
    global _known
    values: list[str] = []
    if settings.clerk is not None:
        values += [settings.clerk.secret_key.get_secret_value()]
        values += [settings.clerk.jwt_key.get_secret_value()]
    if settings.openrouter_api_key is not None:
        values.append(settings.openrouter_api_key.get_secret_value())
    if settings.database_url is not None:
        values += _secrets_in(settings.database_url.get_secret_value())
    with _secrets_lock:
        _secrets.update(v for v in values if len(v) >= MIN_SECRET_CHARS)
        if _secrets:
            longest_first = sorted(_secrets, key=len, reverse=True)
            pattern = re.compile("|".join(re.escape(v) for v in longest_first))
            _known = Redaction(KEY, pattern)


def redact(text: str) -> tuple[str, list[str]]:
    """The text with secrets and personal data replaced, and the labels of what was removed."""
    found: list[str] = []
    known = (_known,) if _known is not None else ()
    for redaction in (*known, *REDACTIONS):

        def replace(match: re.Match[str], redaction: Redaction = redaction) -> str:
            if redaction.keep(match):
                return match.group()
            _merge(found, [redaction.label])
            return redaction.replacement(match)

        text = redaction.pattern.sub(replace, text)
    return text, found


def redact_data(value: Any) -> tuple[Any, list[str]]:
    """`redact` through every string in a JSON-like value (dicts, their keys too, lists, tuples,
    sets, strings). A tuple, set or frozenset comes back a list, as JSON has it:
    `dataclasses.asdict` keeps tuples (the citations on an Assessment's requirements); a set's
    order is its own (#298)."""
    if isinstance(value, str):
        return redact(value)
    found: list[str] = []
    if isinstance(value, dict):
        clean: dict[Any, Any] = {}
        for key, item in value.items():
            clean_key, key_labels = redact(key) if isinstance(key, str) else (key, [])
            clean[clean_key], labels = redact_data(item)
            _merge(found, key_labels)
            _merge(found, labels)
        return clean, found
    if isinstance(value, list | tuple | set | frozenset):
        items = []
        for item in value:
            clean_item, labels = redact_data(item)
            items.append(clean_item)
            _merge(found, labels)
        return items, found
    return value, found


def _merge(found: list[str], labels: list[str]) -> None:
    """Add the labels not yet found, keeping the order they were first found in."""
    found += [label for label in labels if label not in found]


def removal_step(labels: list[str], where: str) -> str:
    """The guard step's sentence for what was removed from `where`."""
    named = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
    return f"Removed {named} from the {where}."


class RedactingFormatter(logging.Formatter):
    """Another formatter's whole line, traceback and `extra=` fields included, redacted. The
    record itself is left as its logger made it, so other formatters read it unchanged."""

    def __init__(self, inner: logging.Formatter | None = None) -> None:
        super().__init__()
        self.inner = inner or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        return redact(self.inner.format(record))[0]


LOGGERS_WITH_HANDLERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access")
"""The root logger and uvicorn's: where the served app's log lines are written."""


def install_log_redaction() -> None:
    """Wrap the formatter of every handler now on the root and uvicorn loggers, and Python's
    last-resort handler, in a RedactingFormatter; a handler wrapped already is left alone.

    It covers the handlers present when it runs: vouch.api runs it once, on import, which is
    after uvicorn has configured its logging (uvicorn's Config does that before loading the
    app). A handler added later isn't covered.
    """
    handlers = [h for name in LOGGERS_WITH_HANDLERS for h in logging.getLogger(name).handlers]
    if logging.lastResort is not None:
        handlers.append(logging.lastResort)
    for handler in handlers:
        if not isinstance(handler.formatter, RedactingFormatter):
            handler.setFormatter(RedactingFormatter(handler.formatter))


REMOVED_FROM = {"result": "assessment"}
"""What a guard step calls the event something was removed from, when not the event's name."""


def redacted_event(event: str, data: Any) -> list[tuple[str, Any]]:
    """An event as it may leave Vouch: its data with secrets and personal data removed
    (security layer 4), after a guard step saying what was removed, if anything was."""
    clean, removed = redact_data(data)
    if not removed:
        return [(event, clean)]
    said = removal_step(removed, REMOVED_FROM.get(event, event))
    return [("step", {"node": GUARD, "text": said}), (event, clean)]
