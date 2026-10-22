"""Tests for S6 central-number candidates (T13). OCR words, barcodes and regions are written
by hand at the positions measured on the sample pages, so no sample data is needed."""

from __future__ import annotations

import pytest

from backend.core.models import (
    BarcodeHit,
    Box,
    OcrResult,
    OcrWord,
    ReaderName,
    Region,
    RegionKind,
    SourceKind,
)
from backend.core.pipeline.s6_candidates_lab import candidates_central
from backend.settings import Settings

SIZE = (2480, 3508)  # A4 at 300 DPI, (width, height) px


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _word(text: str, box: Box, conf: float = 0.9) -> OcrWord:
    return OcrWord(text=text, box=box, confidence=conf)


def _hit(payload: str, box: Box, nearby: list[str]) -> BarcodeHit:
    return BarcodeHit(
        payload=payload, symbology="Code128", box=box, orientation_deg=0, nearby_text=nearby
    )


def _sticker(box: Box) -> Region:
    return Region(
        kind=RegionKind.central_sticker,
        box=box,
        detector="rule-based",
        score=1.0,
        label_text="מרכזי",
    )


def _ocr(words: list[OcrWord]) -> OcrResult:
    return OcrResult(reader=ReaderName.tesseract, words=words, text="")


# A sideways caption: tall and narrow, just right of the bars (A1).
_CAPTION_A1 = _word("מרכזי", (0.036, 0.330, 0.068, 0.342))


def test_barcode_sticker(settings: Settings) -> None:
    """A1: the sticker's barcode is the central number; the form's other barcodes are not."""
    hits = [
        _hit("939376", (0.032, 0.232, 0.056, 0.312), ["מרכזי"]),
        _hit("0046025783", (0.114, 0.825, 0.148, 0.934), ["0046025783"]),  # form number
        _hit("730578626 ", (0.170, 0.056, 0.188, 0.195), []),  # commitment number
    ]
    regions = [_sticker((0.032, 0.232, 0.068, 0.342))]
    found = candidates_central(hits, _ocr([_CAPTION_A1]), regions, settings=settings, size=SIZE)
    assert [(c.value, c.source, c.reader) for c in found] == [
        ("939376", SourceKind.barcode, ReaderName.zxing)
    ]
    assert found[0].region is RegionKind.central_sticker
    assert found[0].label_distance is not None and found[0].label_distance < 0.06


def test_three_stickers_all_kept(settings: Settings) -> None:
    """A9: three stickers, three central numbers - intentionally multi-valued."""
    hits = [
        _hit("937309", (0.625, 0.466, 0.644, 0.548), ["מרכזי"]),
        _hit("937310", (0.686, 0.472, 0.710, 0.552), ["מרכזי"]),
        _hit("937311", (0.747, 0.478, 0.773, 0.558), ["מרכזי"]),
        _hit("2344936", (0.501, 0.271, 0.532, 0.376), ["2344936"]),  # a test-tube label
    ]
    regions = [
        _sticker((0.587, 0.465, 0.663, 0.577)),
        _sticker((0.686, 0.472, 0.722, 0.582)),
        _sticker((0.747, 0.478, 0.782, 0.589)),
    ]
    found = candidates_central(hits, _ocr([]), regions, settings=settings, size=SIZE)
    assert {c.value for c in found} == {"937309", "937310", "937311"}


def test_handwritten_no_barcode(settings: Settings) -> None:
    """A2 (SAP screen): no barcode and no sticker region; the number written under the
    handwritten caption is found by its proximity to the caption."""
    words = [
        _word("מספר", (0.170, 0.150, 0.185, 0.185)),
        _word("מרכזי", (0.170, 0.085, 0.185, 0.140)),
        _word("939158", (0.188, 0.140, 0.205, 0.220), conf=0.55),
        _word("62038", (0.431, 0.630, 0.453, 0.701)),  # a postcode elsewhere on the screen
    ]
    found = candidates_central([], _ocr(words), [], settings=settings, size=SIZE)
    assert [(c.value, c.source) for c in found] == [("939158", SourceKind.handwritten)]
    assert found[0].region is None


def test_decoy_barcode_rejected(settings: Settings) -> None:
    """A barcode inside the sticker region whose nearest label is another field's label
    (here the commitment number) is a decoy."""
    hits = [
        _hit("945992", (0.084, 0.102, 0.109, 0.184), ["945992", "מרכזי"]),
        _hit("730578626", (0.110, 0.102, 0.120, 0.184), ["התחייבות", "מרכזי"]),
    ]
    regions = [_sticker((0.080, 0.100, 0.125, 0.220))]
    found = candidates_central(hits, _ocr([]), regions, settings=settings, size=SIZE)
    assert [c.value for c in found] == ["945992"]


