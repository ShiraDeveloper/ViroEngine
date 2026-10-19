"""tools/inventory.py — T5: Page inventory and barcode/print agreement check.

Run:
    python -m tools.inventory \\
        --pdfs reference/samples_part1.pdf reference/samples_part2.pdf \\
        --out eval/results/inventory.csv

Writes one CSV row per page and prints aggregate stats.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import csv
from dataclasses import dataclass
import math
from pathlib import Path
import re
import sys
from typing import Literal

import cv2
import numpy as np
import pypdfium2
import pytesseract
from pytesseract import Output
from rapidfuzz import fuzz
import structlog
import zxingcpp

from backend.core.models import BarcodeHit, Box, OcrResult, OcrWord, ReaderName

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Tool-internal types  (NOT domain pipeline models — no extra="forbid" needed)
# ---------------------------------------------------------------------------

AgreementVerdict = Literal["exact", "substring", "none"]


@dataclass(frozen=True)
class AgreementRow:
    """Agreement result for one barcode on one page."""

    payload: str
    symbology: str
    nearby_numeric: list[str]
    verdict: AgreementVerdict


@dataclass
class PageInventoryRow:
    """One row in inventory.csv (one page)."""

    label: str
    width: int
    height: int
    detected_rotation: int
    barcode_count: int
    payloads: str  # semicolon-joined
    caption_found: bool
    agreement_verdict: str  # e.g. "exact:2,none:1"
    institution_keyword: str
    page_role_guess: str
    yellow_mask_present: bool  # always False at runtime (eval/ only)


@dataclass
class InventoryStats:
    """Aggregate statistics returned by build_inventory."""

    total_pages: int = 0
    rotated_pages: int = 0
    total_barcodes: int = 0
    central_captions: int = 0
    exact_agreements: int = 0
    substring_agreements: int = 0
    no_agreements: int = 0

    @property
    def level_a_supported(self) -> bool:
        """True when at least one barcode payload matches its printed neighbour."""
        return (self.exact_agreements + self.substring_agreements) > 0

    def summary(self) -> str:
        return (
            f"{self.total_pages} pages | {self.rotated_pages} rotated | "
            f"{self.total_barcodes} barcodes | central captions: {self.central_captions} | "
            f"agreement: exact {self.exact_agreements}, "
            f"substring {self.substring_agreements}, "
            f"none {self.no_agreements}"
        )


# ---------------------------------------------------------------------------
# Pure helpers (testable without real PDFs or adapters)
# ---------------------------------------------------------------------------

_NUMERIC_RE = re.compile(r"^\d+$")

# Diagonal of the normalised 1x1 coordinate space
_UNIT_DIAGONAL = math.sqrt(2)


def _is_numeric_token(text: str) -> bool:
    """Return True for tokens that are pure digit strings (no hyphens, no letters)."""
    return bool(_NUMERIC_RE.fullmatch(text.strip()))


def _box_center(box: Box) -> tuple[float, float]:
    """Return (cy, cx) — centre of a normalised (y_min, x_min, y_max, x_max) box."""
    y_min, x_min, y_max, x_max = box
    return (y_min + y_max) / 2.0, (x_min + x_max) / 2.0


def _euclidean(ay: float, ax: float, by: float, bx: float) -> float:
    """Euclidean distance between two points in normalised (y, x) space."""
    return math.sqrt((ay - by) ** 2 + (ax - bx) ** 2)


def _assign_labels(page_counts: Sequence[int]) -> list[str]:
    """Assign A1..An, B1..Bm labels across PDFs.

    page_counts[0] = pages in first PDF  -> A1..A<n>
    page_counts[1] = pages in second PDF -> B1..B<m>
    Never raises; an empty sequence returns an empty list.
    """
    labels: list[str] = []
    for prefix_idx, count in enumerate(page_counts):
        prefix = chr(ord("A") + prefix_idx)
        for i in range(1, count + 1):
            labels.append(f"{prefix}{i}")
    return labels


def barcode_print_agreement(
    hits: list[BarcodeHit],
    ocr: OcrResult,
    *,
    radius: float = 0.06,
) -> list[AgreementRow]:
    """For every barcode, find numeric tokens within `radius` of the page diagonal
    and report exact / substring / none.  radius=0.06 is the sticker neighbourhood.

    Both hits and ocr must be in the same coordinate space (same page orientation).
    Returns an empty list when hits is empty. Never raises.
    """
    threshold = radius * _UNIT_DIAGONAL

    rows: list[AgreementRow] = []
    for hit in hits:
        hcy, hcx = _box_center(hit.box)

        nearby_numeric: list[str] = []
        for word in ocr.words:
            if not _is_numeric_token(word.text):
                continue
            wcy, wcx = _box_center(word.box)
            if _euclidean(hcy, hcx, wcy, wcx) < threshold:
                nearby_numeric.append(word.text)

        verdict: AgreementVerdict = "none"
        for tok in nearby_numeric:
            if tok == hit.payload:
                verdict = "exact"
                break
            if hit.payload in tok or tok in hit.payload:
                # substring match — keep looking in case an exact match follows
                verdict = "substring"

        rows.append(
            AgreementRow(
                payload=hit.payload,
                symbology=hit.symbology,
                nearby_numeric=nearby_numeric,
                verdict=verdict,
            )
        )

    return rows


# ---------------------------------------------------------------------------
# I/O helpers (call real vendor libs; not directly unit-tested)
# ---------------------------------------------------------------------------


def _render_pdf_pages(pdf_path: Path, *, dpi: int = 300) -> list[np.ndarray]:
    """Render all pages of a PDF at dpi, returning BGR uint8 numpy arrays.

    scale = dpi/72 because PDF points are 1/72 inch.
    Raises FileNotFoundError if the PDF does not exist.
    """
    pdf = pypdfium2.PdfDocument(str(pdf_path))
    pages: list[np.ndarray] = []
    scale = dpi / 72.0
    for page in pdf:
        pil_img = page.render(scale=scale).to_pil()
        arr = np.array(pil_img)
        # pypdfium2 renders RGBA or RGB — convert to BGR for cv2/zxingcpp
        if arr.ndim == 3 and arr.shape[2] == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        elif arr.ndim == 3:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        pages.append(arr)
        page.close()
    pdf.close()
    return pages


def _rotate_image(img: np.ndarray, deg: int) -> np.ndarray:
    """Rotate image clockwise by deg degrees (must be 0, 90, 180, or 270).

    Returns the original array for 0 degrees. Never raises for valid input.
    """
    if deg == 0:
        return img
    if deg == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(f"deg must be 0/90/180/270, got {deg}")


def _run_tesseract_on_array(img: np.ndarray, langs: str = "heb+eng") -> OcrResult:
    """Run pytesseract word-level OCR on a BGR numpy array.

    Returns OcrResult with normalised boxes (y_min, x_min, y_max, x_max) in 0..1.
    Never raises; returns empty OcrResult on Tesseract error.
    """
    try:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        data = pytesseract.image_to_data(rgb, lang=langs, output_type=Output.DICT)
        words: list[OcrWord] = []
        for i in range(len(data["text"])):
            text = (data["text"][i] or "").strip()
            conf_raw = data["conf"][i]
            if not text or conf_raw == -1:
                continue
            conf = float(conf_raw) / 100.0
            lft = int(data["left"][i])
            top = int(data["top"][i])
            wid = int(data["width"][i])
            hgt = int(data["height"][i])
            box: Box = (
                top / h,
                lft / w,
                (top + hgt) / h,
                (lft + wid) / w,
            )
            words.append(
                OcrWord(
                    text=text,
                    box=box,
                    confidence=min(max(conf, 0.0), 1.0),
                )
            )
        full_text = " ".join(w.text for w in words)
        return OcrResult(reader=ReaderName.tesseract, words=words, text=full_text)
    except Exception as exc:
        log.warning("tesseract_failed", error=str(exc))
        return OcrResult(reader=ReaderName.tesseract, words=[], text="", candidates=[])


def _build_barcode_hit(
    result: object,  # zxingcpp.Result — no stub available
    img_shape: tuple[int, ...],
    orientation_deg: int,
    ocr: OcrResult,
    *,
    nearby_radius: float = 0.06,
) -> BarcodeHit:
    """Convert a zxingcpp result into a BarcodeHit with normalised box and nearby_text.

    img_shape must be the shape of the SAME image that was passed to zxingcpp,
    so pixel coordinates are in the same frame as the OCR words.
    """
    h, w = img_shape[:2]
    pos = result.position  # type: ignore[attr-defined]
    xs = [pos.top_left.x, pos.top_right.x, pos.bottom_right.x, pos.bottom_left.x]
    ys = [pos.top_left.y, pos.top_right.y, pos.bottom_right.y, pos.bottom_left.y]
    box: Box = (
        min(ys) / h,
        min(xs) / w,
        max(ys) / h,
        max(xs) / w,
    )

    # Populate nearby_text: all OCR words within nearby_radius x diagonal
    threshold = nearby_radius * _UNIT_DIAGONAL
    hcy, hcx = _box_center(box)
    nearby: list[str] = [
        word.text for word in ocr.words if _euclidean(hcy, hcx, *_box_center(word.box)) < threshold
    ]

    return BarcodeHit(
        payload=result.text,  # type: ignore[attr-defined]
        symbology=str(result.format),  # type: ignore[attr-defined]
        box=box,
        orientation_deg=orientation_deg,
        nearby_text=nearby,
    )


def _process_page(
    img: np.ndarray,
) -> tuple[list[BarcodeHit], OcrResult, int]:
    """Decode barcodes + run OCR, trying orientations 0/90/180/270 in order.

    Returns (hits, ocr, working_orientation_deg).
    OCR is run on the same rotated image that successfully decoded barcodes,
    ensuring box coordinates are in the same space for proximity checks.
    If no barcodes found at any orientation, returns ([], ocr_at_0, 0).
    """
    for deg in (0, 90, 180, 270):
        rotated = _rotate_image(img, deg)
        raw_hits = zxingcpp.read_barcodes(rotated)
        if raw_hits:
            ocr = _run_tesseract_on_array(rotated)
            hits = [_build_barcode_hit(rh, rotated.shape, deg, ocr) for rh in raw_hits]
            return hits, ocr, deg

    # No barcodes found — still run OCR on the original for role/institution guesses
    ocr = _run_tesseract_on_array(img)
    return [], ocr, 0


# ---------------------------------------------------------------------------
# Caption and page-role heuristics
# ---------------------------------------------------------------------------

_MERKAZI_WORD = "מרכזי"

# Institution keyword hints for the CSV — not used by the engine
_INSTITUTION_KEYWORDS: list[tuple[str, str]] = [
    ("כללית", "Clalit"),
    ("מכבי", "Maccabi"),
    ("מאוחדת", "Meuhedet"),
    ("לאומית", "Leumit"),
    ("הדסה", "Hadassah"),
    ("שיבא", "Sheba"),
    ("איכילוב", "Ichilov"),
    ("סורוקה", "Soroka"),
    ('רמב"ם', "Rambam"),
    ("בני ציון", "BneiBrith"),
]

_SAP_KEYWORDS = ["SAP", "אישור", "ממתין", "הפניה"]
_ATTACH_KEYWORDS = ["נספח", "attachment"]


def _detect_merkazi_caption(ocr: OcrResult, *, fuzzy_thresh: int = 85) -> bool:
    """Return True if 'מרכזי' appears in OCR output at fuzzy ratio >= fuzzy_thresh.

    Uses word-level and full-text partial matching because the caption is
    printed sideways and exact matching under-counts.
    Never raises.
    """
    for word in ocr.words:
        if fuzz.ratio(_MERKAZI_WORD, word.text) >= fuzzy_thresh:
            return True
    if fuzz.partial_ratio(_MERKAZI_WORD, ocr.text) >= fuzzy_thresh:
        return True
    return False


def _guess_institution(ocr: OcrResult) -> str:
    """Keyword scan for institution name. Returns empty string if none found."""
    text = ocr.text
    for keyword, name in _INSTITUTION_KEYWORDS:
        if keyword in text:
            return name
    return ""


def _guess_page_role(ocr: OcrResult, barcode_count: int) -> str:
    """Heuristic page-role: 'form' | 'sap_page' | 'attachment' | 'unknown'."""
    text = ocr.text
    if any(kw in text for kw in _SAP_KEYWORDS):
        return "sap_page"
    if any(kw in text for kw in _ATTACH_KEYWORDS):
        return "attachment"
    if barcode_count > 0:
        return "form"
    return "unknown"


# ---------------------------------------------------------------------------
# CSV schema
# ---------------------------------------------------------------------------

CSV_FIELDNAMES = [
    "label",
    "width",
    "height",
    "detected_rotation",
    "barcode_count",
    "payloads",
    "caption_found",
    "agreement_verdict",
    "institution_keyword",
    "page_role_guess",
    "yellow_mask_present",
]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_inventory(
    pdfs: Sequence[Path],
    out_csv: Path,
    *,
    dpi: int = 300,
) -> InventoryStats:
    """Render all pages, run barcode + OCR, write inventory.csv, return aggregate stats.

    A single-page failure is logged and written as an error row — never aborts the batch.
    """
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    # Render all PDFs and assign labels
    page_counts: list[int] = []
    all_images: list[np.ndarray] = []
    for pdf_path in pdfs:
        pages = _render_pdf_pages(pdf_path, dpi=dpi)
        page_counts.append(len(pages))
        all_images.extend(pages)

    labels = _assign_labels(page_counts)
    stats = InventoryStats(total_pages=len(labels))
    csv_rows: list[PageInventoryRow] = []

    for label, img in zip(labels, all_images, strict=False):
        try:
            h, w = img.shape[:2]
            hits, ocr, working_rot = _process_page(img)
            agreement_rows = barcode_print_agreement(hits, ocr)

            if working_rot != 0:
                stats.rotated_pages += 1
            stats.total_barcodes += len(hits)

            caption_found = _detect_merkazi_caption(ocr)
            if caption_found:
                stats.central_captions += 1

            verdict_counts: dict[str, int] = {}
            for ar in agreement_rows:
                verdict_counts[ar.verdict] = verdict_counts.get(ar.verdict, 0) + 1
                if ar.verdict == "exact":
                    stats.exact_agreements += 1
                elif ar.verdict == "substring":
                    stats.substring_agreements += 1
                else:
                    stats.no_agreements += 1

            verdict_str = ",".join(f"{k}:{v}" for k, v in sorted(verdict_counts.items()))

            csv_rows.append(
                PageInventoryRow(
                    label=label,
                    width=w,
                    height=h,
                    detected_rotation=working_rot,
                    barcode_count=len(hits),
                    payloads=";".join(hit.payload for hit in hits),
                    caption_found=caption_found,
                    agreement_verdict=verdict_str,
                    institution_keyword=_guess_institution(ocr),
                    page_role_guess=_guess_page_role(ocr, len(hits)),
                    yellow_mask_present=False,  # eval/ only — never at runtime
                )
            )
            log.info(
                "inventory_page_done",
                label=label,
                barcodes=len(hits),
                rotation=working_rot,
                caption=caption_found,
            )

        except Exception as exc:
            log.warning("inventory_page_failed", label=label, error=str(exc))
            csv_rows.append(
                PageInventoryRow(
                    label=label,
                    width=0,
                    height=0,
                    detected_rotation=0,
                    barcode_count=0,
                    payloads="",
                    caption_found=False,
                    agreement_verdict="error",
                    institution_keyword="",
                    page_role_guess="unknown",
                    yellow_mask_present=False,
                )
            )

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for row in csv_rows:
            writer.writerow(
                {
                    "label": row.label,
                    "width": row.width,
                    "height": row.height,
                    "detected_rotation": row.detected_rotation,
                    "barcode_count": row.barcode_count,
                    "payloads": row.payloads,
                    "caption_found": str(row.caption_found).lower(),
                    "agreement_verdict": row.agreement_verdict,
                    "institution_keyword": row.institution_keyword,
                    "page_role_guess": row.page_role_guess,
                    "yellow_mask_present": str(row.yellow_mask_present).lower(),
                }
            )

    log.info("inventory_complete", out_csv=str(out_csv), summary=stats.summary())
    return stats


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Build a page inventory CSV and answer the barcode/print agreement question (T5)."
        )
    )
    parser.add_argument(
        "--pdfs",
        nargs="+",
        required=True,
        type=Path,
        help="PDF files to inventory, in label order (A, B, ...).",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output CSV path (e.g. eval/results/inventory.csv).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="Render DPI (default 300).",
    )
    args = parser.parse_args(argv)

    for pdf in args.pdfs:
        if not pdf.exists():
            print(f"ERROR: PDF not found: {pdf}", file=sys.stderr)
            return 1

    stats = build_inventory(args.pdfs, args.out, dpi=args.dpi)
    print(stats.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
