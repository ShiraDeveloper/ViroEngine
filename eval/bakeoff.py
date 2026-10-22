"""Reader and region-detector bake-off on the hard dev pages (PLAN §10.6, T17).

    python -m eval.bakeoff --gold eval/gold.csv --out eval/results/bakeoff.csv [--owner ahuba|shira]
                           [--isolate] [--only NAME]

Each contestant changes exactly one stage of the same baseline container (Tesseract + TrOCR +
rule-based regions, local only); everything else is held constant. A contestant that cannot run
(no adapter, no model file, no key) is **recorded as skipped with its reason** - never as a zero
(project rule §10, "never guess"). The decision rule was declared before any run: the lowest
silent error wins; on a tie the local, then the faster contestant wins. For region detectors the
T16 rule also applies: a challenger must add less than ``region_max_added_ms`` and
``region_max_added_mb`` over the baseline (measured, so it needs ``--isolate``); mean region IoU is
not measured because there are no reviewed gold boxes yet, and the report says so.

EVALUATION ONLY. ``backend/core`` never imports this module. Dev split only: a held-out label in
the page list stops the run. Output holds counts and rates, never a field value.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import csv
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Final

import structlog

from backend.container import Container, build_container
from backend.core.errors import SafeLineError
from backend.core.models import Strict
from backend.runner import load_known_hashes
from backend.settings import Settings
from eval.calibrate import read_held_out
from eval.run_eval import (
    DEFAULT_GOLD,
    DEFAULT_HELD_OUT,
    DEFAULT_PDFS,
    DEFAULT_RESULTS,
    REPO_ROOT,
    EvalInputError,
    EvalReport,
    HeldOutLeak,
    evaluate,
    read_gold,
    read_split_column,
)

log = structlog.get_logger()

# PLAN §10.6 / T17: hard pages, all in the development split (a test and `check_pages` enforce it).
HARD_PAGES: Final = (
    "A2", "A9", "A13", "A14", "A17", "A20", "A24",
    "B6", "B10", "B12", "B14", "B16", "B18", "B20", "B22",
)  # fmt: skip
DEFAULT_OUT: Final = DEFAULT_RESULTS / "bakeoff.csv"
DEFAULT_MODELS: Final = REPO_ROOT / "config" / "models.yaml"
IOU_NOTE: Final = "mean region IoU not measured: no reviewed gold boxes yet (T16)"
NO_ADAPTER: Final = "no adapter in the repository"


class Stage(StrEnum):
    printed_ocr = "printed_ocr"
    digit_ocr = "digit_ocr"
    region = "region"
    vision = "vision"


class Owner(StrEnum):
    """Who runs a contestant (PLAN T17 step 2-3): Ahuba the vision and region ones, Shira the OCR."""

    ahuba = "ahuba"
    shira = "shira"


@dataclass(frozen=True)
class Contestant:
    """One candidate for one stage. `swap` returns the baseline container with only that stage
    changed; None marks the stage's baseline, which reuses the baseline run."""

    name: str
    stage: Stage
    owner: Owner
    local: bool
    swap: Callable[[Container], Container] | None = None
    unavailable: Callable[[Container], str | None] = lambda _c: (
        None
    )  # reason, or None if it can run


class BakeoffConfig(Strict):
    """Thresholds from `config/models.yaml: bakeoff` (project rule §9)."""

    region_max_added_ms: float = 400.0
    region_max_added_mb: float = 120.0


class ContestantRow(Strict):
    """One CSV row: a contestant's measurements, or why it did not run."""

    contestant: str
    stage: Stage
    owner: Owner
    local: bool
    status: str  # "ran" | "skipped"
    skip_reason: str = ""
    isolated: bool = False  # True when it ran in its own process, so memory is its own
    pages: int = 0
    fields: int = 0
    accuracy: float | None = None
    printed_accuracy: float | None = None
    handwritten_accuracy: float | None = None
    handwritten_n: int = 0
    silent_error: float | None = None
    flag_rate: float | None = None
    p50_latency_ms: int | None = None
    peak_memory_mb: float | None = None
    cloud_calls: int = 0
    winner: bool = False


class StageDecision(Strict):
    """The verdict for one stage: the winner (None when nothing ran) and why."""

    stage: Stage
    winner: str | None
    reason: str
    rejected: dict[str, str]


class BakeoffReport(Strict):
    """What a bake-off measured. Every contestant is a row, winners and losers alike."""

    date: date
    split: str
    pages: list[str]
    rows: list[ContestantRow]
    decisions: list[StageDecision]
    trocr_assumption: str
    notes: list[str]


Evaluator = Callable[[Contestant, Container], EvalReport]

