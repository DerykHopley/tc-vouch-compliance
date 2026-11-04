"""The security guard's middleware layers, outside the model (docs/requirements.md, 2026-11-03).

Layer 1, input limits: a request body over MAX_MESSAGE_BYTES is refused with 413 before any
route runs (MessageLimit); a user over RATE_REQUESTS in RATE_SECONDS is refused with 429 and
a Retry-After (RateLimit, checked by the API's `rate_limit` dependency).

Layer 2, the allow-list again: the API hands the graph its model wrapped in ToolAllowList, so
every tool call the model makes is checked before the graph can run it, whatever the graph's
own guards do.

Layer 4, no secrets or personal data out: `redact` removes keys, bearer tokens, phone numbers
and e-mail addresses beyond the profile's own from every event the API streams (a guard step
notes each removal), and from every log record, whichever logger made it (install_log_filter).
"""

import logging
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vouch.graph import SEARCH_POLICIES, SUBMIT_ANSWER

MAX_MESSAGE_BYTES = 8_192
"""A request body's limit: a control id and a note of a few hundred words fit many times over."""
MESSAGE_TOO_LONG = f"That message is too long for Vouch: at most {MAX_MESSAGE_BYTES} bytes."
RATE_REQUESTS = 60
RATE_SECONDS = 60
"""A user's request rate: one a second, well above a person working in the app (and the
browser suite, on its one local user), and far below a script flooding it."""
TOO_MANY_REQUESTS = "Too many requests. Please wait a moment and try again."

ALLOWED_TOOLS = frozenset({SEARCH_POLICIES, SUBMIT_ANSWER})
"""The compliance profile's allow-list as tool names: the policy search, and the answer. Spec M1
(#23) brings the profile's evidence sources; this then follows them."""
REFUSED_TOOL = "refused_tool"
"""A refused call's new name: no graph has a tool by it, so nothing runs, and the model still
gets its call answered ("not available")."""

PROFILE_EMAIL_DOMAINS = ("nordlicht.example",)
"""The profile's own e-mail domain: its role addresses (the security desk) are evidence, so they
stay. Spec M1's compliance profile has no domain yet; when it gets one, this follows it."""
REMOVED = "[removed]"
_REDACTIONS = (
    # Bearer tokens first, so the key inside one is named as the token it is.
    ("a bearer token", re.compile(r"\bBearer\s+(?=[\w.~+/=-]*\d)[\w.~+/=-]{16,}", re.IGNORECASE)),
    ("a key", re.compile(r"(?<![A-Za-z0-9])(?:sk|pk)[-_][A-Za-z0-9_-]{16,}")),
    (
        "an e-mail address",
        re.compile(r"[\w.%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})"),
    ),
    # International (+49, 0049) or national (030, 0171) numbers; dots aren't separators, so a
    # date (01.02.2026) or a version isn't one. Seven to fifteen digits, as phone numbers have.
    ("a phone number", re.compile(r"(?<![\w.,/+-])(?:\+|0)\d[\d ()/-]{5,}\d(?![\w-])")),
)


def _kept(kind: str, match: re.Match[str]) -> bool:
    """A match that isn't what it looks like: the profile's own address, a number too short."""
    if kind == "an e-mail address":
        domain = match.group(1).lower()
        return any(domain == own or domain.endswith("." + own) for own in PROFILE_EMAIL_DOMAINS)
    if kind == "a phone number":
        return not 7 <= sum(c.isdigit() for c in match.group()) <= 15
    return False


def redact(text: str) -> tuple[str, list[str]]:
    """The text with secrets and personal data replaced by REMOVED, and what kinds were found."""
    found: list[str] = []
    for kind, pattern in _REDACTIONS:

        def replace(match: re.Match[str], kind: str = kind) -> str:
            if _kept(kind, match):
                return match.group()
            if kind not in found:
                found.append(kind)
            return f"Bearer {REMOVED}" if kind == "a bearer token" else REMOVED

        text = pattern.sub(replace, text)
    return text, found


def redact_data(value: Any) -> tuple[Any, list[str]]:
    """`redact` through every string in a JSON-like value (dicts, lists, strings)."""
    if isinstance(value, str):
        return redact(value)
    found: list[str] = []
    if isinstance(value, dict):
        clean: Any = {}
        for key, item in value.items():
            clean[key], kinds = redact_data(item)
            found += [k for k in kinds if k not in found]
        return clean, found
    if isinstance(value, list):
        items = []
        for item in value:
            item, kinds = redact_data(item)
            items.append(item)
            found += [k for k in kinds if k not in found]
        return items, found
    return value, found


