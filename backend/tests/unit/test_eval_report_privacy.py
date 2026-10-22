"""T26 — the metrics the dashboard reads hold counts and rates, never a field value.

test_eval_report_has_no_value_fields          — no model field can carry a form value or text
test_eval_report_serialises_only_numbers_and_closed_names — a dumped report has no free strings
"""

from __future__ import annotations

from datetime import date

from backend.core.models import FieldKey
from eval.run_eval import EvalReport, FieldBreakdown

FORBIDDEN_PARTS = ("value", "text", "name", "image", "crop")


def test_eval_report_has_no_value_fields() -> None:
    names = [*EvalReport.model_fields, *FieldBreakdown.model_fields]

    offenders = [n for n in names if any(part in n for part in FORBIDDEN_PARTS)]

    assert offenders == []


def test_eval_report_serialises_only_numbers_and_closed_names() -> None:
    report = EvalReport(
        split="dev",
        date=date(2026, 10, 26),
        local_only=True,
        ablation=None,
        pages=1,
        failed_pages=[],
        unlabelled_pages=0,
        fields=7,
        correct=6,
        accuracy=0.86,
        printed_accuracy=None,
        printed_n=0,
        handwritten_accuracy=None,
        handwritten_n=0,
        silent_error=0.0,
        flag_recall=None,
        flag_precision=None,
        flag_rate=0.1,
        local_close_rate=0.7,
        escalation_rate=0.1,
        cloud_calls=0,
        measured_cost_usd=0.0,
        network_attempts=0,
        p50_latency_ms=10,
        p95_latency_ms=20,
        peak_memory_mb=None,
        per_field=[
            FieldBreakdown(
                field=FieldKey.national_id,
                n=1,
                accuracy=1.0,
                flag_rate=0.0,
                silent_error=0.0,
                top_failure_reason=None,
            )
        ],
    )

    dumped = report.model_dump(mode="json")
    strings = {
        v
        for v in [*dumped.values(), *(x for f in dumped["per_field"] for x in f.values())]
        if isinstance(v, str)
    }

    assert strings <= {"dev", "2026-10-26", "national_id"}