CSV_COLUMNS: Final = (
    "contestant", "stage", "owner", "local", "status", "skip_reason", "isolated", "pages",
    "fields", "accuracy", "printed_accuracy", "handwritten_accuracy", "handwritten_n",
    "silent_error", "flag_rate", "p50_latency_ms", "peak_memory_mb", "cloud_calls", "winner",
)  # fmt: skip


# --------------------------------------------------------------------------- contestants


def _with_layers(base: Container, **layers: bool) -> Container:
    updated = base.pipeline.layers.model_copy(update=layers)
    return replace(base, pipeline=base.pipeline.model_copy(update={"layers": updated}))


def _tesseract_reads_digits(base: Container) -> Container:
    """The digit reader switched off: S7's second read is Tesseract's alone (no TrOCR)."""
    return _with_layers(base, digit_reader=False)


def _yolo_regions(base: Container) -> Container:
    from backend.adapters.regions.yolo_onnx import YoloPreferred, YoloRegionDetector

    yolo = YoloRegionDetector(settings=base.settings, storage=base.storage)
    kinds = base.settings.yolo_kinds_list
    return replace(base, regions=YoloPreferred(yolo=yolo, fallback=base.regions, kinds=kinds))


def _yolo_missing(base: Container) -> str | None:
    from backend.adapters.regions.yolo_onnx import YoloRegionDetector

    detector = YoloRegionDetector(settings=base.settings, storage=base.storage)
    return None if detector.available else "no trained model file (ADR-016: experiment not run)"


def _gemini(model_field: str) -> Callable[[Container], Container]:
    def swap(base: Container) -> Container:
        from backend.adapters.vision.chain import VisionChainAdapter

        model = getattr(base.settings, model_field)
        settings = base.settings.model_copy(update={"gemini_model": model, "local_only": False})
        return replace(base, settings=settings, vision=VisionChainAdapter(settings=settings))

    return swap


def _no_gemini_key(base: Container) -> str | None:
    return None if base.settings.gemini_keys_list else "no GEMINI_API_KEYS configured"


def _not_built(_base: Container) -> str:
    return NO_ADAPTER


def _digit_model_missing(base: Container) -> str | None:
    root = Path(base.settings.models_dir)
    needed = (
        base.settings.trocr_vision_file,
        base.settings.trocr_text_file,
        base.settings.trocr_vocab_file,
    )
    return None if all((root / name).is_file() for name in needed) else "TrOCR model files absent"


def default_contestants() -> list[Contestant]:
    """Every contestant of PLAN §10.6. Baselines come first in each stage."""
    return [
        Contestant("tesseract", Stage.printed_ocr, Owner.shira, True),
        Contestant("surya", Stage.printed_ocr, Owner.shira, True, unavailable=_not_built),
        Contestant(
            "mistral_ocr_4.1", Stage.printed_ocr, Owner.shira, False, unavailable=_not_built
        ),
        Contestant("azure_di_f0", Stage.printed_ocr, Owner.shira, False, unavailable=_not_built),
        Contestant("trocr", Stage.digit_ocr, Owner.shira, True, unavailable=_digit_model_missing),
        Contestant(
            "tesseract_digits", Stage.digit_ocr, Owner.shira, True, swap=_tesseract_reads_digits
        ),
        Contestant("rule_based", Stage.region, Owner.ahuba, True),
        Contestant("yolo_onnx", Stage.region, Owner.ahuba, True, _yolo_regions, _yolo_missing),
        Contestant("no_vision", Stage.vision, Owner.ahuba, True),
        Contestant(
            "gemini_flash",
            Stage.vision,
            Owner.ahuba,
            False,
            _gemini("gemini_model"),
            _no_gemini_key,
        ),
        Contestant(
            "gemini_flash_lite",
            Stage.vision,
            Owner.ahuba,
            False,
            _gemini("gemini_model_lite"),
            _no_gemini_key,
        ),
    ]


# --------------------------------------------------------------------------- guards and decision


def check_pages(pages: Sequence[str], *, held_out: set[str], gold_labels: set[str]) -> list[str]:
    """`pages` unchanged, once they are known to be dev-split pages with gold rows.
    Raises HeldOutLeak for a held-out label and EvalInputError when the held-out list is
    unknown (a page cannot be shown to be dev) or a page has no gold row."""
    if not held_out:
        raise EvalInputError(
            "no held-out list (eval/held_out.txt): cannot show the bake-off pages are dev pages"
        )
    leaked = sorted(set(pages) & held_out)
    if leaked:
        raise HeldOutLeak(f"held-out page(s) in the bake-off page list: {', '.join(leaked)}")
    missing = sorted(set(pages) - gold_labels)
    if missing:
        raise EvalInputError(f"bake-off page(s) with no gold row: {', '.join(missing)}")
    return list(pages)


