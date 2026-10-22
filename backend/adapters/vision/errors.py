"""Errors internal to the provider adapters. None of them reaches the core: the chain turns every
one into `ProviderExhausted`, which S9 handles. Messages never carry a key, a URL or a body."""

from __future__ import annotations

from typing import Final

from backend.core.errors import ProviderRateLimited, SafeLineError

# Structural: how long a credential whose daily quota is spent rests before it is tried again.
# Free-tier quotas renew once a day; trying once an hour costs one request and finds the reset.
DAILY_QUOTA_COOLDOWN_SECONDS: Final = 3600.0


class ProviderUnavailable(SafeLineError):
    """5xx, a timeout or a network failure: retried with backoff, then the provider is skipped."""

    code = "PROVIDER_UNAVAILABLE"
    recoverable = True
    http_status = 502


class ProviderDailyQuota(ProviderRateLimited):
    """A 429 naming a per-day quota: a one-minute cooldown cannot help, so the credential rests
    for `DAILY_QUOTA_COOLDOWN_SECONDS` and the chain moves on to the next model at once."""

    code = "PROVIDER_DAILY_QUOTA"


class ProviderTimedOut(ProviderUnavailable):
    """The request did not answer within the timeout. A model that hangs once usually hangs
    again under load, so the chain moves to the next model instead of retrying it."""

    code = "PROVIDER_TIMED_OUT"


class ProviderRejected(SafeLineError):
    """A 4xx other than 401/403/429 (an unknown model id, a refused parameter): retrying the same
    request cannot help, so the provider is skipped at once."""

    code = "PROVIDER_REJECTED"
    recoverable = True
    http_status = 502
