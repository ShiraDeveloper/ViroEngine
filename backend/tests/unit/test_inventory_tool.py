"""Tests for tools/inventory.py — T5.

test_agreement_detects_exact_match          — payload matches nearby numeric token exactly
test_agreement_detects_substring_match      — payload is a substring of a nearby token
test_agreement_ignores_distant_number       — numeric token too far away is not collected
test_labels_assigned_in_file_order          — A1..An then B1..Bm
test_labels_match_expected_sample_counts    — 26+22 pages give 48 labels
test_handles_page_with_no_barcode           — empty hits -> empty agreement list, no crash
"""

from __future__ import annotations

from backend.core.models import BarcodeHit, Box, OcrResult, OcrWord, ReaderName
from tools.inventory import (
    _assign_labels,
    barcode_print_agreement,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hit(payload: str, box: Box) -> BarcodeHit:
    return BarcodeHit(
        payload=payload,
        symbology="Code128",
        box=box,
        orientation_deg=0,
        nearby_text=[],
    )


def _ocr_with_word(text: str, box: Box) -> OcrResult:
    return OcrResult(
        reader=ReaderName.tesseract,
        words=[OcrWord(text=text, box=box, confidence=0.9)],
        text=text,
        candidates=[],
    )


def _empty_ocr() -> OcrResult:
    return OcrResult(reader=ReaderName.tesseract, words=[], text="", candidates=[])


# ---------------------------------------------------------------------------
# Required tests
# ---------------------------------------------------------------------------


def test_agreement_detects_exact_match() -> None:
    """A numeric OCR word at the barcode centre returns verdict 'exact'."""
    hit = _hit("123456", (0.40, 0.40, 0.60, 0.60))
    # Word is 0.02 diagonal units from the barcode centre — well within radius=0.06*sqrt(2)~0.085
    ocr = _ocr_with_word("123456", (0.41, 0.41, 0.49, 0.55))
    rows = barcode_print_agreement([hit], ocr, radius=0.06)
    assert len(rows) == 1
    assert rows[0].verdict == "exact"
    assert "123456" in rows[0].nearby_numeric


def test_agreement_detects_substring_match() -> None:
    """A numeric token that contains the payload returns verdict 'substring'."""
    # Payload "456" is a substring of nearby word "123456"
    hit = _hit("456", (0.40, 0.40, 0.60, 0.60))
    ocr = _ocr_with_word("123456", (0.41, 0.41, 0.49, 0.55))
    rows = barcode_print_agreement([hit], ocr, radius=0.06)
    assert len(rows) == 1
    assert rows[0].verdict == "substring"


def test_agreement_ignores_distant_number() -> None:
    """A numeric token far from the barcode must not be collected (verdict='none')."""
    hit = _hit("123456", (0.40, 0.40, 0.60, 0.60))
    # Word is in the far corner — distance >> 0.085 threshold
    ocr = _ocr_with_word("123456", (0.90, 0.90, 0.99, 0.99))
    rows = barcode_print_agreement([hit], ocr, radius=0.06)
    assert len(rows) == 1
    assert rows[0].verdict == "none"
    assert rows[0].nearby_numeric == []


def test_labels_assigned_in_file_order() -> None:
    """Labels are A1..An for the first PDF then B1..Bm for the second."""
    labels = _assign_labels([3, 2])
    assert labels == ["A1", "A2", "A3", "B1", "B2"]


def test_labels_match_expected_sample_counts() -> None:
    """26+22 pages produce A1..A26 then B1..B22 (48 total)."""
    labels = _assign_labels([26, 22])
    assert labels[0] == "A1"
    assert labels[25] == "A26"
    assert labels[26] == "B1"
    assert labels[47] == "B22"
    assert len(labels) == 48


def test_handles_page_with_no_barcode() -> None:
    """Empty hits list returns an empty agreement list without raising."""
    rows = barcode_print_agreement([], _empty_ocr())
    assert rows == []
