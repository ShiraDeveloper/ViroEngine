"""tools/demo.py - run pipeline stages on chosen sample pages (PLAN §12 enabler).

    python -m tools.demo measure A3 A1 --pdf reference/samples_part1.pdf
    python -m tools.demo regions A9 A2 --pdf reference/samples_part1.pdf
    python -m tools.demo candidates A1 A9 A2 B6 --fields central_number,receipt_date

A label such as ``A3`` selects page 3 (1-based) of the PDF given; the letter is informational.

``measure`` prints, per page, the S1 measurement and the S2 corrections applied. ``regions``
runs S0-S4 and prints the regions found: central stickers (with the payloads of the barcodes
inside them), the receipt stamp, the patient box and the rest. Both need Tesseract with the
Hebrew data (TESSERACT_CMD, TESSDATA_PREFIX).
"""

from __future__ import annotations

import argparse
from collections import Counter
import logging
from pathlib import Path
import re
import sys
import tempfile

import structlog

from backend.adapters.barcode.zxing import ZxingAdapter
from backend.adapters.clock.system import SystemClock
from backend.adapters.fakes import FakeLearningStore
from backend.adapters.imaging.cv import CvImaging
from backend.adapters.imaging.pdf import DocumentReader
from backend.adapters.ocr.tesseract import TesseractAdapter
from backend.adapters.regions.rule_based import RuleBasedDetector
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.geometry import contains, page_size
from backend.core.models import Candidate, FieldKey, NormalizedPage, PageRaw, RegionKind
from backend.core.pipeline.s0_intake import UploadedBlob, intake
from backend.core.pipeline.s1_measure import measure
from backend.core.pipeline.s2_correct import correct
from backend.core.pipeline.s3_read_page import read_page
from backend.core.pipeline.s4_regions import detect_regions
from backend.core.pipeline.s6_candidates_lab import candidates_central, candidates_receipt_date
from backend.settings import Settings

_LABEL = re.compile(r"^[A-Za-z]+(\d+)$")
_JOB_ID = "demo"
# Sample files by label letter (reference/README.md).
_SAMPLE_PDFS = {
    "A": Path("reference/samples_part1.pdf"),
    "B": Path("reference/samples_part2.pdf"),
}


def _parse_labels(labels: list[str]) -> dict[str, int] | None:
    """Label -> zero-based page index, or None (after reporting) when a label is malformed."""
    indices: dict[str, int] = {}
    for label in labels:
        match = _LABEL.match(label)
        if match is None or int(match.group(1)) < 1:
            print(f"ERROR: {label!r} is not a page label like A3", file=sys.stderr)
            return None
        indices[label] = int(match.group(1)) - 1
    return indices


def _rendered_pages(
    labels: list[str], pdf: Path, settings: Settings, storage: TmpdirStorage
) -> dict[str, PageRaw | None]:
    """S0 on `pdf`: the raw page for each label, or None when it did not render."""
    indices = _parse_labels(labels)
    if indices is None:
        return {}
    result = intake(
        [UploadedBlob(filename=pdf.name, data=pdf.read_bytes())],
        settings=settings,
        documents=DocumentReader(settings=settings),
        storage=storage,
        job_id=_JOB_ID,
    )
    by_index = {page.page_index: page for page in result.pages}
    return {label: by_index.get(index) for label, index in indices.items()}


def run_measure(labels: list[str], pdf: Path) -> int:
    """Measure and correct the labelled pages of `pdf`, printing one line each."""
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    with tempfile.TemporaryDirectory() as workdir:
        settings = Settings(_env_file=None, data_dir=workdir)  # type: ignore[call-arg]
        storage = TmpdirStorage(settings=settings)
        imaging = CvImaging(settings=settings)
        ocr = TesseractAdapter(settings=settings, storage=storage)
        barcode = ZxingAdapter(settings=settings, storage=storage)
        pages = _rendered_pages(labels, pdf, settings, storage)
        if not pages:
            return 2
        for label, page in pages.items():
            if page is None:
                print(f"{label}: not rendered (page missing or failed)")
                continue
            m = measure(
                page, settings=settings, ocr=ocr, barcode=barcode, imaging=imaging, storage=storage
            )
            normalized = correct(page, m, settings=settings, imaging=imaging, storage=storage)
            print(
                f"{label}: rotation={m.rotation_deg} skew={m.skew_deg:.1f} "
                f"blur={m.blur_score:.0f} needs={m.needs} applied={normalized.corrections_applied}"
            )
        storage.purge_job(_JOB_ID)
    return 0


def run_regions(labels: list[str], pdf: Path) -> int:
    """Run S0-S4 on the labelled pages of `pdf` and print the regions found on each."""
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    with tempfile.TemporaryDirectory() as workdir:
        settings = Settings(_env_file=None, data_dir=workdir)  # type: ignore[call-arg]
        storage = TmpdirStorage(settings=settings)
        imaging = CvImaging(settings=settings)
        ocr = TesseractAdapter(settings=settings, storage=storage)
        barcode = ZxingAdapter(settings=settings, storage=storage)
        detector = RuleBasedDetector(settings=settings, storage=storage)
        pages = _rendered_pages(labels, pdf, settings, storage)
        if not pages:
            return 2
        for label, page in pages.items():
            if page is None:
                print(f"{label}: not rendered (page missing or failed)")
                continue
            m = measure(
                page, settings=settings, ocr=ocr, barcode=barcode, imaging=imaging, storage=storage
            )
            normalized = correct(page, m, settings=settings, imaging=imaging, storage=storage)
            print(
                f"{label}: {_describe(normalized, settings, ocr, barcode, imaging, storage, detector)}"
            )
        storage.purge_job(_JOB_ID)
    return 0


