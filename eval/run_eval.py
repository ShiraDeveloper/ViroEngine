"""Evaluation runner: measure extraction accuracy against the gold set (PLAN §10, T15).

    python -m eval.run_eval --gold eval/gold.csv --split dev --local-only

The runner uploads the sample PDFs, runs the pipeline on the pages of the chosen split and
compares every decided field with the confirmed gold value. Split rules (project rule §10):
`dev` never processes a held-out page and refuses to run if it cannot tell which pages those
are; `heldout` prints a final-report banner; numbers are never reported without their split and
date. Prints counts, rates and field names only - never a field value.

If the sample PDFs or the gold file are missing the runner exits 0 with a skip message, so CI
works without `reference/`.
"""

from __future__ import annotations

import argparse
from collections.abc import Collection, Iterator
from contextlib import contextmanager, nullcontext
import csv
from dataclasses import dataclass, replace
from datetime import date
import errno
from pathlib import Path
import re
import socket
import sys
from typing import Any, Final, Literal
import uuid

from pydantic import Field

from backend.container import Container, build_container, load_pipeline_config
from backend.core.errors import SafeLineError
from backend.core.models import ConfidenceLevel, FieldKey, FieldResult, SourceKind, Strict
from backend.core.normalize import (
    normalize_central,
    normalize_commitment,
    normalize_id,
    normalize_name,
)
from backend.core.pipeline.orchestrator import apply_labels, iter_outcomes
from backend.core.pipeline.outcome import PageOutcome
from backend.core.pipeline.s0_intake import UploadedBlob, intake
from backend.core.scoring import SENDER_NOT_LISTED
from backend.core.validators import parse_il_date
from backend.settings import Settings
from eval.calibrate import read_held_out, to_record, write_signals

Split = Literal["dev", "heldout", "all"]

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_GOLD: Final = REPO_ROOT / "eval" / "gold.csv"
DEFAULT_HELD_OUT: Final = REPO_ROOT / "eval" / "held_out.txt"
DEFAULT_RESULTS: Final = REPO_ROOT / "eval" / "results"
DEFAULT_PDFS: Final = (
    REPO_ROOT / "reference" / "samples_part1.pdf",
    REPO_ROOT / "reference" / "samples_part2.pdf",
)

# Structural: the gold file marks a field that is genuinely blank on the form (PLAN §10.1).
EMPTY_TOKEN: Final = "<EMPTY>"
# Structural: page labels of the sample PDFs are the part letter plus the 1-based page number.
LABEL_PREFIX: Final = {"samples_part1": "A", "samples_part2": "B"}
_LABELLED_STEM: Final = re.compile(r"^[A-Za-z]+\d+$")
# Structural: column names of the draft (`page_label`, `confirmed_value`) and of the final
# layout calibrate.py assumes (`page_id`, `value`); T7 has not fixed which one gold.csv uses.
_PAGE_COLUMNS: Final = ("page_label", "page_id", "page")
_VALUE_COLUMNS: Final = ("confirmed_value", "value")
# Structural: sources whose value was read from print, screen or a stamp or barcode.
_PRINTED_SOURCES: Final = frozenset(
    {SourceKind.printed, SourceKind.barcode, SourceKind.screen, SourceKind.stamp}
)


class EvalInputError(SafeLineError):
    """The gold file, the held-out list or the sample pages are unusable, or the requested split
    cannot be kept clean."""

    code = "EVAL_INPUT"


class HeldOutLeak(SafeLineError):
    """A dev run would have read a held-out page (project rule §10)."""

    code = "HELD_OUT_REFUSED"


class FieldBreakdown(Strict):
    """Accuracy of one field over the evaluated pages."""

    field: FieldKey
    n: int
    accuracy: float | None
    flag_rate: float | None
    silent_error: float | None
    top_failure_reason: str | None  # most common closed-set reason among its wrong fields


