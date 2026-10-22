"""Circuit breaker per provider (PLAN §6.3): 5 consecutive failures -> open 60 s -> half-open probe."""

from __future__ import annotations

from collections.abc import Callable
import threading
import time
from typing import Literal

CircuitState = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    """Stops calling a provider that keeps failing, then lets one probe through to test it.

    closed -> (`failure_threshold` consecutive failures) -> open -> (`open_seconds` later) ->
    half_open -> success closes it, failure re-opens it for another `open_seconds`.
    The clock is injected (monotonic seconds)."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        open_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1 or open_seconds < 0:
            raise ValueError("failure_threshold must be >= 1 and open_seconds >= 0")
        self._threshold = failure_threshold
        self._open_seconds = open_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """closed, open (calls refused) or half_open (one probe may go through)."""
        with self._lock:
            return self._state()

    def _state(self) -> CircuitState:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self._open_seconds:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        """True when a call may go ahead. In half_open exactly one probe is admitted until its
        outcome is recorded."""
        with self._lock:
            state = self._state()
            if state == "closed":
                return True
            if state == "half_open" and not self._probing:
                self._probing = True
                return True
            return False

    def record_success(self) -> None:
        """A call succeeded: the count restarts and an open or half-open circuit closes."""
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probing = False

    def record_failure(self) -> None:
        """A call failed after its retries. The `failure_threshold`-th in a row (or any failure of
        a half-open probe) opens the circuit."""
        with self._lock:
            probe = self._probing
            self._probing = False
            self._failures += 1
            if probe or self._failures >= self._threshold:
                self._opened_at = self._clock()
