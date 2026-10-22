"""PageOutcome - what the orchestrator hands to row assembly, evaluation and the report."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from backend.core.models import CommitmentRow, FieldKey, FieldResult, ImageRef, PageGroup, Strict
from backend.core.pipeline.s5_classify import PageRole
from backend.core.pipeline.s8_score import FieldEvaluation
from backend.core.pipeline.s9_escalate import EscalationStats


class PageOutcome(Strict):
    """What one page produced. `evaluations` is empty for a failed page. Holds field values, so
    it is never logged and never copied into the RunReport."""

    page_id: str
    label: str | None
    source_file: str
    page_index: int
    status: Literal["done", "failed"]
    error_code: str | None = None
    page_role: PageRole | None = None
    sender: str | None = None
    # S10 inputs: the page content hash (duplicate detection) and an explicit "page N of M".
    sha256: str | None = None
    # The corrected page the boxes of this page's fields refer to (crop and image routes).
    color_image: ImageRef | None = None
    page_number: int | None = None
    page_total: int | None = None
    corrections: list[str] = Field(default_factory=list)
    durations_ms: dict[str, int] = Field(default_factory=dict)
    evaluations: dict[FieldKey, FieldEvaluation] = Field(default_factory=dict)
    escalation: EscalationStats = Field(default_factory=EscalationStats)  # S9 counters, no values

    @property
    def results(self) -> dict[FieldKey, FieldResult]:
        """The decided field of every key this page produced."""
        return {k: ev.result for k, ev in self.evaluations.items()}


# Rows, groups and orphan page ids: what S10 (T20) produces from the page outcomes.
Assembly = tuple[list[CommitmentRow], list[PageGroup], list[str]]