def _row_from_report(
    contestant: Contestant, report: EvalReport, *, isolated: bool
) -> ContestantRow:
    return ContestantRow(
        contestant=contestant.name,
        stage=contestant.stage,
        owner=contestant.owner,
        local=contestant.local,
        status="ran",
        isolated=isolated,
        pages=report.pages,
        fields=report.fields,
        accuracy=report.accuracy,
        printed_accuracy=report.printed_accuracy,
        handwritten_accuracy=report.handwritten_accuracy,
        handwritten_n=report.handwritten_n,
        silent_error=report.silent_error,
        flag_rate=report.flag_rate,
        p50_latency_ms=report.p50_latency_ms,
        peak_memory_mb=report.peak_memory_mb,
        cloud_calls=report.cloud_calls,
    )


def _skipped(contestant: Contestant, reason: str) -> ContestantRow:
    return ContestantRow(
        contestant=contestant.name,
        stage=contestant.stage,
        owner=contestant.owner,
        local=contestant.local,
        status="skipped",
        skip_reason=reason,
    )


def _ranking(row: ContestantRow) -> tuple[float, int, float, str]:
    """Lowest silent error first; on a tie the local one, then the faster, then by name."""
    return (
        row.silent_error if row.silent_error is not None else float("inf"),
        0 if row.local else 1,
        float(row.p50_latency_ms) if row.p50_latency_ms is not None else float("inf"),
        row.contestant,
    )


def decide_stage(
    stage: Stage,
    rows: Sequence[ContestantRow],
    baseline: str,
    *,
    config: BakeoffConfig,
) -> StageDecision:
    """Apply the pre-declared rule to the rows of one stage. A region challenger is also held to
    the T16 latency and memory limits over `baseline`; when they cannot be measured (not
    isolated) it is rejected, not waved through."""
    mine = [r for r in rows if r.stage is stage]
    ran = [r for r in mine if r.status == "ran" and r.silent_error is not None]
    rejected = {
        r.contestant: r.skip_reason or "no silent-error measurement" for r in mine if r not in ran
    }
    if not ran:
        return StageDecision(
            stage=stage, winner=None, reason="no contestant ran", rejected=rejected
        )
    base = next((r for r in ran if r.contestant == baseline), None)
    eligible: list[ContestantRow] = []
    for row in ran:
        if stage is Stage.region and row.contestant != baseline:
            problem = _region_cost_problem(row, base, config)
            if problem is not None:
                rejected[row.contestant] = problem
                continue
        eligible.append(row)
    winner = min(eligible, key=_ranking)
    tied = [r for r in eligible if r.silent_error == winner.silent_error]
    why = f"lowest silent error {winner.silent_error:.4f}"
    if len(tied) > 1:
        why += f" (tie with {len(tied) - 1} other; local, then faster wins)"
    return StageDecision(stage=stage, winner=winner.contestant, reason=why, rejected=rejected)


def _region_cost_problem(
    row: ContestantRow, base: ContestantRow | None, config: BakeoffConfig
) -> str | None:
    """Why a region challenger fails the T16 cost limits, or None when it passes."""
    if base is None or not (row.isolated and base.isolated):
        return "added latency and memory not measured (run with --isolate)"
    if row.p50_latency_ms is None or base.p50_latency_ms is None:
        return "latency not measured"
    if row.peak_memory_mb is None or base.peak_memory_mb is None:
        return "memory not measured"
    added_ms = row.p50_latency_ms - base.p50_latency_ms
    added_mb = row.peak_memory_mb - base.peak_memory_mb
    if added_ms >= config.region_max_added_ms:
        return f"adds {added_ms} ms (limit {config.region_max_added_ms:g})"
    if added_mb >= config.region_max_added_mb:
        return f"adds {added_mb:.0f} MB (limit {config.region_max_added_mb:g})"
    return None


