"""Builders for exporter tests: synthetic rows and report, no sample data."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.core.models import (
    REASON_TEXT,
    CommitmentRow,
    ConfidenceLevel,
    CostCounters,
    FieldKey,
    FieldResult,
    ReaderName,
    ReadingTrailStep,
    RunReport,
    SourceKind,
)


def make_field(
    key: FieldKey,
    value: str | None,
    level: ConfidenceLevel = ConfidenceLevel.A,
    *,
    reason: str | None = None,
) -> FieldResult:
    if level is ConfidenceLevel.D and reason is None:
        reason = REASON_TEXT["not_found"]
    return FieldResult(
        field=key,
        value=value,
        display_value=value,
        level=level,
        score=0.9,
        source=SourceKind.printed,
        box=None,
        candidates=[],
        validations=[],
        trail=[
            ReadingTrailStep(
                stage="S3",
                reader=ReaderName.tesseract,
                outcome="value",
                value=value,
                reader_confidence=0.9,
                duration_ms=1,
                cloud=False,
            )
        ],
        reason=reason,
        escalated=False,
    )


def make_row(
    row_id: str = "r1",
    *,
    national_id: str = "011223344",
    commitment: str = "X132767",
    centrals: tuple[str, ...] = ("123456",),
    receipt_date: str | None = "05/03/2026",
    first_name_level: ConfidenceLevel = ConfidenceLevel.A,
    commitment_level: ConfidenceLevel = ConfidenceLevel.A,
    row_level: ConfidenceLevel = ConfidenceLevel.A,
) -> CommitmentRow:
    fields = {
        FieldKey.first_name: make_field(FieldKey.first_name, "דנה", first_name_level),
        FieldKey.last_name: make_field(FieldKey.last_name, "כהן"),
        FieldKey.national_id: make_field(FieldKey.national_id, national_id),
        FieldKey.central_number: make_field(
            FieldKey.central_number, centrals[0] if centrals else None
        ),
        FieldKey.receipt_date: make_field(FieldKey.receipt_date, receipt_date),
        FieldKey.sender: make_field(FieldKey.sender, "מכבי"),
        FieldKey.commitment_number: make_field(
            FieldKey.commitment_number, commitment, commitment_level
        ),
    }
    return CommitmentRow(
        row_id=row_id,
        group_id="g1",
        page_ids=["p1", "p2"],
        fields=fields,
        central_numbers=list(centrals),
        row_level=row_level,
        status="auto",
    )


def make_report() -> RunReport:
    return RunReport(
        job_id="job-1",
        started_at=datetime(2026, 10, 22, tzinfo=UTC),
        finished_at=None,
        page_count=3,
        row_count=1,
        fields_total=7,
        fields_closed_local=6,
        fields_escalated=1,
        fields_flagged=1,
        tier1_calls=0,
        tier2_calls=0,
        local_close_rate=0.86,
        level_histogram={"A": 6, "D": 1},
        stage_durations_ms={"S3": 10},
        corrections_histogram={},
        cost=CostCounters(
            provider_calls=0, cache_hits=0, input_tokens=0, output_tokens=0, estimated_usd=0.0
        ),
        provider_failovers=[],
        engine_version="test",
        prompt_version="p1",
    )
