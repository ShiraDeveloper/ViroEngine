"""S11 finalize: the run report, the job status and the purge schedule (PLAN §4 S11, T20)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped,unused-ignore]

from backend.core.models import ConfidenceLevel, JobStatus
from backend.core.pipeline.s10_rows import RowsConfig, assemble_rows
from backend.core.pipeline.s11_finalize import (
    PAGE_ACCOUNTING_ERROR,
    accounting_errors,
    build_report,
    final_status,
    flagged_field_count,
    purge_schedule,
)
from backend.tests.fixtures.rows import ctx, page

MODELS_YAML = Path(__file__).resolve().parents[3] / "config" / "models.yaml"
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
DATE = "18/06/2026"


def good() -> list:  # type: ignore[type-arg]
    return [
        page(
            "p1",
            centrals=["937309"],
            date=DATE,
            commitments=["4521"],
            first_name="דנה",
            last_name="כהן",
        )
    ]


def test_clean_job_is_confirmed() -> None:
    pages = good()
    rows, _, orphans = assemble_rows(pages, ctx=ctx())

    assert flagged_field_count(rows) == 0
    assert final_status(pages, rows, orphans, []) is JobStatus.confirmed


def test_a_flagged_row_needs_review() -> None:
    pages = [
        page("p1", centrals=["937309"], commitments=["4521"], first_name="דנה", last_name="כהן")
    ]  # no stamp date
    rows, _, orphans = assemble_rows(pages, ctx=ctx())

    assert flagged_field_count(rows) > 0
    assert final_status(pages, rows, orphans, []) is JobStatus.needs_review


def test_an_orphan_page_needs_review() -> None:
    pages = [*good(), page("p2", "sap_screen", centrals=["555555"])]
    rows, _, orphans = assemble_rows(pages, ctx=ctx())

    assert orphans == ["p2"]
    assert final_status(pages, rows, orphans, []) is JobStatus.needs_review


def test_a_recorded_error_needs_review() -> None:
    pages = good()
    rows, _, orphans = assemble_rows(pages, ctx=ctx())

    assert final_status(pages, rows, orphans, ["X"]) is JobStatus.needs_review


def test_no_page_read_is_failed() -> None:
    pages = [page("p1", status="failed")]

    assert final_status(pages, [], ["p1"], ["E:p1"]) is JobStatus.failed


def test_a_multi_central_page_is_not_flagged_at_row_level() -> None:
    pages = [
        page(
            "p1",
            centrals=["937309", "937310", "937311"],
            date=DATE,
            commitments=["4521"],
            first_name="דנה",
            last_name="כהן",
        )
    ]
    rows, _, orphans = assemble_rows(pages, ctx=ctx())

    assert pages[0].results[next(iter(pages[0].results))] is not None
    assert flagged_field_count(rows) == 0
    assert final_status(pages, rows, orphans, []) is JobStatus.confirmed


def test_report_holds_counters_only() -> None:
    pages = good()

    report = build_report(
        "job-1",
        pages,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=5),
        row_count=1,
        page_count=1,
        intake_ms=12,
        prompt_version="v1",
    )

    dumped = report.model_dump_json()
    assert "937309" not in dumped and "4521" not in dumped and "011223344" not in dumped
    assert report.page_count == 1 and report.row_count == 1
    assert report.stage_durations_ms["S0"] == 12
    assert sum(report.level_histogram.values()) == report.fields_total


def test_accounting_error_is_reported_when_a_page_is_lost() -> None:
    pages = good()
    _, groups, orphans = assemble_rows(pages, ctx=ctx())

    assert accounting_errors(1, groups, orphans) == []
    assert accounting_errors(2, groups, orphans) == [PAGE_ACCOUNTING_ERROR]


def test_purge_schedule_follows_the_ttl() -> None:
    schedule = purge_schedule(NOW, ttl_minutes=30, purged_visible_minutes=10, sweep_minutes=5)

    assert schedule.expires_at == NOW + timedelta(minutes=30)
    assert schedule.purged_visible_until == NOW + timedelta(minutes=40)
    assert schedule.sweep_every == timedelta(minutes=5)


@pytest.mark.parametrize(
    "bad",
    [
        {"ttl_minutes": 0, "purged_visible_minutes": 10, "sweep_minutes": 5},
        {"ttl_minutes": 30, "purged_visible_minutes": -1, "sweep_minutes": 5},
        {"ttl_minutes": 30, "purged_visible_minutes": 10, "sweep_minutes": 0},
    ],
)
def test_purge_schedule_rejects_nonsense(bad: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="positive"):
        purge_schedule(NOW, **bad)


def test_rows_defaults_match_models_yaml() -> None:
    data = yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8"))

    assert RowsConfig.model_validate(data["rows"]) == RowsConfig()


def test_levels_are_unchanged_by_assembly_for_a_plain_form() -> None:
    pages = good()
    (row,) = assemble_rows(pages, ctx=ctx())[0]

    for key, result in pages[0].results.items():
        if key.value in ("central_number", "commitment_number"):
            assert row.fields[key].level is result.level
    assert row.row_level in (ConfidenceLevel.A, ConfidenceLevel.B)
