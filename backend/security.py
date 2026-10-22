"""HTTP-boundary security controls (PLAN §8: T-5, T-7, T-9).

Three pieces, all small and testable on their own:

* `SecurityHeadersMiddleware` - the response headers every answer carries (clickjacking, MIME
  sniffing, referrer leak, CSP, HSTS over TLS);
* `BodyLimitMiddleware` - refuses an oversized body before it is buffered, both by the declared
  `Content-Length` and by counting a chunked stream;
* `SlidingWindow` / `RateLimits` - a per-client request budget with an injectable clock.

The middlewares are plain ASGI (not `BaseHTTPMiddleware`), so they never buffer a body or a stream
and Server-Sent Events pass through untouched.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, MutableMapping
import math
import time
from typing import Any, Final

from fastapi import HTTPException, Request

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# Structural: headers sent on every response (PLAN §8 T-9).
STATIC_HEADERS: Final = {
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "permissions-policy": "geolocation=(), camera=(), microphone=()",
    "x-xss-protection": "1; mode=block",
    "content-security-policy": "default-src 'self'",
}
# Structural: the interactive API docs load their UI from a CDN, so they get a CSP that allows it.
# Everything else, which never serves HTML, keeps `default-src 'self'`.
_DOCS_PATHS: Final = ("/v1/docs", "/v1/redoc")
_DOCS_CSP: Final = (
    "default-src 'self'; img-src 'self' data: https://fastapi.tiangolo.com; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net"
)
# Structural: one year, the usual HSTS lifetime; sent only when the request came over TLS.
_HSTS: Final = "max-age=31536000; includeSubDomains"
_BYTES_PER_MB: Final = 1024 * 1024
# Structural: allowance on top of MAX_UPLOAD_MB for multipart framing and form fields (PLAN T27).
BODY_OVERHEAD_MB: Final = 2
_SECONDS_PER_MINUTE: Final = 60.0


def _is_tls(scope: Scope) -> bool:
    if scope.get("scheme") in ("https", "wss"):
        return True
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-proto" and value.split(b",")[0].strip() == b"https":
            return True
    return False


class SecurityHeadersMiddleware:
    """Adds the standard security headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        docs = scope.get("path", "").startswith(_DOCS_PATHS)
        tls = _is_tls(scope)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = {k.decode("latin-1").lower(): v for k, v in message.get("headers", [])}
                added = dict(STATIC_HEADERS)
                if docs:
                    added["content-security-policy"] = _DOCS_CSP
                if tls:
                    added["strict-transport-security"] = _HSTS
                for name, value in added.items():
                    headers[name] = value.encode("latin-1")
                message = {
                    **message,
                    "headers": [(k.encode("latin-1"), v) for k, v in headers.items()],
                }
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodyLimitMiddleware:
    """Answers 413 for a request body above `max_bytes` without reading it all.

    A declared `Content-Length` over the limit is refused at once. A body with no declared length
    is counted as it streams and cut off at the limit, so a client cannot dodge the check by
    chunking. The 413 body matches the application's error shape."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = next(
            (int(v) for k, v in scope.get("headers", []) if k == b"content-length" and v.isdigit()),
            None,
        )
        if declared is not None and declared > self.max_bytes:
            await self._refuse(send)
            return
        seen = 0
        refused = False

        async def counting_receive() -> Message:
            nonlocal seen, refused
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    refused = True
                    return {"type": "http.disconnect"}
            return message

        started = False

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if refused and not started:
                started = True
                await self._refuse(send)
                return
            if refused:
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        await self.app(scope, counting_receive, guarded_send)

    @staticmethod
    async def _refuse(send: Send) -> None:
        body = b'{"error":"TOO_LARGE","recoverable":false}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SlidingWindow:
    """At most `limit` hits per `window_seconds` per key; the clock (seconds) is injected."""

    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if limit < 1 or window_seconds <= 0:
            raise ValueError("limit must be >= 1 and window_seconds > 0")
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> float | None:
        """Record a request. None when it is allowed, else the seconds until it would be."""
        now = self._clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return self._window - (now - hits[0])
        hits.append(now)
        return None

    def reset(self) -> None:
        """Forget every client (tests)."""
        self._hits.clear()


class RateLimits:
    """The two budgets of PLAN §8 T-7: all requests, and uploads on top of that."""

    def __init__(self, per_minute: int, uploads_per_minute: int) -> None:
        self.requests = SlidingWindow(per_minute, _SECONDS_PER_MINUTE)
        self.uploads = SlidingWindow(uploads_per_minute, _SECONDS_PER_MINUTE)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _refuse(retry_after: float) -> HTTPException:
    return HTTPException(
        status_code=429,
        headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        detail={"error": "RATE_LIMITED", "recoverable": True},
    )


# Structural: probes must keep working when a client has used its budget.
_UNLIMITED: Final = ("/v1/health",)


async def limit_requests(request: Request) -> None:
    """App-wide dependency: 429 + Retry-After once a client exceeds its per-minute budget."""
    if request.url.path in _UNLIMITED:
        return
    limits: RateLimits = request.app.state.rate_limits
    wait = limits.requests.hit(_client(request))
    if wait is not None:
        raise _refuse(wait)


async def limit_uploads(request: Request) -> None:
    """Upload-route dependency: the stricter budget for creating jobs."""
    limits: RateLimits = request.app.state.rate_limits
    wait = limits.uploads.hit(_client(request))
    if wait is not None:
        raise _refuse(wait)
