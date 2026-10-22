"""Tests for eval/bakeoff.py (T17). The evaluator is a plain fake that returns scripted reports, so
the harness (isolation, guards, decision rule, CSV) is exercised without OCR, models or keys."""

from __future__ import annotations

import csv
from dataclasses import fields
from datetime import date
from pathlib import Path

import pytest

from backend.container import Container, build_container
from backend.settings import Settings
from eval.bakeoff import (
    HARD_PAGES,
    BakeoffConfig,
    Contestant,
    ContestantRow,
    Owner,
    Stage,
    check_pages,
    decide_stage,
    default_contestants,
    run_bakeoff,
    trocr_assumption,
)
from eval.make_gold import pick_held_out
from eval.run_eval import EvalInputError, EvalReport, HeldOutLeak

REPO_ROOT = Path(__file__).resolve().parents[3]
FAMILIES = REPO_ROOT / "config" / "gold_families.json"
DEV_PAGES = ["A2", "A9"]


def report(
    *,
    silent: float = 0.10,
    p50: int = 1000,
    mb: float = 200.0,
    hw_acc: float | None = 0.5,
    hw_n: int = 10,
) -> EvalReport:
    return EvalReport(
        split="dev", date=date(2026, 10, 22), local_only=True, ablation=None, pages=2,
        failed_pages=[], unlabelled_pages=0, fields=14, correct=10, accuracy=0.7,
        printed_accuracy=0.8, printed_n=20, handwritten_accuracy=hw_acc, handwritten_n=hw_n,
        silent_error=silent, flag_recall=None, flag_precision=None, flag_rate=0.2,
        local_close_rate=None, escalation_rate=None, cloud_calls=0, measured_cost_usd=0.0,
        network_attempts=0, p50_latency_ms=p50, p95_latency_ms=p50, peak_memory_mb=mb,
    )  # fmt: skip


def row(
    name: str,
    stage: Stage = Stage.region,
    *,
    silent: float | None = 0.10,
    local: bool = True,
    p50: int | None = 1000,
    mb: float | None = 200.0,
    isolated: bool = True,
    status: str = "ran",
    hw: float | None = 0.5,
) -> ContestantRow:
    return ContestantRow(
        contestant=name, stage=stage, owner=Owner.ahuba, local=local, status=status,
        isolated=isolated, silent_error=silent, p50_latency_ms=p50, peak_memory_mb=mb,
        handwritten_accuracy=hw, handwritten_n=10,
    )  # fmt: skip


@pytest.fixture
def base() -> Container:
    return build_container(Settings(_env_file=None, local_only=True), use_fakes=True)  # type: ignore[call-arg]


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    gold = tmp_path / "gold.csv"
    with gold.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["page_label", "field", "confirmed_value"])
        for label in DEV_PAGES:
            writer.writerow([label, "first_name", "x"])
    held = tmp_path / "held_out.txt"
    held.write_text("A5\nB7\n", encoding="utf-8")
    return gold, held


# --------------------------------------------------------------------------- hard pages


def test_hard_pages_are_all_development_split() -> None:
    """No hard page is held-out: against the committed list when it exists, and by construction
    (the picker never chooses one) otherwise."""
    held_file = REPO_ROOT / "eval" / "held_out.txt"
    if held_file.is_file():
        held = {
            ln.strip() for ln in held_file.read_text(encoding="utf-8").splitlines() if ln.strip()
        }
        assert not set(HARD_PAGES) & held
    assert len(HARD_PAGES) == len(set(HARD_PAGES)) == 15


def test_held_out_picker_never_chooses_a_hard_page(tmp_path: Path) -> None:
    labels = [f"A{i}" for i in range(1, 27)] + [f"B{i}" for i in range(1, 23)]
    gold = tmp_path / "gold.csv"
    with gold.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["page_label", "field", "confirmed_value"])
        for label in labels:
            writer.writerow([label, "first_name", "x"])
    families = {
        "sap": ["A2", "A3", "A4", "B20", "B21"],
        "handwriting_heavy": ["A17", "A18", "B10", "B11"],
        "faint_stamp": ["A5", "B8"],
        "fax_grade": ["A12", "B3"],
        "multi_central": ["A9", "A14", "A10"],
        "multi_commitment": ["A25", "B20"],
        "no_commitment": ["A17", "A21"],
        "letter_commitment": ["B14", "B15"],
    }
    for seed in range(20):
        picked = pick_held_out(gold, tmp_path / "held.txt", seed=seed, families=families)
        assert len(picked) == 15 and not set(picked) & set(HARD_PAGES)


