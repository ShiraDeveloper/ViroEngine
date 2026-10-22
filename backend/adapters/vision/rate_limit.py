"""Token bucket per provider (PLAN §6.3): capacity 10, one token back every 6 s (~10 req/min)."""

from __future__ import annotations

from collections.abc import Callable
import threading
import time


class TokenBucket:
    """A bucket of `capacity` tokens refilled continuously, one per `refill_seconds`.

    The clock is injected (monotonic seconds) so tests advance time without sleeping."""

    def __init__(
        self,
        *,
        capacity: int,
        refill_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1 or refill_seconds <= 0:
            raise ValueError("capacity must be >= 1 and refill_seconds > 0")
        self._capacity = float(capacity)
        self._refill_seconds = refill_seconds
        self._clock = clock
        self._tokens = float(capacity)
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        gained = max(0.0, now - self._last) / self._refill_seconds
        self._tokens = min(self._capacity, self._tokens + gained)
        self._last = now

    def try_acquire(self) -> bool:
        """Take one token if there is one. Never blocks."""
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def wait_seconds(self) -> float:
        """Seconds until one token is available (0.0 when one is)."""
        with self._lock:
            self._refill()
            missing = max(0.0, 1.0 - self._tokens)
            return missing * self._refill_seconds
