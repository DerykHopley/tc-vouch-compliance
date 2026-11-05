"""Security layer 1, input limits, outside the model (docs/requirements.md, 2026-11-03).

A request body over MAX_MESSAGE_BYTES is refused with 413 before any route runs (MessageLimit,
an ASGI middleware); a user over RATE_REQUESTS in RATE_SECONDS is refused with 429 and a
Retry-After (RateLimit, which the API's `rate_limit` dependency asks).
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_MESSAGE_BYTES = 8_192
"""A request body's limit: a control id and a note of a few hundred words fit many times over."""
MESSAGE_TOO_LONG = f"That message is too long for Vouch: at most {MAX_MESSAGE_BYTES} bytes."
RATE_REQUESTS = 60
RATE_SECONDS = 60
"""A user's request rate: one a second, well above a person working in the app, and far below
a script flooding it. VOUCH_RATE_REQUESTS (vouch.settings) changes the count: the browser suite,
all on local mode's one user, raises it (#138)."""
TOO_MANY_REQUESTS = "Too many requests. Please wait a moment and try again."


class MessageLimit:
    """ASGI middleware: a body over the limit gets 413 before the app sees the request.

    The body is read here, at most one chunk past the limit, then handed to the app as it came.
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


async def _too_long(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse({"detail": MESSAGE_TOO_LONG}, status_code=413)(scope, receive, send)


class RateLimit:
    """At most `requests` per user in any `per_seconds` (a sliding window), in memory.

    One Cloud Run instance at most (spec #3), so one process holds every user's count; a
    restart forgets them, which only ever lets a user in sooner. Each call trims the asking
    user's own times; once a window, a sweep forgets the users idle for a whole window.
    """

    def __init__(
        self,
        requests: int = RATE_REQUESTS,
        per_seconds: float = RATE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests < 1:
            raise ValueError(f"a rate limit needs at least 1 request, not {requests}")
        if per_seconds <= 0:
            raise ValueError(f"a rate limit's window must be positive, not {per_seconds}")
        self.requests = requests
        self.per_seconds = per_seconds
        self._clock = clock
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._swept = clock()

    def wait_for(self, user_id: str) -> int | None:
        """None, and the request counted; or the whole seconds until the user may ask again."""
        now = self._clock()
        cutoff = now - self.per_seconds
        with self._lock:
            if now - self._swept >= self.per_seconds:
                self._sweep(cutoff)
                self._swept = now
            times = self._seen.setdefault(user_id, deque())
            while times and times[0] <= cutoff:
                times.popleft()
            if len(times) >= self.requests:
                return max(1, math.ceil(times[0] - cutoff))
            times.append(now)
            return None

    def users(self) -> set[str]:
        """The users whose requests are still counted."""
        with self._lock:
            return set(self._seen)

    def _sweep(self, cutoff: float) -> None:
        idle = [uid for uid, times in self._seen.items() if not times or times[-1] <= cutoff]
        for uid in idle:
            del self._seen[uid]
