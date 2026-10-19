"""LearningStorePort — contract for the privacy-preserving learning layer.

Stores locations and abstracted shapes only.
Names, IDs, dates, form values and images are rejected by _assert_pii_free.
This port never returns values — only positional and structural hints.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.core.models import LayoutHint, LayoutObservation, PatternHint


@runtime_checkable
class LearningStorePort(Protocol):
    """Record confirmed observations and return positional hints for future pages."""

    def record_confirmation(self, obs: LayoutObservation) -> None:
        """Persist a confirmed field observation.

        Raises ValueError if obs contains PII (_assert_pii_free gate).
        Must be called only after human confirmation (POST /v1/jobs/{id}/confirm).
        Stores locations and abstracted shapes only — never the value.
        """
        ...

    def layout_hints(self, sender: str, field: str) -> list[LayoutHint]:
        """Return approximate region locations for (sender, field) pairs.

        Returns an empty list when no observations are available.
        Hints are used to create extra Region candidates with modest scores;
        they never override detector output.
        """
        ...

    def pattern_hints(self, sender: str, field: str) -> list[PatternHint]:
        """Return regex-pattern hints for (sender, field) pairs.

        Returns an empty list when no patterns are available.
        Patterns adjust the confidence score — they never reject or create a value (§2.1).
        """
        ...
