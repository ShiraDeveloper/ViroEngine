"""Tests for the final held-out run and its guards (T30): freeze tag, held-out used once, complete
ablation table. The evaluator is a fake returning scripted reports; no gold, OCR or git needed."""

from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path

import pytest

from eval.final import (
    FULL_SYSTEM,
    AblationRow,
    FreezeError,
    HeldOutAlreadyUsed,
    IncompleteAblation,
    assert_held_out_unread,
    check_freeze,
    ensure_complete,
    render_markdown,
    run_final,
    write_report,
)
from eval.heldout_log import read_runs, record_read
from eval.run_eval import ABLATIONS, EvalReport, evaluate

HEAD = "a" * 40


def report(split: str = "heldout", silent: float = 0.05, fields: int = 105) -> EvalReport:
    return EvalReport(
        split=split, date=date(2026, 10, 24), local_only=True, ablation=None, pages=15,  # type: ignore[arg-type]
        failed_pages=[], unlabelled_pages=0, fields=fields, correct=90, accuracy=0.86,
        printed_accuracy=0.9, printed_n=60, handwritten_accuracy=0.6, handwritten_n=20,
        silent_error=silent, flag_recall=0.9, flag_precision=0.5, flag_rate=0.15,
        local_close_rate=0.7, escalation_rate=0.1, cloud_calls=0, measured_cost_usd=0.0,
        network_attempts=0, p50_latency_ms=9000, p95_latency_ms=12000, peak_memory_mb=300.0,
    )  # fmt: skip


def final(tmp_path: Path, **overrides: object) -> object:
    calls: list[str | None] = []

    def evaluator(ablation: str | None) -> EvalReport:
        calls.append(ablation)
        return report()

    args: dict[str, object] = {
        "evaluator": evaluator, "log": tmp_path / "log.jsonl", "head": HEAD, "tagged": HEAD,
    }  # fmt: skip
    args.update(overrides)
    result = run_final(**args)  # type: ignore[arg-type]
    return result, calls


# --------------------------------------------------------------------------- freeze


def test_final_run_refuses_without_the_freeze_tag(tmp_path: Path) -> None:
    with pytest.raises(FreezeError):
        final(tmp_path, tagged=None)


def test_final_run_refuses_when_head_moved_after_the_tag(tmp_path: Path) -> None:
    with pytest.raises(FreezeError):
        final(tmp_path, head="b" * 40)


def test_rehearsal_is_labelled_not_final(tmp_path: Path) -> None:
    result, _ = final(tmp_path, tagged=None, check_freeze_tag=False)
    assert not result.freeze_checked  # type: ignore[attr-defined]
    assert result.notes[0].startswith("REHEARSAL")  # type: ignore[attr-defined]
    assert "NOT checked" in render_markdown(result)  # type: ignore[arg-type]


def test_check_freeze_accepts_the_tagged_commit() -> None:
    check_freeze(head=HEAD, tagged=HEAD)


# --------------------------------------------------------------------------- once


def test_held_out_split_used_exactly_once(tmp_path: Path) -> None:
    """Nothing read the held-out pages before the final run; a second run is refused."""
    log = tmp_path / "log.jsonl"
    assert_held_out_unread(log)  # no log: untouched
    record_read(log, split="heldout", pages=15, ablation=None, commit=HEAD)
    with pytest.raises(HeldOutAlreadyUsed):
        assert_held_out_unread(log)
    with pytest.raises(HeldOutAlreadyUsed):
        final(tmp_path)


def test_a_malformed_log_line_still_counts_as_a_use(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    log.write_text("not json\n", encoding="utf-8")
    assert len(read_runs(log)) == 1
    with pytest.raises(HeldOutAlreadyUsed):
        assert_held_out_unread(log)


def test_evaluate_logs_a_heldout_read_before_reading(tmp_path: Path) -> None:
    gold = tmp_path / "gold.csv"
    with gold.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["page_label", "field", "confirmed_value"])
        writer.writerow(["A5", "first_name", "x"])
        writer.writerow(["A6", "first_name", "x"])
    held = tmp_path / "held.txt"
    held.write_text("A5\n", encoding="utf-8")
    log = tmp_path / "log.jsonl"
    # No sample PDFs are passed, so the run stops after the log line: the read was recorded first.
    with pytest.raises(Exception, match="sample PDFs not found"):
        evaluate(gold, split="heldout", held_out=held, pdfs=(), heldout_log=log)
    runs = read_runs(log)
    assert [(r.split, r.pages) for r in runs] == [("heldout", 1)]
    raw = log.read_text(encoding="utf-8")
    assert "A5" not in raw and "first_name" not in raw  # counts only


def test_dev_run_does_not_touch_the_log(tmp_path: Path) -> None:
    gold = tmp_path / "gold.csv"
    gold.write_text("page_label,field,confirmed_value\nA6,first_name,x\n", encoding="utf-8")
    held = tmp_path / "held.txt"
    held.write_text("A5\n", encoding="utf-8")
    log = tmp_path / "log.jsonl"
    with pytest.raises(Exception, match="sample PDFs not found"):
        evaluate(gold, split="dev", held_out=held, pdfs=(), heldout_log=log)
    assert not log.exists()


# --------------------------------------------------------------------------- table


def test_ablation_table_complete(tmp_path: Path) -> None:
    result, calls = final(tmp_path)
    assert [r.name for r in result.rows] == [FULL_SYSTEM, *ABLATIONS]  # type: ignore[attr-defined]
    assert calls == [None, *ABLATIONS]
    write_report(result, tmp_path / "out")  # type: ignore[arg-type]
    data = json.loads((tmp_path / "out" / "final_report.json").read_text(encoding="utf-8"))
    assert len(data["rows"]) == 1 + len(ABLATIONS)
    text = (tmp_path / "out" / "final_report.md").read_text(encoding="utf-8")
    assert all(f"| {name} |" in text for name in [FULL_SYSTEM, *ABLATIONS])
    assert "not run" in text  # augmented variants are said not to have run


def test_missing_ablation_row_stops_the_report() -> None:
    rows = [AblationRow(name=FULL_SYSTEM, report=report())]
    with pytest.raises(IncompleteAblation):
        ensure_complete(rows)


def test_dev_measurement_is_not_a_final_row() -> None:
    rows = [AblationRow(name=n, report=report()) for n in (FULL_SYSTEM, *ABLATIONS)]
    rows[2] = AblationRow(name=rows[2].name, report=report(split="dev"))
    with pytest.raises(IncompleteAblation):
        ensure_complete(rows)
