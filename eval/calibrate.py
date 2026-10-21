"""Calibrate the S8 threshold and signal weights on the DEV split only (PLAN §5.5, T14).

Input: a signal dump (one JSON line per decided field, written by the evaluation runner, T15) and
the gold CSV. The dump is the only input that depends on the engine, so a re-run never calls a
provider. Held-out pages are refused, never used (project rule §10).

Gold CSV columns: page_id, field, value, split. `value` empty means the field is absent on the
page. `split` is "dev" or "held_out" and may be omitted when eval/held_out.txt lists the held-out
pages. ASSUMPTION: this layout is not yet fixed by make_gold; adjust `read_gold` if it differs.

Search: threshold 0.60-0.95 step 0.01 for each candidate weight vector; coordinate descent on the
weights in steps of 0.04 that keep the sum at 1.00. Objective: maximize flag recall subject to
flag_rate <= max_flag_rate, tie-break on lower silent error, then fewer flagged fields (each
flagged field is a cloud call or a human look).

Run:  python -m eval.calibrate --gold eval/gold.csv --split dev
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path
import sys
from typing import Literal

from pydantic import Field

from backend.core.errors import SafeLineError
from backend.core.models import FieldKey, Strict
from backend.core.pipeline.s8_score import FieldEvaluation
from backend.core.scoring import (
    DEFAULT_WEIGHTS,
    ScoringConfig,
    ScoringWeights,
    Signals,
    combined_score,
    decide,
    scoring_from_mapping,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = REPO_ROOT / "config" / "models.yaml"
DEFAULT_HELD_OUT = REPO_ROOT / "eval" / "held_out.txt"
DEFAULT_SIGNALS = REPO_ROOT / "eval" / "results" / "signals.jsonl"

# Structural: PLAN §5.5 search grid, in hundredths so no float drift accumulates.
THRESHOLD_LO, THRESHOLD_HI, THRESHOLD_STEP = 60, 95, 1
WEIGHT_STEP = 4  # hundredths: +-0.04
WEIGHT_TOTAL = 100  # hundredths: the weights sum to 1.00
MAX_DESCENT_ROUNDS = 25  # structural stop; each round strictly improves, so this is never reached

BEGIN_MARK = "# BEGIN calibrated"
END_MARK = "# END calibrated"
_WEIGHT_NAMES = ("agree", "source", "label", "margin", "pattern", "conf", "image")


class HeldOutRefused(SafeLineError):
    """Calibration was asked to use the held-out split, which is never used for tuning."""

    code = "HELD_OUT_REFUSED"


class CalibrationInputError(SafeLineError):
    """The gold file, the signal dump or models.yaml is unusable (missing column, no rows,
    missing markers)."""

    code = "CALIBRATION_INPUT"


class SignalRecord(Strict):
    """One decided field. `gate` says how the field is flagged:
    "accepted" - level A/B/C regardless of score; "flagged" - level D regardless of score;
    "score"    - level C if the combined score reaches the threshold, else D."""

    page_id: str
    field: FieldKey
    predicted: str | None
    gate: Literal["accepted", "flagged", "score"]
    signals: Signals | None = None


class Metrics(Strict):
    n_fields: int
    n_wrong: int
    flagged: int
    flag_recall: float  # share of wrong fields that were flagged; 1.0 when none are wrong
    flag_rate: float  # share of all fields that were flagged
    silent_error: float  # share of all fields that are wrong and NOT flagged


class CalibrationResult(Strict):
    split: Literal["dev"]
    threshold: float
    weights: ScoringWeights
    metrics: Metrics
    feasible: bool  # flag_rate <= max_flag_rate was reachable
    target_met: bool  # flag_recall >= target_flag_recall
    excluded_held_out: int = Field(ge=0)
    unlabeled: int = Field(ge=0)


def to_record(page_id: str, evaluation: FieldEvaluation, cfg: ScoringConfig) -> SignalRecord:
    """Reduce an S8 evaluation to the record calibration needs. No candidate lists, no images."""
    result = evaluation.result
    if evaluation.evidence is None:
        return SignalRecord(page_id=page_id, field=result.field, predicted=None, gate="flagged")
    d = decide(evaluation.evidence, cfg)
    gate: Literal["accepted", "flagged", "score"]
    if d.gate == "score":
        gate = "score"
    else:
        gate = "flagged" if d.level is not None and d.level.value == "D" else "accepted"
    return SignalRecord(
        page_id=page_id,
        field=result.field,
        predicted=result.value,
        gate=gate,
        signals=evaluation.signals,
    )


def write_signals(path: Path, records: list[SignalRecord]) -> None:
    """Write the dump as JSON lines (UTF-8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8", newline="\n"
    )


