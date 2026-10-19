"""tools/demo.py — run one pipeline stage on chosen sample pages (PLAN §12 enabler).

    python -m tools.demo measure A3 A1 --pdf reference/samples_part1.pdf

Prints one line per page: the S1 measurement and the S2 corrections applied. A label such as
``A3`` selects page 3 (1-based) of the PDF given; the letter is informational.

Rotation voting needs the real Tesseract and barcode adapters (T11). Until they exist the
demo falls back to fakes that abstain, says so, and orientation then reads as 0.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import re
import sys
import tempfile

import structlog

from backend.adapters.fakes import FakeBarcode, FakeOcr
from backend.adapters.imaging.cv import CvImaging
from backend.adapters.imaging.pdf import DocumentReader
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.pipeline.s0_intake import UploadedBlob, intake
from backend.core.pipeline.s1_measure import measure
from backend.core.pipeline.s2_correct import correct
from backend.core.ports.barcode import BarcodePort
from backend.core.ports.ocr import OcrPort
from backend.settings import Settings

_LABEL = re.compile(r"^[A-Za-z]+(\d+)$")
_JOB_ID = "demo"


def _load_vote_ports(settings: Settings) -> tuple[OcrPort, BarcodePort, bool]:
    """The real OCR and barcode adapters when built (T11), otherwise abstaining fakes."""
    try:
        from backend.adapters.barcode.zxing import ZxingAdapter
        from backend.adapters.ocr.tesseract import TesseractAdapter
    except ImportError:
        return FakeOcr(), FakeBarcode(), False
    return TesseractAdapter(settings=settings), ZxingAdapter(), True


def run_measure(labels: list[str], pdf: Path) -> int:
    """Measure and correct the labelled pages of `pdf`, printing one line each."""
    indices: dict[str, int] = {}
    for label in labels:
        match = _LABEL.match(label)
        if match is None or int(match.group(1)) < 1:
            print(f"ERROR: {label!r} is not a page label like A3", file=sys.stderr)
            return 2
        indices[label] = int(match.group(1)) - 1

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    with tempfile.TemporaryDirectory() as workdir:
        settings = Settings(_env_file=None, data_dir=workdir)  # type: ignore[call-arg]
        storage = TmpdirStorage(settings=settings)
        imaging = CvImaging(settings=settings)
        ocr, barcode, real_votes = _load_vote_ports(settings)
        if not real_votes:
            print("note: T11 adapters not built; rotation votes abstain, orientation reads 0")

        result = intake(
            [UploadedBlob(filename=pdf.name, data=pdf.read_bytes())],
            settings=settings,
            documents=DocumentReader(settings=settings),
            storage=storage,
            job_id=_JOB_ID,
        )
        by_index = {page.page_index: page for page in result.pages}
        for label, index in indices.items():
            page = by_index.get(index)
            if page is None:
                print(f"{label}: not rendered (page {index + 1} missing or failed)")
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


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Run one pipeline stage on sample pages.")
    stages = parser.add_subparsers(dest="stage", required=True)
    measure_cmd = stages.add_parser("measure", help="S0-S2: intake, measure, correct")
    measure_cmd.add_argument("labels", nargs="+", help="page labels, e.g. A3 A1")
    measure_cmd.add_argument("--pdf", required=True, type=Path, help="source PDF")
    args = parser.parse_args(argv)

    if not args.pdf.exists():
        print(f"ERROR: PDF not found: {args.pdf}", file=sys.stderr)
        return 1
    return run_measure(args.labels, args.pdf)


if __name__ == "__main__":
    sys.exit(main())
