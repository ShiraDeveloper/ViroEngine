"""SenderIndexPort - contract for the closed list of sending institutions.

The core never imports the rapidfuzz-backed implementation in backend/adapters/senders; it
sees only this Protocol, so tests can pass any object with the same shape.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.core.models import SenderSpec


@runtime_checkable
class SenderMatchLike(Protocol):
    """One letterhead matched to one closed-list entry. `score` is 0-100."""

    @property
    def canonical(self) -> str: ...

    @property
    def score(self) -> float: ...

    @property
    def spec(self) -> SenderSpec: ...


@runtime_checkable
class SenderIndexPort(Protocol):
    """Closed list lookup. Never invents an institution that is not in `specs`."""

    @property
    def specs(self) -> tuple[SenderSpec, ...]: ...

    @property
    def min_ratio(self) -> int:
        """Default acceptance threshold (0-100) from config/senders.json."""
        ...

    def get(self, canonical: str) -> SenderSpec | None:
        """The entry with this canonical name, or None."""
        ...

    def match(self, text: str, *, min_ratio: int | None = None) -> SenderMatchLike | None:
        """Best closed-list match for `text` at or above `min_ratio`; None below it or on a tie.
        Never raises."""
        ...
