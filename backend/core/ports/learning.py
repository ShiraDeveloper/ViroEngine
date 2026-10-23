"""LearningStorePort — contract for the privacy-preserving learning layer.

Stores locations and abstracted shapes only.
Names, IDs, dates, form values and images are rejected by _assert_pii_free.
This port never returns values — only positional and structural hints.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from backend.core.models import (
    FieldStat,
    LayoutHint,
    LayoutObservation,
    PatternHint,
    ReaderStat,
    RunMetric,
)


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

    def label_synonyms(self, sender: str, field: str) -> list[str]:
        """Return label texts a human approved for (sender, field) after enough confirmations.

        Returns an empty list when there are none. Used only to widen the labels S4/S6 search
        for; a synonym never creates or rejects a value."""
        ...

    def record_run(self, metric: RunMetric) -> None:
        """Append one finished job's counters (no values) to the learning curve."""
        ...

    def record_outcome(self, sender: str, field: str, *, corrected: bool) -> None:
        """Count one reviewed field: accepted as read, or corrected by a person. Counts only."""
        ...

    def field_stats(self) -> list[FieldStat]:
        """Everything review has taught so far, one entry per (sender, field). Never a value."""
        ...

    def record_reader_outcome(self, field: str, reader: str, *, right: bool) -> None:
        """Count one disputed name a person decided: whether `reader` had read that value."""
        ...

    def reader_stats(self) -> list[ReaderStat]:
        """Per (field, reader): disputes a person decided and how many that reader got right."""
        ...

    def pattern_hints(self, sender: str, field: str) -> list[PatternHint]:
        """Return regex-pattern hints for (sender, field) pairs.

        Returns an empty list when no patterns are available.
        Patterns adjust the confidence score — they never reject or create a value (§2.1).
        """
        ...


@runtime_checkable
class LearningAdminPort(Protocol):
    """What the dashboard and the approval screen read and do. Counters, boxes and shapes only."""

    def run_metrics(self, limit: int = 200) -> list[dict[str, Any]]:
        """The learning curve, oldest first."""
        ...

    def proposals(self) -> dict[str, list[dict[str, Any]]]:
        """Label synonyms and sender proposals, approved or not."""
        ...

    def approve(self, kind: Literal["label", "sender"], proposal_id: int) -> bool:
        """Approve one proposal (an explicit human act). False when there is none."""
        ...

    def seed(self) -> dict[str, list[dict[str, Any]]]:
        """Promoted layouts and shapes as a PII-free document."""
        ...
