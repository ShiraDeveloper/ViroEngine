"""Credential ring (PLAN §6.1): rotate on 429, retire on 401/403, always log by label.

This exists for resilience across a minute-rate limit, not to multiply a free quota; the
sanctioned key set is documented in `.env.example`. A key is held in `Credential.key` and
nowhere else: it is excluded from `repr`, never logged and never part of an error message.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import time
from typing import Literal

import structlog

log = structlog.get_logger()


@dataclass
class Credential:
    """One API key. Logs and health use `label` (k1, k2, ...), never `key`."""

    label: str
    key: str = field(repr=False)
    state: Literal["ok", "cooling", "dead"] = "ok"
    cooldown_until: float = 0.0


class CredentialRing:
    """An ordered set of credentials for one provider. Thread-safe enough for the one worker
    thread that calls it; the clock (monotonic seconds) is injected."""

    def __init__(
        self,
        keys: Sequence[str],
        *,
        provider: str,
        cooldown_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._creds = [Credential(label=f"k{i}", key=k) for i, k in enumerate(keys, start=1)]

    def next(self) -> Credential | None:
        """The first credential that is usable now: `ok`, or `cooling` whose cooldown has ended.
        None when every credential is cooling or dead."""
        now = self._clock()
        for cred in self._creds:
            if cred.state == "cooling" and cred.cooldown_until <= now:
                cred.state = "ok"
            if cred.state == "ok":
                return cred
        return None

    def cool(self, cred: Credential) -> None:
        """Rate limited (429): rest this credential for the cooldown, then it is usable again."""
        cred.state = "cooling"
        cred.cooldown_until = self._clock() + self._cooldown
        log.info("credential_rate_limited", provider=self._provider, label=cred.label)

    def kill(self, cred: Credential) -> None:
        """Rejected (401/403): dead for the life of the process. Logged once, by label."""
        if cred.state == "dead":
            return
        cred.state = "dead"
        log.warning("credential_dead", provider=self._provider, label=cred.label)

    @property
    def labels(self) -> list[str]:
        """Labels of the credentials that are not dead."""
        return [c.label for c in self._creds if c.state != "dead"]

    @property
    def configured(self) -> int:
        """How many credentials were configured, dead or not."""
        return len(self._creds)
