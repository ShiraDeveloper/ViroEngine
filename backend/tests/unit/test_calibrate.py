from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from backend.core.models import FieldKey
from backend.core.scoring import DEFAULT_WEIGHTS, Signals
from eval.calibrate import (
    BEGIN_MARK,
    END_MARK,
    CalibrationInputError,
    SignalRecord,
    calibrate,
    evaluate_metrics,
    read_signals,
    write_config,
    write_signals,
)


def sig(strength: float) -> Signals:
    return Signals(
        s_agree=0.0, s_source=strength, s_label=strength, s_margin=strength,
        s_pattern=strength, s_conf=strength, s_image=strength, s_hard=1.0,
    )  # fmt: skip


def rec(page: str, predicted: str | None, gate: str, strength: float = 0.0) -> SignalRecord:
    return SignalRecord(
        page_id=page,
        field=FieldKey.central_number,
        predicted=predicted,
        gate=gate,  # type: ignore[arg-type]
        signals=sig(strength) if gate == "score" else None,
    )


def write_gold(path: Path, rows: list[tuple[str, str, str]]) -> None:
    lines = ["page_id,field,value,split", *(f"{p},central_number,{v},{s}" for p, v, s in rows)]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_metrics_count_wrong_unflagged_as_silent_error() -> None:
    pairs = [
        (rec("p1", "1", "accepted"), "1"),
        (rec("p2", "9", "accepted"), "2"),  # wrong and silent
        (rec("p3", "9", "flagged"), "3"),  # wrong and caught
        (rec("p4", "4", "flagged"), "4"),  # right but flagged
    ]
    m = evaluate_metrics(pairs, DEFAULT_WEIGHTS, 0.8)
    assert (m.n_wrong, m.flagged) == (2, 2)
    assert m.flag_recall == 0.5
    assert m.flag_rate == 0.5
    assert m.silent_error == 0.25


def test_score_gate_flags_below_threshold_only() -> None:
    weak = rec("p1", "1", "score", 0.5)
    strong = rec("p2", "1", "score", 1.0)
    m = evaluate_metrics([(weak, "1"), (strong, "1")], DEFAULT_WEIGHTS, 0.7)
    assert m.flagged == 1


def test_calibrate_flags_wrong_fields_within_the_rate_cap(tmp_path: Path) -> None:
    records = [rec(f"w{i}", "x", "score", 0.5) for i in range(2)]
    records += [rec(f"r{i}", "ok", "score", 1.0) for i in range(8)]
    write_signals(tmp_path / "s.jsonl", records)
    write_gold(
        tmp_path / "gold.csv",
        [(f"w{i}", "ok", "dev") for i in range(2)] + [(f"r{i}", "ok", "dev") for i in range(8)],
    )
    result = calibrate(tmp_path / "gold.csv", tmp_path / "s.jsonl", held_out=None)
    assert result.metrics.flag_recall == 1.0
    assert result.metrics.flag_rate <= 0.15 + 0.1  # 2 of 10 wrong fields flagged
    assert result.metrics.silent_error == 0.0
    assert sum(result.weights.as_tuple()) == pytest.approx(1.0)


def test_calibrate_excludes_held_out_pages(tmp_path: Path) -> None:
    write_signals(tmp_path / "s.jsonl", [rec("d1", "1", "accepted"), rec("h1", "1", "accepted")])
    write_gold(tmp_path / "gold.csv", [("d1", "1", "dev"), ("h1", "2", "held_out")])
    result = calibrate(tmp_path / "gold.csv", tmp_path / "s.jsonl", held_out=None)
    assert result.excluded_held_out == 1
    assert result.metrics.n_fields == 1


def test_calibrate_excludes_pages_listed_in_held_out_file(tmp_path: Path) -> None:
    write_signals(tmp_path / "s.jsonl", [rec("d1", "1", "accepted"), rec("h1", "2", "accepted")])
    write_gold(tmp_path / "gold.csv", [("d1", "1", ""), ("h1", "1", "")])
    (tmp_path / "held_out.txt").write_text("# held out\nh1\n", encoding="utf-8")
    result = calibrate(
        tmp_path / "gold.csv", tmp_path / "s.jsonl", held_out=tmp_path / "held_out.txt"
    )
    assert result.excluded_held_out == 1
    assert result.metrics.n_wrong == 0


def test_calibrate_reports_unreachable_recall_honestly(tmp_path: Path) -> None:
    write_signals(tmp_path / "s.jsonl", [rec("p1", "9", "accepted"), rec("p2", "1", "accepted")])
    write_gold(tmp_path / "gold.csv", [("p1", "1", "dev"), ("p2", "1", "dev")])
    result = calibrate(tmp_path / "gold.csv", tmp_path / "s.jsonl", held_out=None)
    assert not result.target_met  # an A/B error cannot be flagged by any threshold
    assert result.metrics.silent_error == 0.5


def test_calibrate_without_labelled_rows_fails(tmp_path: Path) -> None:
    write_signals(tmp_path / "s.jsonl", [rec("p1", "1", "accepted")])
    write_gold(tmp_path / "gold.csv", [("other", "1", "dev")])
    with pytest.raises(CalibrationInputError):
        calibrate(tmp_path / "gold.csv", tmp_path / "s.jsonl", held_out=None)


def test_signals_round_trip(tmp_path: Path) -> None:
    records = [rec("p1", "1", "score", 0.7), rec("p2", None, "flagged")]
    write_signals(tmp_path / "s.jsonl", records)
    assert read_signals(tmp_path / "s.jsonl") == records


def test_write_config_replaces_only_the_marked_block(tmp_path: Path) -> None:
    models = tmp_path / "models.yaml"
    models.write_text(
        f"keep: 1\nscoring:\n  {BEGIN_MARK} x\n  old: 1\n  {END_MARK}\nafter: 2\n",
        encoding="utf-8",
    )
    write_signals(tmp_path / "s.jsonl", [rec("p1", "1", "accepted")])
    write_gold(tmp_path / "gold.csv", [("p1", "1", "dev")])
    result = calibrate(tmp_path / "gold.csv", tmp_path / "s.jsonl", held_out=None)
    write_config(result, models, date(2026, 10, 21))
    text = models.read_text(encoding="utf-8")
    assert text.startswith("keep: 1\nscoring:\n") and text.endswith("after: 2\n")
    assert "old: 1" not in text
    assert "dev split" in text and "2026-10-21" in text
    assert "weights:" in text


def test_write_config_requires_markers(tmp_path: Path) -> None:
    models = tmp_path / "models.yaml"
    models.write_text("scoring: {}\n", encoding="utf-8")
    write_signals(tmp_path / "s.jsonl", [rec("p1", "1", "accepted")])
    write_gold(tmp_path / "gold.csv", [("p1", "1", "dev")])
    result = calibrate(tmp_path / "gold.csv", tmp_path / "s.jsonl", held_out=None)
    with pytest.raises(CalibrationInputError):
        write_config(result, models, date(2026, 10, 21))