def trocr_assumption(rows: Sequence[ContestantRow]) -> str:
    """Whether the T13 assumption (TrOCR transfers to handwritten digits) held here, in words.
    Undetermined unless both digit readers ran and saw handwriting."""
    by_name = {r.contestant: r for r in rows if r.stage is Stage.digit_ocr and r.status == "ran"}
    trocr, plain = by_name.get("trocr"), by_name.get("tesseract_digits")
    if trocr is None or plain is None:
        return "undetermined: both digit readers must run"
    if (
        trocr.handwritten_accuracy is None
        or plain.handwritten_accuracy is None
        or trocr.silent_error is None
        or plain.silent_error is None
    ):
        return "undetermined: no handwritten field was scored"
    better = trocr.handwritten_accuracy > plain.handwritten_accuracy
    no_worse = trocr.silent_error <= plain.silent_error
    if better and no_worse:
        return (
            f"confirmed on {trocr.handwritten_n} handwritten fields: TrOCR "
            f"{trocr.handwritten_accuracy:.3f} vs Tesseract {plain.handwritten_accuracy:.3f}"
        )
    return (
        f"refuted on {trocr.handwritten_n} handwritten fields: TrOCR "
        f"{trocr.handwritten_accuracy:.3f} vs Tesseract {plain.handwritten_accuracy:.3f}, "
        f"silent error {trocr.silent_error:.4f} vs {plain.silent_error:.4f}"
    )


def load_config(path: Path = DEFAULT_MODELS) -> BakeoffConfig:
    """The `bakeoff:` section of models.yaml (defaults when absent). Raises
    pydantic.ValidationError for an unknown key."""
    import yaml  # type: ignore[import-untyped,unused-ignore]  # eval tooling only

    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return BakeoffConfig.model_validate(data.get("bakeoff", {}))


# --------------------------------------------------------------------------- run


def run_bakeoff(
    pages: Sequence[str],
    gold: Path,
    out: Path,
    *,
    contestants: Sequence[Contestant] | None = None,
    evaluator: Evaluator,
    base: Container,
    isolated: bool = False,
    held_out: Path | None = DEFAULT_HELD_OUT,
    config: BakeoffConfig | None = None,
    today: date | None = None,
) -> BakeoffReport:
    """Run every contestant on `pages` and write `out` (CSV). `evaluator(contestant, container)`
    measures one contestant on a container already changed in that stage only; `isolated` records
    that each ran in its own process. Raises HeldOutLeak / EvalInputError (see `check_pages`)."""
    held = read_held_out(held_out) | (read_split_column(gold) if gold.is_file() else set())
    chosen = check_pages(pages, held_out=held, gold_labels=set(read_gold(gold)))
    cfg = config or BakeoffConfig()
    listed = list(contestants) if contestants is not None else default_contestants()
    rows: list[ContestantRow] = []
    baseline_report: EvalReport | None = None
    for contestant in listed:
        reason = contestant.unavailable(base)
        if reason is not None:
            rows.append(_skipped(contestant, reason))
            continue
        if contestant.swap is None:
            if baseline_report is None:
                baseline_report = evaluator(contestant, base)
            report = baseline_report
        else:
            report = evaluator(contestant, contestant.swap(base))
        rows.append(_row_from_report(contestant, report, isolated=isolated))
        log.info("bakeoff_contestant_done", stage="T17", contestant=contestant.name)

    baselines = {c.stage: c.name for c in listed if c.swap is None}
    decisions = [
        decide_stage(stage, rows, baselines.get(stage, ""), config=cfg)
        for stage in Stage
        if any(r.stage is stage for r in rows)
    ]
    winners = {d.winner for d in decisions if d.winner is not None}
    rows = [
        r.model_copy(update={"winner": r.contestant in winners and r.status == "ran"}) for r in rows
    ]
    report_out = BakeoffReport(
        date=today or date.today(),
        split="dev (hard pages)",
        pages=chosen,
        rows=rows,
        decisions=decisions,
        trocr_assumption=trocr_assumption(rows),
        notes=[IOU_NOTE]
        + ([] if isolated else ["not isolated: memory is the whole process peak, cumulative"]),
    )
    write_csv(out, report_out)
    return report_out


