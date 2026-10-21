"""INTERIM row assembly: one row per processed page. Replaced by S10 (T20, Ahuba).

This is deliberately not grouping. It never attaches a page to another, never splits a page into
one row per commitment, never merges duplicates and reports no orphans. It exists so the
orchestrator can emit rows and an end-to-end run has something to show before T20 merges; T20
passes its own assembler to `run_job` and this module is then deleted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from backend.core.models import (
    CommitmentRow,
    ConfidenceLevel,
    FieldKey,
    FieldResult,
    PageGroup,
)
from backend.core.pipeline.outcome import Assembly, PageOutcome

# Structural: PLAN §5.2 - the four fields that decide a row's level.
CRITICAL_FIELDS: Final = (
    FieldKey.national_id,
    FieldKey.central_number,
    FieldKey.receipt_date,
    FieldKey.commitment_number,
)
_LEVEL_ORDER: Final = {
    ConfidenceLevel.A: 0,
    ConfidenceLevel.B: 1,
    ConfidenceLevel.C: 2,
    ConfidenceLevel.D: 3,
}


def worst_critical_level(results: Mapping[FieldKey, FieldResult]) -> ConfidenceLevel:
    """Worst level among the four critical fields; a missing field counts as D."""
    levels = [results[k].level if k in results else ConfidenceLevel.D for k in CRITICAL_FIELDS]
    return max(levels, key=lambda level: _LEVEL_ORDER[level])


def one_row_per_page(outcomes: Sequence[PageOutcome]) -> Assembly:
    """One row and one single-page group for each page that was processed. Failed pages yield
    nothing: they are reported in the job's errors."""
    rows: list[CommitmentRow] = []
    groups: list[PageGroup] = []
    for outcome in outcomes:
        if outcome.status != "done":
            continue
        results = outcome.results
        central = results.get(FieldKey.central_number)
        rows.append(
            CommitmentRow(
                row_id=f"row-{outcome.page_id}",
                group_id=f"grp-{outcome.page_id}",
                page_ids=[outcome.page_id],
                fields=results,
                central_numbers=[central.value] if central is not None and central.value else [],
                row_level=worst_critical_level(results),
                status="auto",
            )
        )
        groups.append(
            PageGroup(
                group_id=f"grp-{outcome.page_id}",
                page_ids=[outcome.page_id],
                sender=outcome.sender,
                family=None,
                is_orphan=False,
                grouped_by=[],
            )
        )
    return rows, groups, []
