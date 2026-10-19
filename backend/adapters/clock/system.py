"""SystemClock — ClockPort implementation using datetime.now().

Returns the current wall-clock time as an aware datetime (UTC).
"""

from __future__ import annotations

from datetime import datetime, timezone


class SystemClock:
    """ClockPort backed by the system clock.

    Returns UTC time. Use FixedClock from fakes.py in tests.
    """

    def now(self) -> datetime:
        """Return the current UTC datetime."""
        return datetime.now(tz=timezone.utc)