def test_held_out_picker_names_a_family_made_only_of_hard_pages(tmp_path: Path) -> None:
    gold = tmp_path / "gold.csv"
    with gold.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["page_label", "field", "confirmed_value"])
        for label in [f"A{i}" for i in range(1, 27)]:
            writer.writerow([label, "first_name", "x"])
    with pytest.raises(EvalInputError, match="hard pages"):
        pick_held_out(
            gold,
            tmp_path / "held.txt",
            families={
                "sap": ["A3", "A4"],
                "handwriting_heavy": ["A18", "A21"],
                "faint_stamp": ["A5", "A6"],
                "fax_grade": ["A12", "A7"],
                "multi_central": ["A9", "A14"],  # both hard pages: the conflict
                "multi_commitment": ["A25", "A26"],
                "no_commitment": ["A18", "A21"],
                "letter_commitment": ["A15", "A16"],
            },
        )


def test_check_pages_refuses_a_held_out_page() -> None:
    with pytest.raises(HeldOutLeak):
        check_pages(["A2", "A5"], held_out={"A5"}, gold_labels={"A2", "A5"})


def test_check_pages_refuses_without_a_held_out_list() -> None:
    with pytest.raises(EvalInputError):
        check_pages(["A2"], held_out=set(), gold_labels={"A2"})


def test_check_pages_refuses_a_page_without_gold() -> None:
    with pytest.raises(EvalInputError):
        check_pages(["A2", "A9"], held_out={"A5"}, gold_labels={"A2"})


# --------------------------------------------------------------------------- isolation

CHANGES = {
    Stage.printed_ocr: {"ocr_printed"},
    Stage.digit_ocr: {"pipeline"},
    Stage.region: {"regions"},
    Stage.vision: {"settings", "vision"},
}


def test_each_contestant_isolated(base: Container) -> None:
    """A contestant changes only the container fields of its own stage."""
    swapped = [c for c in default_contestants() if c.swap is not None]
    assert {c.stage for c in swapped} == {Stage.digit_ocr, Stage.region, Stage.vision}
    for contestant in swapped:
        changed = {
            f.name
            for f in fields(Container)
            if getattr(base, f.name)
            is not getattr(contestant.swap(base) if contestant.swap else base, f.name)
        }
        assert changed and changed <= CHANGES[contestant.stage], contestant.name


def test_baselines_share_one_evaluation(base: Container, workspace: tuple[Path, Path]) -> None:
    gold, held = workspace
    calls: list[str] = []

    def evaluator(contestant: Contestant, container: Container) -> EvalReport:
        calls.append(contestant.name)
        return report()

    run_bakeoff(
        DEV_PAGES, gold, gold.parent / "out.csv", evaluator=evaluator, base=base, held_out=held,
        contestants=[c for c in default_contestants() if c.swap is None and c.name != "trocr"],
    )  # fmt: skip
    baselines = [c for c in default_contestants() if c.swap is None and c.name != "trocr"]
    available = [c for c in baselines if c.unavailable(base) is None]
    assert len(available) > 1 and len(calls) == 1  # one measurement serves every baseline


# --------------------------------------------------------------------------- report


def test_report_includes_losers(base: Container, workspace: tuple[Path, Path]) -> None:
    gold, held = workspace
    out = gold.parent / "bakeoff.csv"
    silent = {"rule_based": 0.20, "yolo_onnx": 0.30}

    contestants = [
        Contestant("rule_based", Stage.region, Owner.ahuba, True),
        Contestant("yolo_onnx", Stage.region, Owner.ahuba, True, swap=lambda c: c),
        Contestant(
            "ghost",
            Stage.region,
            Owner.ahuba,
            True,
            swap=lambda c: c,
            unavailable=lambda c: "no adapter",
        ),
    ]

    def evaluator(contestant: Contestant, container: Container) -> EvalReport:
        return report(silent=silent[contestant.name])

    result = run_bakeoff(
        DEV_PAGES, gold, out, contestants=contestants, evaluator=evaluator, base=base, held_out=held
    )
    with out.open(encoding="utf-8-sig", newline="") as handle:
        rows = {r["contestant"]: r for r in csv.DictReader(handle)}
    assert set(rows) == {"rule_based", "yolo_onnx", "ghost"}  # the loser and the skipped stay
    assert rows["yolo_onnx"]["silent_error"] == "0.3000" and rows["yolo_onnx"]["winner"] == "0"
    assert rows["rule_based"]["winner"] == "1"
    assert rows["ghost"]["status"] == "skipped" and rows["ghost"]["skip_reason"] == "no adapter"
    assert rows["ghost"]["silent_error"] == ""  # skipped is blank, never a made-up zero
    assert result.decisions[0].rejected["ghost"] == "no adapter"
    assert any("IoU" in n for n in result.notes)


