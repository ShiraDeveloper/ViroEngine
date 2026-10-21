"""tools/demo.py - run pipeline stages on chosen sample pages (PLAN §12 enabler).

    python -m tools.demo measure A3 A1 --pdf reference/samples_part1.pdf
    python -m tools.demo regions A9 A2 --pdf reference/samples_part1.pdf

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
from backend.adapters.fakes import FakeLearningStore
from backend.adapters.imaging.cv import CvImaging
from backend.adapters.imaging.pdf import DocumentReader
from backend.adapters.ocr.tesseract import TesseractAdapter
from backend.adapters.regions.rule_based import RuleBasedDetector
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.geometry import contains
from backend.core.models import NormalizedPage, PageRaw, RegionKind
from backend.core.pipeline.s0_intake import UploadedBlob, intake
from backend.core.pipeline.s1_measure import measure
from backend.core.pipeline.s2_correct import correct
from backend.core.pipeline.s3_read_page import read_page
from backend.core.pipeline.s4_regions import detect_regions
from backend.settings import Settings

_LABEL = re.compile(r"^[A-Za-z]+(\d+)$")
_JOB_ID = "demo"


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
    args = parser.parse_args(argv)

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