def read_signals(path: Path) -> list[SignalRecord]:
    if not path.is_file():
        raise CalibrationInputError(f"signal dump not found: {path}")
    return [
        SignalRecord.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_gold(path: Path) -> dict[tuple[str, str], tuple[str, str | None]]:
    """(page_id, field) -> (gold value, split or None). Raises if the file or a column is missing."""
    if not path.is_file():
        raise CalibrationInputError(f"gold file not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = {"page_id", "field", "value"} - set(reader.fieldnames or [])
        if missing:
            raise CalibrationInputError(f"gold file lacks columns: {sorted(missing)}")
        return {
            (row["page_id"].strip(), row["field"].strip()): (
                (row["value"] or "").strip(),
                (row.get("split") or "").strip() or None,
            )
            for row in reader
        }


def read_held_out(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _flagged(r: SignalRecord, weights: ScoringWeights, threshold: float) -> bool:
    if r.gate == "accepted":
        return False
    if r.gate == "flagged" or r.signals is None:
        return True
    return combined_score(r.signals, weights) < threshold


def evaluate_metrics(
    records: list[tuple[SignalRecord, str]], weights: ScoringWeights, threshold: float
) -> Metrics:
    """Flag recall, flag rate and silent error for `(record, gold value)` pairs. Never raises;
    an empty input reports zeros with recall 1.0."""
    n = len(records)
    wrong = flagged = flagged_wrong = 0
    for r, gold in records:
        is_wrong = (r.predicted or "") != gold
        is_flagged = _flagged(r, weights, threshold)
        wrong += is_wrong
        flagged += is_flagged
        flagged_wrong += is_wrong and is_flagged
    return Metrics(
        n_fields=n,
        n_wrong=wrong,
        flagged=flagged,
        flag_recall=flagged_wrong / wrong if wrong else 1.0,
        flag_rate=flagged / n if n else 0.0,
        silent_error=(wrong - flagged_wrong) / n if n else 0.0,
    )


def _objective(m: Metrics, max_flag_rate: float) -> tuple[bool, float, float, int]:
    """Larger is better. Feasible beats infeasible; infeasible configs minimise the flag rate."""
    feasible = m.flag_rate <= max_flag_rate
    primary = m.flag_recall if feasible else -m.flag_rate
    return feasible, primary, -m.silent_error, -m.flagged


def _weights_from_units(units: list[int]) -> ScoringWeights:
    return ScoringWeights(
        **{n: u / WEIGHT_TOTAL for n, u in zip(_WEIGHT_NAMES, units, strict=True)}
    )


def _best_threshold(
    pairs: list[tuple[SignalRecord, str]], weights: ScoringWeights, max_flag_rate: float
) -> tuple[tuple[bool, float, float, int], float, Metrics]:
    best: tuple[tuple[bool, float, float, int], float, Metrics] | None = None
    for step in range(THRESHOLD_LO, THRESHOLD_HI + 1, THRESHOLD_STEP):
        threshold = step / 100
        m = evaluate_metrics(pairs, weights, threshold)
        key = _objective(m, max_flag_rate)
        if best is None or key > best[0]:
            best = (key, threshold, m)
    assert best is not None  # grid is non-empty
    return best


def calibrate(
    gold: Path,
    signals: Path,
    *,
    split: Literal["dev"] = "dev",
    target_flag_recall: float = 0.95,
    max_flag_rate: float = 0.15,
    held_out: Path | None = DEFAULT_HELD_OUT,
    start: ScoringWeights = DEFAULT_WEIGHTS,
) -> CalibrationResult:
    """Coordinate-descent sweep. REFUSES to run on the held-out split (HeldOutRefused).
    Fields on held-out pages and fields without a gold row are excluded and counted."""
    if split != "dev":
        raise HeldOutRefused("calibration runs on the dev split only")
    gold_rows = read_gold(gold)
    held = read_held_out(held_out)

    pairs: list[tuple[SignalRecord, str]] = []
    excluded = unlabeled = 0
    for r in read_signals(signals):
        row = gold_rows.get((r.page_id, r.field.value))
        row_split = row[1] if row is not None else None
        if r.page_id in held or row_split == "held_out":
            excluded += 1
        elif row is None:
            unlabeled += 1
        else:
            pairs.append((r, row[0]))
    if not pairs:
        raise CalibrationInputError("no labelled dev-split fields to calibrate on")

    units = [round(w * WEIGHT_TOTAL) for w in start.as_tuple()]
    best_key, best_threshold, best_metrics = _best_threshold(
        pairs, _weights_from_units(units), max_flag_rate
    )
    for _ in range(MAX_DESCENT_ROUNDS):
        improved = False
        for i in range(len(units)):
            for j in range(len(units)):
                if i == j or units[j] < WEIGHT_STEP:
                    continue
                trial = list(units)
                trial[i] += WEIGHT_STEP
                trial[j] -= WEIGHT_STEP
                key, threshold, metrics = _best_threshold(
                    pairs, _weights_from_units(trial), max_flag_rate
                )
                if key > best_key:
                    units, best_key, best_threshold, best_metrics = trial, key, threshold, metrics
                    improved = True
        if not improved:
            break

    return CalibrationResult(
        split="dev",
        threshold=best_threshold,
        weights=_weights_from_units(units),
        metrics=best_metrics,
        feasible=best_metrics.flag_rate <= max_flag_rate,
        target_met=best_metrics.flag_recall >= target_flag_recall,
        excluded_held_out=excluded,
        unlabeled=unlabeled,
    )


def render_block(result: CalibrationResult, on: date) -> str:
    """The text between the calibration markers, including both marker lines."""
    m = result.metrics
    notes = []
    if not result.feasible:
        notes.append("FLAG-RATE CAP NOT REACHABLE")
    if not result.target_met:
        notes.append("RECALL TARGET NOT MET")
    flag = f"; {', '.join(notes)}" if notes else ""
    w = result.weights
    lines = [
        f"  {BEGIN_MARK} - eval/calibrate.py rewrites everything up to END; do not edit by hand",
        f"  # provenance: {result.split} split, {m.n_fields} fields, {on.isoformat()}; "
        f"flag recall {m.flag_recall:.3f}, flag rate {m.flag_rate:.3f}, "
        f"silent error {m.silent_error:.4f}{flag}",
        f"  escalation_score_threshold: {result.threshold:.2f}  # also in .env",
        "  weights:",
        *(f"    {n}: {getattr(w, n):.2f}" for n in _WEIGHT_NAMES),
        f"  {END_MARK}",
    ]
    return "\n".join(lines)


def write_config(result: CalibrationResult, models_yaml: Path, on: date) -> None:
    """Replace the marked block of models.yaml, leaving every other line and comment intact."""
    lines = models_yaml.read_text(encoding="utf-8").split("\n")
    try:
        begin = next(i for i, ln in enumerate(lines) if BEGIN_MARK in ln)
        end = next(i for i, ln in enumerate(lines) if END_MARK in ln and i > begin)
    except StopIteration:
        raise CalibrationInputError(f"{models_yaml} lacks the calibration markers") from None
    lines[begin : end + 1] = render_block(result, on).split("\n")
    models_yaml.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def load_scoring(models_yaml: Path) -> tuple[ScoringConfig, ScoringWeights]:
    """Parse the `scoring:` block of models.yaml."""
    import yaml  # type: ignore[import-untyped]  # local: only the eval tooling needs PyYAML

    section = yaml.safe_load(models_yaml.read_text(encoding="utf-8"))["scoring"]
    return scoring_from_mapping(section)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Calibrate S8 on the dev split.")
    p.add_argument("--gold", type=Path, required=True)
    p.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    p.add_argument("--split", choices=["dev", "held_out"], default="dev")
    p.add_argument("--models", type=Path, default=DEFAULT_MODELS)
    p.add_argument("--held-out", type=Path, default=DEFAULT_HELD_OUT)
    p.add_argument("--target-flag-recall", type=float, default=0.95)
    p.add_argument("--max-flag-rate", type=float, default=0.15)
    p.add_argument("--dry-run", action="store_true", help="report only; do not write models.yaml")
    args = p.parse_args(argv)
    out = sys.stdout.write
    try:
        _, start = load_scoring(args.models)
        result = calibrate(
            args.gold,
            args.signals,
            split=args.split,
            target_flag_recall=args.target_flag_recall,
            max_flag_rate=args.max_flag_rate,
            held_out=args.held_out,
            start=start,
        )
    except SafeLineError as exc:
        sys.stderr.write(f"{exc.code}: {exc.detail}\n")
        return 2
    m = result.metrics
    out(
        f"threshold {result.threshold:.2f} | flag recall {m.flag_recall:.2f} | "
        f"flag rate {m.flag_rate:.2f} | silent error {m.silent_error:.3f}\n"
    )
    if not result.feasible:
        out("WARNING: no setting met the flag-rate cap; reporting the lowest flag rate.\n")
    if not result.target_met:
        out(f"WARNING: flag recall target {args.target_flag_recall:.2f} not met within the cap.\n")
    if args.dry_run:
        return 0
    today = date.today()
    write_config(result, args.models, today)
    out(f"weights written to {args.models.name} ({result.split} split, {today.isoformat()})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