def test_run_refuses_a_held_out_page(base: Container, workspace: tuple[Path, Path]) -> None:
    gold, held = workspace
    with pytest.raises(HeldOutLeak):
        run_bakeoff(
            ["A5"], gold, gold.parent / "o.csv", evaluator=lambda c, k: report(), base=base,
            held_out=held, contestants=[],
        )  # fmt: skip


# --------------------------------------------------------------------------- decision rule

CONFIG = BakeoffConfig()


def test_decision_rule_prefers_lower_silent_error() -> None:
    rows = [
        row("slow_accurate", Stage.printed_ocr, silent=0.05, local=False, p50=9000),
        row("fast_local", Stage.printed_ocr, silent=0.12, p50=100),
    ]
    decision = decide_stage(Stage.printed_ocr, rows, "fast_local", config=CONFIG)
    assert decision.winner == "slow_accurate"  # accuracy beats speed and locality


def test_decision_rule_tie_goes_to_local_then_faster() -> None:
    rows = [
        row("cloud", Stage.vision, silent=0.10, local=False, p50=100),
        row("local_slow", Stage.vision, silent=0.10, p50=900),
        row("local_fast", Stage.vision, silent=0.10, p50=300),
    ]
    decision = decide_stage(Stage.vision, rows, "local_slow", config=CONFIG)
    assert decision.winner == "local_fast"
    assert "tie" in decision.reason


def test_decision_with_nothing_run_names_no_winner() -> None:
    rows = [row("a", status="skipped", silent=None, p50=None, mb=None)]
    decision = decide_stage(Stage.region, rows, "a", config=CONFIG)
    assert decision.winner is None


def test_region_challenger_must_be_measured_to_win() -> None:
    rows = [row("rule_based", isolated=False, silent=0.2), row("yolo", isolated=False, silent=0.1)]
    decision = decide_stage(Stage.region, rows, "rule_based", config=CONFIG)
    assert decision.winner == "rule_based"
    assert "--isolate" in decision.rejected["yolo"]


@pytest.mark.parametrize(
    ("challenger", "wins"),
    [
        (row("yolo", silent=0.1, p50=1300, mb=260.0), True),  # +300 ms, +60 MB: inside both limits
        (row("yolo", silent=0.1, p50=1500, mb=260.0), False),  # +500 ms
        (row("yolo", silent=0.1, p50=1300, mb=400.0), False),  # +200 MB
        (row("yolo", silent=0.3, p50=1100, mb=210.0), False),  # cheap but less accurate
    ],
)
def test_region_challenger_held_to_t16_limits(challenger: ContestantRow, wins: bool) -> None:
    rows = [row("rule_based", silent=0.2, p50=1000, mb=200.0), challenger]
    decision = decide_stage(Stage.region, rows, "rule_based", config=CONFIG)
    assert (decision.winner == "yolo") is wins


# --------------------------------------------------------------------------- TrOCR assumption


def test_trocr_assumption_confirmed_when_it_reads_more_without_more_silent_error() -> None:
    rows = [
        row("trocr", Stage.digit_ocr, silent=0.1, hw=0.7),
        row("tesseract_digits", Stage.digit_ocr, silent=0.1, hw=0.4),
    ]
    assert trocr_assumption(rows).startswith("confirmed")


def test_trocr_assumption_refuted_when_it_loses() -> None:
    rows = [
        row("trocr", Stage.digit_ocr, silent=0.1, hw=0.3),
        row("tesseract_digits", Stage.digit_ocr, silent=0.1, hw=0.4),
    ]
    assert trocr_assumption(rows).startswith("refuted")


def test_trocr_assumption_undetermined_when_a_reader_did_not_run() -> None:
    rows = [
        row("trocr", Stage.digit_ocr),
        row("tesseract_digits", Stage.digit_ocr, status="skipped"),
    ]
    assert trocr_assumption(rows).startswith("undetermined")