@pytest.mark.parametrize("payload", ["4521", "94599", "X132767", "1234567890123"])
def test_no_digit_count_filter(payload: str, settings: Settings) -> None:
    """Any length and any shape is kept: identification is by the sticker, not the value."""
    hits = [_hit(payload, (0.084, 0.102, 0.109, 0.184), ["מרכזי"])]
    regions = [_sticker((0.080, 0.100, 0.125, 0.220))]
    found = candidates_central(hits, _ocr([]), regions, settings=settings, size=SIZE)
    assert [c.value for c in found] == [payload]


def test_printed_digits_and_barcode_both_kept(settings: Settings) -> None:
    """B6: the sticker's printed number agrees with its barcode; one candidate per reader,
    so S8 can see two independent readings agree."""
    hits = [_hit("945992", (0.084, 0.102, 0.109, 0.184), ["945992", "מרכזי"])]
    words = [
        _word("945992", (0.112, 0.109, 0.123, 0.175)),
        _word("מרכזי", (0.088, 0.199, 0.120, 0.217)),
    ]
    regions = [_sticker((0.084, 0.102, 0.123, 0.217))]
    found = candidates_central(hits, _ocr(words), regions, settings=settings, size=SIZE)
    assert [(c.value, c.source) for c in found] == [
        ("945992", SourceKind.barcode),
        ("945992", SourceKind.printed),
    ]


def test_digits_read_off_the_bars_ignored(settings: Settings) -> None:
    """Digits OCR finds on the bars themselves are noise, not a printed number."""
    hits = [_hit("937309", (0.625, 0.466, 0.644, 0.548), ["מרכזי"])]
    words = [_word("7", (0.630, 0.500, 0.640, 0.505))]
    regions = [_sticker((0.587, 0.465, 0.663, 0.577))]
    found = candidates_central(hits, _ocr(words), regions, settings=settings, size=SIZE)
    assert [c.value for c in found] == ["937309"]


def test_date_beside_sticker_is_not_a_central_number(settings: Settings) -> None:
    """A date written in the sticker region belongs to the receipt date (date source 2)."""
    hits = [_hit("945992", (0.084, 0.102, 0.109, 0.184), ["מרכזי"])]
    words = [_word("13.8.26", (0.112, 0.105, 0.125, 0.180))]
    regions = [_sticker((0.080, 0.100, 0.130, 0.220))]
    found = candidates_central(hits, _ocr(words), regions, settings=settings, size=SIZE)
    assert [c.value for c in found] == ["945992"]


def test_truncated_caption_word_is_not_a_caption(settings: Settings) -> None:
    """'מרכז' (as in 'מרכז רפואי') is a different word: its phone number is no candidate."""
    words = [
        _word("מרכז", (0.414, 0.494, 0.421, 0.524)),
        _word("6740476", (0.414, 0.420, 0.421, 0.480)),
    ]
    assert candidates_central([], _ocr(words), [], settings=settings, size=SIZE) == []


def test_caption_proximity_unused_when_a_sticker_exists(settings: Settings) -> None:
    """Source 3 (caption proximity) runs only when no sticker region formed."""
    hits = [_hit("946908", (0.049, 0.092, 0.068, 0.175), ["מרכזי"])]
    words = [
        _word("מרכזי", (0.500, 0.500, 0.512, 0.560)),
        _word("123456", (0.515, 0.500, 0.527, 0.560)),
    ]
    regions = [_sticker((0.045, 0.092, 0.117, 0.221))]
    found = candidates_central(hits, _ocr(words), regions, settings=settings, size=SIZE)
    assert [c.value for c in found] == ["946908"]


def test_nothing_found_is_an_empty_list(settings: Settings) -> None:
    assert candidates_central([], _ocr([]), [], settings=settings, size=SIZE) == []


def test_caption_digits_are_not_the_printed_number(settings: Settings) -> None:
    """A9/B10: on a printed sticker only the line under the bars is the number; a '1' or '7'
    read off the caption or the edge of the sticker is no candidate."""
    hits = [_hit("945598", (0.500, 0.300, 0.520, 0.380), ["מרכזי"])]
    words = [
        _word("945598", (0.522, 0.305, 0.532, 0.375)),  # under the bars
        _word("1", (0.505, 0.395, 0.515, 0.400)),  # beside them, by the caption
    ]
    regions = [_sticker((0.495, 0.295, 0.540, 0.410))]
    found = candidates_central(hits, _ocr(words), regions, settings=settings, size=SIZE)
    assert [(c.value, c.source) for c in found] == [
        ("945598", SourceKind.barcode),
        ("945598", SourceKind.printed),
    ]