class EvalReport(Strict):
    """Everything a published number needs: the split, the date and the configuration."""

    split: Split
    date: date
    local_only: bool
    ablation: str | None
    pages: int
    failed_pages: list[str]  # labels of pages that raised; their fields count as flagged-wrong
    unlabelled_pages: int  # pages with no gold row; not scored
    fields: int
    correct: int
    accuracy: float | None
    printed_accuracy: float | None
    printed_n: int
    handwritten_accuracy: float | None
    handwritten_n: int
    silent_error: float | None
    flag_recall: float | None  # None when no field is wrong: there is nothing to recall
    flag_precision: float | None  # None when nothing is flagged
    flag_rate: float | None
    local_close_rate: float | None
    escalation_rate: float | None
    cloud_calls: int
    measured_cost_usd: float
    network_attempts: int  # socket connects tried during a --local-only run; must be 0
    p50_latency_ms: int | None
    p95_latency_ms: int | None
    peak_memory_mb: float | None
    per_field: list[FieldBreakdown] = Field(default_factory=list)


def ratio(numerator: int, denominator: int) -> float | None:
    """`numerator / denominator`, or None when there is nothing to divide by (never a made-up 0)."""
    return numerator / denominator if denominator else None


# --------------------------------------------------------------------------- gold


def _pick(names: list[str], options: tuple[str, ...], what: str, path: Path) -> str:
    for option in options:
        if option in names:
            return option
    raise EvalInputError(f"{path.name} has no {what} column (expected one of {options})")


