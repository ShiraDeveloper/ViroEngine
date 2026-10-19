"""ClockPort — injectable clock so 'not in the future' is testable.

The FixedClock fake in adapters/fakes.py returns a pinned datetime,
making every date validation deterministic in tests.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClockPort(Protocol):
    """Return the current datetime."""

    def now(self) -> datetime:
        """Return the current wall-clock time as a timezone-aware datetime.

        Used for date validation (receipt date must not be in the future)
        and for TTL expiry calculations.
        """
        ...