def _describe(
    page: NormalizedPage,
    settings: Settings,
    ocr: TesseractAdapter,
    barcode: ZxingAdapter,
    imaging: CvImaging,
    storage: TmpdirStorage,
    detector: RuleBasedDetector,
) -> str:
    """One line: the region kinds found, with sticker payloads or the handwritten marker."""
    words, hits = read_page(
        page, settings=settings, ocr=ocr, barcode=barcode, imaging=imaging, storage=storage
    )
    regions = detect_regions(
        page,
        words,
        hits,
        settings=settings,
        sender=None,
        learning=FakeLearningStore(),
        detector=detector,
    )
    parts: list[str] = []
    counts = Counter(r.kind for r in regions if r.kind is not RegionKind.letterhead)
    for kind, count in sorted(counts.items(), key=lambda item: item[0].value):
        text = f"{kind.value} x{count}" if count > 1 else kind.value
        if kind is RegionKind.central_sticker:
            payloads = [
                h.payload for r in regions if r.kind is kind for h in hits if contains(r.box, h.box)
            ]
            text += f" ({', '.join(payloads) if payloads else 'handwritten, no barcode'})"
        parts.append(text)
    return ", ".join(parts) if parts else "no regions"


def run_candidates(labels: list[str], pdf: Path | None, fields: set[FieldKey]) -> int:
    """Run S0-S4 and the T13 branches of S6 on the labelled pages; one line each.

    Without `pdf`, a label's letter picks the sample file: A -> part 1, B -> part 2.
    """
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    by_pdf: dict[Path, list[str]] = {}
    for label in labels:
        source = pdf or _SAMPLE_PDFS.get(label[:1].upper())
        if source is None or not source.exists():
            print(f"ERROR: no PDF for {label} (pass --pdf)", file=sys.stderr)
            return 1
        by_pdf.setdefault(source, []).append(label)
    with tempfile.TemporaryDirectory() as workdir:
        settings = Settings(_env_file=None, data_dir=workdir)  # type: ignore[call-arg]
        storage = TmpdirStorage(settings=settings)
        imaging = CvImaging(settings=settings)
        ocr = TesseractAdapter(settings=settings, storage=storage)
        barcode = ZxingAdapter(settings=settings, storage=storage)
        detector = RuleBasedDetector(settings=settings, storage=storage)
        clock = SystemClock()
        for source, group in by_pdf.items():
            pages = _rendered_pages(group, source, settings, storage)
            if not pages:
                return 2
            for label, raw in pages.items():
                if raw is None:
                    print(f"{label}: not rendered (page missing or failed)")
                    continue
                m = measure(
                    raw,
                    settings=settings,
                    ocr=ocr,
                    barcode=barcode,
                    imaging=imaging,
                    storage=storage,
                )
                page = correct(raw, m, settings=settings, imaging=imaging, storage=storage)
                words, hits = read_page(
                    page,
                    settings=settings,
                    ocr=ocr,
                    barcode=barcode,
                    imaging=imaging,
                    storage=storage,
                )
                regions = detect_regions(
                    page,
                    words,
                    hits,
                    settings=settings,
                    sender=None,
                    learning=FakeLearningStore(),
                    detector=detector,
                )
                size = page_size(page)
                parts: list[str] = []
                if FieldKey.central_number in fields:
                    central = candidates_central(hits, words, regions, settings=settings, size=size)
                    parts.append(
                        "central=[" + ", ".join(_describe_candidate(c) for c in central) + "]"
                    )
                if FieldKey.receipt_date in fields:
                    dates = candidates_receipt_date(
                        words, regions, settings=settings, clock=clock, size=size
                    )
                    parts.append("date=[" + ", ".join(_describe_candidate(c) for c in dates) + "]")
                print(f"{label}: {' '.join(parts)}")
            storage.purge_job(_JOB_ID)
    return 0


def _describe_candidate(candidate: Candidate) -> str:
    """'939376 barcode', '18/06/2026 stamp', plus any reason notes."""
    value = candidate.value
    notes = f" ({', '.join(candidate.notes)})" if candidate.notes else ""
    return f"{value} {candidate.source.value}{notes}"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Run pipeline stages on sample pages.")
    stages = parser.add_subparsers(dest="stage", required=True)
    for name, helptext in (
        ("measure", "S0-S2: intake, measure, correct"),
        ("regions", "S0-S4: intake, measure, correct, read, detect regions"),
    ):
        cmd = stages.add_parser(name, help=helptext)
        cmd.add_argument("labels", nargs="+", help="page labels, e.g. A3 A1")
        cmd.add_argument("--pdf", required=True, type=Path, help="source PDF")
    cand = stages.add_parser("candidates", help="S0-S6: central number and receipt date")
    cand.add_argument("labels", nargs="+", help="page labels, e.g. A1 A9 B6")
    cand.add_argument("--pdf", type=Path, default=None, help="source PDF (default: by letter)")
    cand.add_argument(
        "--fields",
        default="central_number,receipt_date",
        help="comma-separated: central_number, receipt_date",
    )
    args = parser.parse_args(argv)

    if args.stage == "candidates":
        try:
            fields = {FieldKey(f.strip()) for f in args.fields.split(",") if f.strip()}
        except ValueError:
            print(f"ERROR: unknown field in {args.fields!r}", file=sys.stderr)
            return 1
        return run_candidates(args.labels, args.pdf, fields)
    if not args.pdf.exists():
        print(f"ERROR: PDF not found: {args.pdf}", file=sys.stderr)
        return 1
    return (
        run_measure(args.labels, args.pdf)
        if args.stage == "measure"
        else run_regions(args.labels, args.pdf)
    )


if __name__ == "__main__":
    sys.exit(main())
