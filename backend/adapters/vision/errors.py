"""Errors internal to the provider adapters. None of them reaches the core: the chain turns every
one into `ProviderExhausted`, which S9 handles. Messages never carry a key, a URL or a body."""

from __future__ import annotations

from backend.core.errors import SafeLineError


class ProviderUnavailable(SafeLineError):
    """5xx, a timeout or a network failure: retried with backoff, then the provider is skipped."""

    code = "PROVIDER_UNAVAILABLE"
    recoverable = True
    http_status = 502


class ProviderRejected(SafeLineError):
    """A 4xx other than 401/403/429 (an unknown model id, a refused parameter): retrying the same
    request cannot help, so the provider is skipped at once."""

    code = "PROVIDER_REJECTED"
    recoverable = True
    http_status = 502
