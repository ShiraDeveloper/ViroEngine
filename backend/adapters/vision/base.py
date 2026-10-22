"""What every provider adapter shares: the reply shape, the call contract and credential rotation.

A provider turns one rendered prompt into text. It never validates the text (the chain does, with
the strict schema), never retries (the chain does) and never decides what happens next when it
fails (the chain fails over). Its one job beyond building the request is rotating credentials.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any, Final, Protocol

from backend.adapters.vision.errors import ProviderUnavailable
from backend.adapters.vision.prompt import RenderedPrompt
from backend.adapters.vision.ring import Credential, CredentialRing
from backend.adapters.vision.transport import HttpCaller
from backend.core.errors import ProviderInvalidKey, ProviderRateLimited
from backend.core.models import ReaderName

# Structural: a model id becomes part of a URL path, so it is limited to what real ids contain.
_MODEL_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True)
class ProviderReply:
    """The model's text and the usage the provider reported (0 when it reported none)."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class Provider(Protocol):
    """One cloud vision provider behind the chain."""

    name: ReaderName
    ring: CredentialRing

    @property
    def model_id(self) -> str:
        """The configured model id; empty when the provider has none (then it is unavailable)."""
        ...

    def available(self) -> bool:
        """True when at least one live credential and a model id are configured."""
        ...

    def generate(
        self, prompt: RenderedPrompt, schema: dict[str, Any], *, caller: HttpCaller
    ) -> ProviderReply:
        """One request. Rotates credentials on 429/401/403 and raises ProviderRateLimited or
        ProviderInvalidKey when none is left, ProviderUnavailable/ProviderRejected/
        ProviderBadResponse as the transport reports."""
        ...


def check_model_id(model: str) -> str:
    """The id unchanged. Raises ValueError for anything that could alter a URL path."""
    if not _MODEL_ID.fullmatch(model):
        raise ValueError("model id contains characters a model id never has")
    return model


def png_b64(data: bytes) -> str:
    """Standard base64 of an image, as every provider's inline-image field expects."""
    return base64.b64encode(data).decode("ascii")


def with_ring[T](ring: CredentialRing, attempt: Callable[[Credential], T]) -> T:
    """Run `attempt` with the first usable credential, moving on after a 429 (the credential
    cools) or a 401/403 (it dies). Raises the last such error when every credential has been
    tried, ProviderRateLimited when all are cooling and ProviderInvalidKey when all are dead."""
    last: ProviderRateLimited | ProviderInvalidKey | None = None
    while (cred := ring.next()) is not None:
        try:
            return attempt(cred)
        except ProviderRateLimited as exc:
            ring.cool(cred)
            last = exc
        except ProviderInvalidKey as exc:
            ring.kill(cred)
            last = exc
    if last is not None:
        raise last
    if ring.configured == 0:
        raise ProviderUnavailable("no credential configured")
    if ring.labels:
        raise ProviderRateLimited("every credential is cooling")
    raise ProviderInvalidKey("every credential was rejected")