def read_gold(path: Path) -> dict[str, dict[FieldKey, str | None]]:
    """Gold values by page label then field. `<EMPTY>` and blank become None (genuinely blank on
    the form). Raises EvalInputError for a missing file, column, unknown field or duplicate row."""
    if not path.is_file():
        raise EvalInputError(f"gold file not found: {path.name}")
    gold: dict[str, dict[FieldKey, str | None]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = list(reader.fieldnames or [])
        page_col = _pick(names, _PAGE_COLUMNS, "page", path)
        value_col = _pick(names, _VALUE_COLUMNS, "value", path)
        if "field" not in names:
            raise EvalInputError(f"{path.name} has no field column")
        for row in reader:
            label, raw_field = row[page_col].strip(), row["field"].strip()
            try:
                key = FieldKey(raw_field)
            except ValueError:
                raise EvalInputError(f"{path.name}: unknown field {raw_field!r}") from None
            if key in gold.setdefault(label, {}):
                raise EvalInputError(f"{path.name}: duplicate row for {label} {raw_field}")
            value = (row[value_col] or "").strip()
            gold[label][key] = None if value in ("", EMPTY_TOKEN) else value
    if not gold:
        raise EvalInputError(f"{path.name} holds no rows")
    return gold


def read_split_column(path: Path) -> set[str]:
    """Labels the gold file itself marks `held_out` in an optional `split` column."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = list(reader.fieldnames or [])
        if "split" not in names:
            return set()
        page_col = _pick(names, _PAGE_COLUMNS, "page", path)
        return {r[page_col].strip() for r in reader if (r["split"] or "").strip() == "held_out"}


def label_of_page(source_file: str, page_index: int) -> str | None:
    """Sample page label: `samples_part1.pdf` page 3 is A3. A file already named like a label
    (`A3.png`) keeps it. Anything else has no label and is not scored."""
    stem = Path(source_file).stem
    if stem in LABEL_PREFIX:
        return f"{LABEL_PREFIX[stem]}{page_index + 1}"
    return stem if _LABELLED_STEM.match(stem) else None


def _label_key(label: str) -> tuple[str, int]:
    match = re.match(r"^([A-Za-z]+)(\d+)$", label)
    return (match.group(1), int(match.group(2))) if match else (label, 0)


def select_labels(gold_labels: set[str], split: Split, held_out: set[str]) -> list[str]:
    """The labels a run may read, in natural order. `dev` excludes every held-out label and
    refuses to run when no held-out list is known; `heldout` keeps only those labels."""
    if split != "all" and not held_out:
        raise EvalInputError(
            "no held-out list (eval/held_out.txt): cannot tell the dev split from the held-out one"
        )
    if split == "dev":
        chosen = gold_labels - held_out
    elif split == "heldout":
        chosen = gold_labels & held_out
    else:
        chosen = set(gold_labels)
    return sorted(chosen, key=_label_key)


# --------------------------------------------------------------------------- comparators


def _date_of(text: str) -> date | None:
    parsed = parse_il_date(text)
    if parsed is not None:
        return parsed
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def values_match(key: FieldKey, predicted: str | None, gold: str | None) -> bool:
    """PLAN §10.4: dates compare as dates; names after whitespace and final-letter
    normalization; numbers exactly (an ID after padding and surplus-zero removal); a blank gold
    value matches only a field left empty. The sender `לא ברשימה` is a blank sender."""
    if key is FieldKey.sender and gold == SENDER_NOT_LISTED:
        gold = None
    if predicted is None or gold is None:
        return predicted is None and gold is None
    if key is FieldKey.national_id:
        return (normalize_id(predicted) or predicted) == (normalize_id(gold) or gold)
    if key is FieldKey.receipt_date:
        p, g = _date_of(predicted), _date_of(gold)
        return p == g if p is not None and g is not None else predicted.strip() == gold.strip()
    if key in (FieldKey.first_name, FieldKey.last_name):
        return normalize_name(predicted) == normalize_name(gold)
    if key is FieldKey.commitment_number:
        return normalize_commitment(predicted) == normalize_commitment(gold)
    if key is FieldKey.central_number:
        return normalize_central(predicted) == normalize_central(gold)
    return predicted.strip() == gold.strip()


# --------------------------------------------------------------------------- metrics


@dataclass(frozen=True)
class Scored:
    """One field compared with its gold value. Holds no value, only the verdicts."""

    label: str
    field: FieldKey
    correct: bool
    flagged: bool  # level D, or the page failed (a visible failure, never silent)
    source: SourceKind
    reason: str | None
    escalated: bool
    cloud_calls: int


def score_outcome(
    outcome: PageOutcome, label: str, gold: dict[FieldKey, str | None]
) -> list[Scored]:
    """Every gold field of a page compared with the decision. A failed page, or a field the
    pipeline did not produce, counts as wrong and flagged: the technician sees the failure."""
    results = outcome.results
    scored: list[Scored] = []
    for key, expected in gold.items():
        result: FieldResult | None = results.get(key)
        if result is None:
            scored.append(
                Scored(label, key, False, True, SourceKind.unknown, outcome.error_code, False, 0)
            )
            continue
        scored.append(
            Scored(
                label=label,
                field=key,
                correct=values_match(key, result.value, expected),
                flagged=result.level is ConfidenceLevel.D,
                source=result.source,
                reason=result.reason,
                escalated=result.escalated,
                cloud_calls=sum(1 for s in result.trail if s.cloud),
            )
        )
    return scored


def _accuracy(rows: list[Scored]) -> float | None:
    return ratio(sum(1 for r in rows if r.correct), len(rows))


def field_breakdown(rows: list[Scored]) -> list[FieldBreakdown]:
    """Per-field accuracy, flag rate, silent error and the commonest reason among wrong fields."""
    out: list[FieldBreakdown] = []
    for key in FieldKey:
        mine = [r for r in rows if r.field is key]
        if not mine:
            continue
        reasons = [r.reason for r in mine if not r.correct and r.reason]
        top = max(set(reasons), key=reasons.count) if reasons else None
        out.append(
            FieldBreakdown(
                field=key,
                n=len(mine),
                accuracy=_accuracy(mine),
                flag_rate=ratio(sum(1 for r in mine if r.flagged), len(mine)),
                silent_error=ratio(
                    sum(1 for r in mine if not r.correct and not r.flagged), len(mine)
                ),
                top_failure_reason=top,
            )
        )
    return out


def percentile(values: list[int], q: float) -> int | None:
    """Nearest-rank percentile (0 < q <= 1) of `values`; None when there are none."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, round(q * len(ordered)) - 1))]


def peak_memory_mb() -> float | None:
    """Peak resident memory of this process in MB, or None where the platform cannot tell."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("page_faults", wintypes.DWORD),
                    ("peak_working_set", ctypes.c_size_t),
                    *[(f"other_{i}", ctypes.c_size_t) for i in range(8)],
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            windll = getattr(ctypes, "windll")  # noqa: B009 - absent on non-Windows type stubs
            handle = wintypes.HANDLE(-1)  # pseudo-handle of the current process
            if not windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return None
            return counters.peak_working_set / (1024 * 1024)
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes, macOS bytes.
        return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    except (OSError, ImportError, AttributeError):
        return None


@contextmanager
def deny_network() -> Iterator[list[int]]:
    """Make every internet-socket connect fail and count the attempts (the M3 proof that a
    `--local-only` run makes no network call). Unix-domain sockets are left alone."""
    attempts = [0]
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def blocked_connect(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            attempts[0] += 1
            raise OSError("network access is disabled during a local-only evaluation")
        real_connect(self, address)

    def blocked_connect_ex(self: socket.socket, address: Any) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            attempts[0] += 1
            return errno.ENETUNREACH
        return real_connect_ex(self, address)

    socket.socket.connect = blocked_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = blocked_connect_ex  # type: ignore[method-assign]
    try:
        yield attempts
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]


def summarize(
    scored: list[Scored],
    outcomes: list[tuple[str, PageOutcome]],
    *,
    split: Split,
    on: date,
    local_only: bool,
    ablation: str | None,
    unlabelled: int,
    network_attempts: int,
) -> EvalReport:
    """Fold the per-field verdicts into the report. `outcomes` pairs each label with its page."""
    wrong = [r for r in scored if not r.correct]
    flagged = [r for r in scored if r.flagged]
    flagged_wrong = [r for r in wrong if r.flagged]
    silent = [r for r in wrong if not r.flagged]
    printed = [r for r in scored if r.source in _PRINTED_SOURCES]
    handwritten = [r for r in scored if r.source is SourceKind.handwritten]
    latencies = [sum(o.durations_ms.values()) for _, o in outcomes if o.status == "done"]
    return EvalReport(
        split=split,
        date=on,
        local_only=local_only,
        ablation=ablation,
        pages=len(outcomes),
        failed_pages=[label for label, o in outcomes if o.status == "failed"],
        unlabelled_pages=unlabelled,
        fields=len(scored),
        correct=len(scored) - len(wrong),
        accuracy=_accuracy(scored),
        printed_accuracy=_accuracy(printed),
        printed_n=len(printed),
        handwritten_accuracy=_accuracy(handwritten),
        handwritten_n=len(handwritten),
        silent_error=ratio(len(silent), len(scored)),
        flag_recall=ratio(len(flagged_wrong), len(wrong)),
        flag_precision=ratio(len(flagged_wrong), len(flagged)),
        flag_rate=ratio(len(flagged), len(scored)),
        local_close_rate=ratio(
            sum(1 for r in scored if not r.flagged and not r.escalated), len(scored)
        ),
        escalation_rate=ratio(sum(1 for r in scored if r.escalated), len(scored)),
        cloud_calls=sum(r.cloud_calls for r in scored),
        measured_cost_usd=0.0,  # no provider exists yet (S9, T18); real counters replace this
        network_attempts=network_attempts,
        p50_latency_ms=percentile(latencies, 0.50),
        p95_latency_ms=percentile(latencies, 0.95),
        peak_memory_mb=peak_memory_mb(),
        per_field=field_breakdown(scored),
    )


# --------------------------------------------------------------------------- run

# Layer switches for --ablation, applied through `config/models.yaml: layers` overrides rather
# than code edits (PLAN §10.5). Layers whose stage does not exist yet (S9, learning curve) are
# not listed: a switch that changes nothing would put a fake row in the table.
ABLATIONS: Final[dict[str, dict[str, bool]]] = {
    "no_second_read": {"second_read": False},
    "no_digit_reader": {"digit_reader": False},
    "no_learning": {"learning": False},
    "no_logo": {"logo": False},
}


def prepare_services(
    services: Container | None,
    *,
    local_only: bool,
    ablation: str | None,
    models: Path | None,
) -> Container:
    """The container to evaluate with: local-only forced (vision removed), layers switched off
    for `ablation`. Raises EvalInputError for an unknown ablation name."""
    if ablation is not None and ablation not in ABLATIONS:
        raise EvalInputError(f"unknown ablation {ablation!r}; choose from {sorted(ABLATIONS)}")
    if services is None:
        settings = Settings(local_only=True) if local_only else Settings()  # type: ignore[call-arg]
        services = build_container(settings)
        if models is not None:
            services = replace(services, pipeline=load_pipeline_config(models))
    if local_only:
        settings = services.settings.model_copy(update={"local_only": True})
        services = replace(services, settings=settings, vision=None)
    if ablation is not None:
        layers = services.pipeline.layers.model_copy(update=ABLATIONS[ablation])
        services = replace(
            services, pipeline=services.pipeline.model_copy(update={"layers": layers})
        )
    return services


def evaluate(
    gold: Path,
    *,
    split: Split,
    ablation: str | None = None,
    local_only: bool = False,
    held_out: Path | None = DEFAULT_HELD_OUT,
    pdfs: tuple[Path, ...] = DEFAULT_PDFS,
    uploads: list[UploadedBlob] | None = None,
    services: Container | None = None,
    models: Path | None = None,
    signals_out: Path | None = None,
    today: date | None = None,
    pages: Collection[str] | None = None,
    known_hashes: frozenset[str] = frozenset(),
    cloud_consent: bool = False,
) -> EvalReport:
    """Run the pipeline on the pages of `split` and compare every field with the gold value.

    A dev run never processes a held-out page: the pages are chosen from the gold labels
    minus the held-out list before any OCR happens, and a final assertion backs that up.
    `uploads` replaces the sample PDFs (tests); `services` replaces the real container.
    `pages` narrows the run to those labels (a subset of the split, e.g. the bake-off's hard
    pages); `known_hashes` marks sample pages so a non-local run may reach the vision port
    (PLAN §8.5); `cloud_consent` allows it for any page.
    Raises EvalInputError when the gold, the held-out list or the pages are unusable."""
    gold_rows = read_gold(gold)
    held = read_held_out(held_out) | read_split_column(gold)
    labels = set(select_labels(set(gold_rows), split, held))
    if pages is not None:
        labels &= set(pages)
    if uploads is None:
        present = [p for p in pdfs if p.is_file()]
        if not present:
            raise EvalInputError("sample PDFs not found (reference/samples_part*.pdf)")
        uploads = [UploadedBlob(filename=p.name, data=p.read_bytes()) for p in present]
    svc = prepare_services(services, local_only=local_only, ablation=ablation, models=models)

    job_id = f"eval-{uuid.uuid4().hex[:12]}"
    outcomes: list[tuple[str, PageOutcome]] = []
    attempts = [0]
    try:
        result = intake(
            uploads,
            settings=svc.settings,
            documents=svc.documents,
            storage=svc.storage,
            job_id=job_id,
            known_hashes=known_hashes,
        )
        raws = apply_labels(result.pages, lambda r: label_of_page(r.source_file, r.page_index))
        chosen = [r for r in raws if r.label in labels]
        if split == "dev" and any(r.label in held for r in chosen):
            raise HeldOutLeak("a held-out page was selected in a dev run")
        guard = deny_network() if local_only else nullcontext(attempts)
        with guard as seen:
            for outcome in iter_outcomes(chosen, services=svc, cloud_consent=cloud_consent):
                outcomes.append((outcome.label or outcome.page_id, outcome))
            attempts = seen
    finally:
        svc.storage.purge_job(job_id)

    scored = [row for label, o in outcomes for row in score_outcome(o, label, gold_rows[label])]
    unlabelled = sum(1 for r in raws if r.label not in gold_rows and r.label not in held)
    if signals_out is not None and split == "dev":
        write_signals(
            signals_out,
            [
                to_record(label, ev, svc.pipeline.scoring)
                for label, o in outcomes
                for ev in o.evaluations.values()
            ],
        )
    return summarize(
        scored,
        outcomes,
        split=split,
        on=today or date.today(),
        local_only=local_only,
        ablation=ablation,
        unlabelled=unlabelled,
        network_attempts=attempts[0],
    )


# --------------------------------------------------------------------------- command line


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render(report: EvalReport, *, per_field: bool = False) -> str:
    """The metric set in the order that is the argument: silent error first. Counts and rates
    only; a field value never appears."""
    r = report
    lines = []
    if r.split == "heldout":
        lines += ["=" * 64, "FINAL REPORT - held-out split. Never tune on these numbers.", "=" * 64]
    lines += [
        f"split {r.split} | {r.date.isoformat()} | "
        f"{'local-only' if r.local_only else 'cloud allowed'} | ablation {r.ablation or 'none'}",
        f"pages {r.pages} | fields {r.fields} | failed pages {len(r.failed_pages)}"
        + (f" ({', '.join(r.failed_pages)})" if r.failed_pages else "")
        + f" | pages without gold {r.unlabelled_pages}",
        f"silent error {_fmt(r.silent_error)} | flag recall {_fmt(r.flag_recall)} | "
        f"flag rate {_fmt(r.flag_rate)} | flag precision {_fmt(r.flag_precision)}",
        f"accuracy {_fmt(r.accuracy)} | printed {_fmt(r.printed_accuracy)} (n={r.printed_n}) | "
        f"handwritten {_fmt(r.handwritten_accuracy)} (n={r.handwritten_n})",
        f"local close rate {_fmt(r.local_close_rate)} | escalation rate {_fmt(r.escalation_rate)} | "
        f"cloud calls {r.cloud_calls} | measured cost ${r.measured_cost_usd:.2f}",
        f"latency p50 {r.p50_latency_ms} ms | p95 {r.p95_latency_ms} ms | "
        f"peak memory {_fmt(r.peak_memory_mb, 0)} MB | network attempts {r.network_attempts}",
    ]
    if per_field:
        lines.append("field | n | accuracy | flag rate | silent error | top failure reason")
        lines += [
            f"{b.field.value} | {b.n} | {_fmt(b.accuracy)} | {_fmt(b.flag_rate)} | "
            f"{_fmt(b.silent_error)} | {b.top_failure_reason or '-'}"
            for b in r.per_field
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Exit 0 also when the sample data is absent (CI), 2 on unusable input."""
    p = argparse.ArgumentParser(description="Measure extraction accuracy against the gold set.")
    p.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    p.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    p.add_argument("--local-only", action="store_true", help="no cloud call; sockets are denied")
    p.add_argument("--ablation", help=f"one of {sorted(ABLATIONS)} or 'all'")
    p.add_argument("--held-out", type=Path, default=DEFAULT_HELD_OUT)
    p.add_argument("--pdf", type=Path, action="append", help="sample PDF (repeatable)")
    p.add_argument("--models", type=Path, default=None)
    p.add_argument("--per-field", action="store_true")
    p.add_argument("--emit-json", action="store_true", help="write eval/results/latest.json")
    p.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = p.parse_args(argv)
    pdfs = tuple(args.pdf) if args.pdf else DEFAULT_PDFS
    if not args.gold.is_file() or not any(f.is_file() for f in pdfs):
        sys.stdout.write("skip: eval/gold.csv or the sample PDFs are absent (git-ignored data)\n")
        return 0
    names: list[str | None] = [None, *ABLATIONS] if args.ablation == "all" else [args.ablation]
    reports: list[EvalReport] = []
    try:
        for name in names:
            report = evaluate(
                args.gold,
                split=args.split,
                ablation=name,
                local_only=args.local_only,
                held_out=args.held_out,
                pdfs=pdfs,
                models=args.models,
                signals_out=args.results / "signals.jsonl" if name is None else None,
            )
            reports.append(report)
            sys.stdout.write(render(report, per_field=args.per_field) + "\n")
    except SafeLineError as exc:
        sys.stderr.write(f"{exc.code}: {exc.detail}\n")
        return 2
    args.results.mkdir(parents=True, exist_ok=True)
    if args.emit_json:
        (args.results / "latest.json").write_text(reports[0].model_dump_json(indent=2), "utf-8")
    if args.ablation == "all":
        rows = "[" + ",".join(r.model_dump_json() for r in reports) + "]"
        (args.results / "ablation.json").write_text(rows, "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
