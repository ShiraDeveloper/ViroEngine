"""The only place a provider request touches the network, behind one tiny callable.

`Transport` is `Callable[[HttpRequest], HttpResponse]`. Production uses `httpx_transport`; every
test injects a fake, so no test reaches the network. Keys travel in headers (never in a URL), and
nothing here logs a header, a body or a URL.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import time
from typing import Any, Final

import httpx

from backend.adapters.vision.errors import ProviderRejected, ProviderUnavailable
from backend.adapters.vision.rate_limit import TokenBucket
from backend.core.errors import ProviderBadResponse, ProviderInvalidKey, ProviderRateLimited

# Structural: HTTP status classes (RFC 9110).
_RATE_LIMITED: Final = 429
_AUTH_REJECTED: Final = (401, 403)
_SERVER_ERROR_FROM: Final = 500
_TIMEOUT_OR_BUSY: Final = (408, 409, 425)
_OK_FROM, _OK_TO = 200, 300


@dataclass(frozen=True)
class HttpRequest:
    """One POST. `headers` hold the credential, so the object is never logged or repr'd."""

    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    timeout: float


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = field(repr=False)


class TransportError(Exception):
    """The request did not complete (timeout, DNS, connection). The message names the failure
    class only."""


Transport = Callable[[HttpRequest], HttpResponse]


def httpx_transport(request: HttpRequest) -> HttpResponse:
    """POST with httpx. Redirects are not followed, so a credential header can never be replayed
    to another host. Raises TransportError."""
    try:
        reply = httpx.post(
            request.url,
            headers=dict(request.headers),
            content=request.body,
            timeout=request.timeout,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise TransportError(type(exc).__name__) from None
    return HttpResponse(status=reply.status_code, body=reply.content)


@dataclass
class HttpCaller:
    """Rate-limited JSON POSTs with HTTP status mapped to the error taxonomy.

    Every request first takes a token from `bucket`, sleeping (injectable) until one is free, so a
    provider's published ceiling is respected instead of discovered through 429s."""

    transport: Transport
    bucket: TokenBucket
    timeout: float
    sleep: Callable[[float], None] = time.sleep

    def post(
        self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """The decoded JSON object of a 2xx answer.

        Raises ProviderRateLimited (429), ProviderInvalidKey (401/403), ProviderUnavailable (5xx,
        timeout, network), ProviderRejected (other 4xx) and ProviderBadResponse (2xx that is not a
        JSON object)."""
        while not self.bucket.try_acquire():
            self.sleep(self.bucket.wait_seconds())
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            reply = self.transport(
                HttpRequest(url=url, headers=headers, body=body, timeout=self.timeout)
            )
        except TransportError as exc:
            raise ProviderUnavailable(f"transport failed: {exc}") from None
        status = reply.status
        if status == _RATE_LIMITED:
            raise ProviderRateLimited("provider answered 429")
        if status in _AUTH_REJECTED:
            raise ProviderInvalidKey(f"provider answered {status}")
        if status >= _SERVER_ERROR_FROM or status in _TIMEOUT_OR_BUSY:
            raise ProviderUnavailable(f"provider answered {status}")
        if not _OK_FROM <= status < _OK_TO:
            raise ProviderRejected(f"provider answered {status}")
        try:
            decoded = json.loads(reply.body)
        except ValueError:
            raise ProviderBadResponse("provider answer is not JSON") from None
        if not isinstance(decoded, dict):
            raise ProviderBadResponse("provider answer is not a JSON object")
        return decoded