def write_csv(path: Path, report: BakeoffReport) -> None:
    """UTF-8 CSV with a BOM (opens in a spreadsheet), LF line ends. Rates and counts only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in report.rows:
            writer.writerow({k: _cell(v) for k, v in row.model_dump().items()})


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render(report: BakeoffReport) -> str:
    """The table and the verdicts as text."""
    lines = [
        f"bake-off | {report.date.isoformat()} | {report.split} | {len(report.pages)} pages",
        "contestant | stage | status | printed acc | handwritten acc | silent err | p50 ms | MB",
    ]
    for r in report.rows:

        def f(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.3f}"

        mark = " *" if r.winner else ""
        detail = (
            f"{f(r.printed_accuracy)} | {f(r.handwritten_accuracy)} | {f(r.silent_error)} | "
            f"{r.p50_latency_ms if r.p50_latency_ms is not None else 'n/a'} | "
            f"{f'{r.peak_memory_mb:.0f}' if r.peak_memory_mb is not None else 'n/a'}"
            if r.status == "ran"
            else f"skipped: {r.skip_reason}"
        )
        lines.append(f"{r.contestant}{mark} | {r.stage.value} | {r.status} | {detail}")
    lines += [
        f"winner ({d.stage.value}): {d.winner or 'none'} - {d.reason}" for d in report.decisions
    ]
    lines.append(f"TrOCR assumption: {report.trocr_assumption}")
    lines += [f"note: {n}" for n in report.notes]
    return "\n".join(lines)


# --------------------------------------------------------------------------- command line


def in_process_evaluator(
    gold: Path, pages: Sequence[str], held_out: Path | None, pdfs: tuple[Path, ...]
) -> Evaluator:
    """Evaluate in this process. Vision contestants may reach the provider for known sample
    pages only (their hashes), never for any other document."""

    def run(contestant: Contestant, container: Container) -> EvalReport:
        return evaluate(
            gold,
            split="dev",
            local_only=contestant.local,
            held_out=held_out,
            pdfs=pdfs,
            services=container,
            pages=pages,
            known_hashes=load_known_hashes(),
        )

    return run


def isolated_evaluator(
    gold: Path, pages: Sequence[str], held_out: Path | None, pdfs: tuple[Path, ...]
) -> Evaluator:
    """Evaluate each contestant in a child process so its memory peak is its own."""

    def run(contestant: Contestant, container: Container) -> EvalReport:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            command = [
                sys.executable, "-m", "eval.bakeoff", "--only", contestant.name,
                "--emit-report", str(report_path), "--gold", str(gold),
                "--pages", *pages, "--out", str(Path(tmp) / "unused.csv"),
            ]  # fmt: skip
            if held_out is not None:
                command += ["--held-out", str(held_out)]
            for pdf in pdfs:
                command += ["--pdf", str(pdf)]
            done = subprocess.run(command, check=False, cwd=REPO_ROOT)
            if done.returncode != 0 or not report_path.is_file():
                raise EvalInputError(f"contestant {contestant.name} failed in its own process")
            return EvalReport.model_validate_json(report_path.read_text(encoding="utf-8"))

    return run


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Exit 0 when the sample data is absent (CI), 2 on unusable input."""
    p = argparse.ArgumentParser(description="Reader and region-detector bake-off (hard dev pages).")
    p.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--held-out", type=Path, default=DEFAULT_HELD_OUT)
    p.add_argument("--pdf", type=Path, action="append", help="sample PDF (repeatable)")
    p.add_argument("--pages", nargs="+", default=list(HARD_PAGES))
    p.add_argument("--owner", choices=[o.value for o in Owner], help="run only this owner's")
    p.add_argument("--isolate", action="store_true", help="one child process per contestant")
    p.add_argument("--only", help="(internal) run one contestant and write --emit-report")
    p.add_argument("--emit-report", type=Path, help="(internal) where --only writes its report")
    args = p.parse_args(argv)
    pdfs = tuple(args.pdf) if args.pdf else DEFAULT_PDFS
    if not args.gold.is_file() or not any(f.is_file() for f in pdfs):
        sys.stdout.write("skip: eval/gold.csv or the sample PDFs are absent (git-ignored data)\n")
        return 0
    try:
        base = build_container(Settings(local_only=True))
        listed = default_contestants()
        if args.only:
            return _run_one(args, listed, base, pdfs)
        if args.owner:
            listed = [c for c in listed if c.owner.value == args.owner]
        make = isolated_evaluator if args.isolate else in_process_evaluator
        report = run_bakeoff(
            args.pages,
            args.gold,
            args.out,
            contestants=listed,
            evaluator=make(args.gold, args.pages, args.held_out, pdfs),
            base=base,
            isolated=args.isolate,
            held_out=args.held_out,
            config=load_config(),
        )
    except SafeLineError as exc:
        sys.stderr.write(f"{exc.code}: {exc.detail}\n")
        return 2
    sys.stdout.write(render(report) + "\n")
    return 0


def _run_one(
    args: argparse.Namespace, listed: list[Contestant], base: Container, pdfs: tuple[Path, ...]
) -> int:
    """Child mode: measure one contestant and write its EvalReport as JSON."""
    contestant = next((c for c in listed if c.name == args.only), None)
    if contestant is None or args.emit_report is None:
        sys.stderr.write("EVAL_INPUT: unknown contestant or no --emit-report\n")
        return 2
    container = base if contestant.swap is None else contestant.swap(base)
    report = in_process_evaluator(args.gold, args.pages, args.held_out, pdfs)(contestant, container)
    args.emit_report.write_text(json.dumps(json.loads(report.model_dump_json())), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