def removal_step(kinds: list[str], where: str) -> str:
    """The guard step's sentence for what was removed from `where`."""
    named = kinds[0] if len(kinds) == 1 else ", ".join(kinds[:-1]) + " and " + kinds[-1]
    return f"Removed {named} from the {where}."


def install_log_filter() -> None:
    """Make every log record redacted from now on, whichever logger makes it.

    A record factory, not a handler filter: it holds for uvicorn's handlers, a library's
    debug lines and a test's capture alike. The message is formatted and redacted once, and
    a traceback is formatted and redacted before any handler sees it. Installing twice is once.
    """
    make = logging.getLogRecordFactory()
    if getattr(make, "redacting", False):
        return

    def redacting(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = make(*args, **kwargs)
        try:
            message = record.getMessage()
        except (TypeError, ValueError):  # a bad format string: keep its parts, redacted alone
            message = f"{record.msg} {record.args}"
        record.msg, record.args = redact(message)[0], None
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))[0]
        if record.stack_info:
            record.stack_info = redact(record.stack_info)[0]
        return record

    redacting.redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(redacting)


class MessageLimit:
    """ASGI middleware: a body over the limit gets 413 before the app sees the request.

    The body is read here, at most one byte over the limit, then handed to the app as it came.
    A declared Content-Length over the limit is refused without reading anything.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_MESSAGE_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await _too_long(scope, receive, send)
            return
        body = bytearray()
        last: Message = {"type": "http.request", "body": b"", "more_body": False}
        while True:
            message = await receive()
            if message["type"] != "http.request":  # the client went away mid-body
                last = message
                break
            body += message.get("body", b"")
            if len(body) > self.max_bytes:
                await _too_long(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()  # after the body: the disconnect a stream listens for
            replayed = True
            if last["type"] != "http.request":
                return last
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


class RateLimit:
    """At most `requests` per user in any `per_seconds` (a sliding window), in memory.

    One Cloud Run instance at most (spec #3), so one process holds every user's count; a
    restart forgets them, which only ever lets a user in sooner.
    """

    def __init__(
        self,
        requests: int = RATE_REQUESTS,
        per_seconds: float = RATE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.requests = requests
        self.per_seconds = per_seconds
        self._clock = clock
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def wait_for(self, user_id: str) -> int | None:
        """None, and the request counted; or the whole seconds until the user may ask again."""
        now = self._clock()
        with self._lock:
            for uid in [
                uid for uid, times in self._seen.items() if times[-1] <= now - self.per_seconds
            ]:
                del self._seen[uid]  # nobody seen this window: forget them, so memory stays small
            times = self._seen.setdefault(user_id, deque())
            while times and times[0] <= now - self.per_seconds:
                times.popleft()
            if len(times) >= self.requests:
                return max(1, math.ceil(times[0] + self.per_seconds - now))
            times.append(now)
            return None


async def _too_long(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse({"detail": MESSAGE_TOO_LONG}, status_code=413)(scope, receive, send)


class ToolAllowList:
    """The model, with every tool call it makes checked against `allowed` (security layer 2).

    A call outside it is renamed REFUSED_TOOL before the graph sees it, so the graph replies
    that the tool isn't available and runs nothing. Each refusal leaves a guard step's
    sentence, which the API streams (take_refusals).
    """

    def __init__(self, model: BaseChatModel, allowed: frozenset[str]) -> None:
        self._model = model
        self._allowed = allowed
        self._refusals: list[str] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:
        return self._model.bind_tools(tools, **kwargs) | RunnableLambda(self._checked)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:
        """Structured output (the judge) runs no tool, so there's nothing to check."""
        return self._model.with_structured_output(schema, **kwargs)

    def take_refusals(self) -> list[str]:
        """The guard steps' sentences since the last take, oldest first."""
        refusals, self._refusals = self._refusals, []
        return refusals

    def _checked(self, reply: AIMessage) -> AIMessage:
        calls = []
        for call in reply.tool_calls:
            if call["name"] in self._allowed:
                calls.append(call)
            else:
                name = _UNSAFE_NAME.sub("?", call["name"])[:64]
                self._refusals.append(f"Refused {name}: {NOT_ALLOWED}.")
                calls.append({**call, "name": REFUSED_TOOL})
        if calls == reply.tool_calls:
            return reply
        return reply.model_copy(update={"tool_calls": calls})


GUARD = "guard"
"""The node a guard step names, as the graph's own guard steps do (spec M1)."""
NOT_ALLOWED = "it isn't on the compliance profile's allow-list"
_UNSAFE_NAME = re.compile(r"[^\w.-]")
"""A tool name is the model's own text: shown in a step, it keeps to word characters."""
