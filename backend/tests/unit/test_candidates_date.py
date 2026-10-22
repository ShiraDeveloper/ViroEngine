"""Tests for S6 receipt-date candidates (T13): the stamp, the handwritten date beside a
sticker, and the decoys. Words are placed by hand at the positions measured on the samples."""

from __future__ import annotations

import pytest

from backend.adapters.fakes import FixedClock
from backend.core.models import Box, OcrResult, OcrWord, ReaderName, Region, RegionKind, SourceKind
from backend.core.pipeline.s6_candidates_lab import (
    NOTE_DATE_FUTURE,
    NOTE_DATE_IMPOSSIBLE,
    NOTE_STAMP_HANDWRITTEN_DIFFER,
    candidates_receipt_date,
)
from backend.settings import Settings

SIZE = (2496, 3563)  # A1, (width, height) px

# A1's stamp and sticker, as S4 found them on the real page.
STAMP_A1 = Region(
    kind=RegionKind.receipt_stamp, box=(0.64, 0.12, 0.76, 0.41), detector="rule-based", score=0.9
)
STICKER = Region(
    kind=RegionKind.central_sticker,
    box=(0.084, 0.102, 0.123, 0.217),
    detector="rule-based",
    score=1.0,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _word(text: str, box: Box, conf: float = 0.9) -> OcrWord:
    return OcrWord(text=text, box=box, confidence=conf)


def _dates(
    words: list[OcrWord], regions: list[Region], settings: Settings
) -> list[tuple[str, SourceKind]]:
    found = candidates_receipt_date(
        OcrResult(reader=ReaderName.tesseract, words=words, text=""),
        regions,
        settings=settings,
        clock=FixedClock(),
        size=SIZE,
    )
    return [(c.value, c.source) for c in found]


def _stamp_words(day: list[str], month_year: tuple[str, str] = ("-06-", "2026")) -> list[OcrWord]:
    """The stamp: 'התקבל' above a gapped date, as the stamp re-read returns it (A1)."""
    words = [_word("התקבל", (0.650, 0.190, 0.670, 0.330))]
    x = 0.200
    for part in day:
        words.append(_word(part, (0.684, x, 0.699, x + 0.012)))
        x += 0.015
    words.append(_word(month_year[0], (0.684, x + 0.005, 0.699, x + 0.040)))
    words.append(_word(month_year[1], (0.684, x + 0.045, 0.699, x + 0.090)))
    return words


def test_stamp_with_spaced_digits(settings: Settings) -> None:
    """A1: '1 8 -06- 2026' read as separate tokens -> 2026-06-18 from the stamp."""
    assert _dates(_stamp_words(["1", "8"]), [STAMP_A1], settings) == [
        ("18/06/2026", SourceKind.stamp)
    ]


def test_gapped_day_keeps_both_digits(settings: Settings) -> None:
    """A5: '2 1 -06- 2026' must not lose its first digit and become the 1st."""
    assert _dates(_stamp_words(["2", "1"]), [STAMP_A1], settings) == [
        ("21/06/2026", SourceKind.stamp)
    ]


def test_handwritten_beside_sticker(settings: Settings) -> None:
    """B6: '13.8.26' written under the sticker -> 2026-08-13, tagged handwritten."""
    words = [_word("13.8.26", (0.128, 0.105, 0.140, 0.180))]
    found = _dates(words, [STICKER], settings)
    assert found == [("13/08/2026", SourceKind.handwritten)]


def test_patient_admission_date_rejected(settings: Settings) -> None:
    """B6: '09.08.26' governed by 'ת.קבלה:' is the admission date, even near a sticker."""
    words = [
        _word("ת.קבלה:", (0.130, 0.240, 0.142, 0.300)),
        _word("09.08.26", (0.130, 0.160, 0.142, 0.230)),
    ]
    assert _dates(words, [STICKER], settings) == []


def test_form_header_date_rejected(settings: Settings) -> None:
    """A1: the printed '15.06.2026' of 'תאריך הפקה' must not beat the stamp's 18-06-2026,
    even placed beside the sticker."""
    words = [
        *_stamp_words(["18"]),
        _word("15.06.2026", (0.130, 0.110, 0.140, 0.200)),
        _word(":", (0.130, 0.205, 0.140, 0.210)),
        _word("הפקה", (0.130, 0.215, 0.140, 0.260)),
        _word("תאריך", (0.130, 0.265, 0.140, 0.320)),
    ]
    assert _dates(words, [STAMP_A1, STICKER], settings) == [("18/06/2026", SourceKind.stamp)]


def test_sample_collection_date_rejected(settings: Settings) -> None:
    words = [
        _word("06/08/2026", (0.130, 0.110, 0.140, 0.190)),
        _word("הדגימה:", (0.130, 0.200, 0.140, 0.250)),
        _word("לקיחת", (0.130, 0.255, 0.140, 0.300)),
        _word("תאריך", (0.130, 0.305, 0.140, 0.350)),
    ]
    assert _dates(words, [STICKER], settings) == []


@pytest.mark.parametrize("label", ["הדפסה", "הודפס", "הופק", "תוקף"])
def test_print_date_rejected(label: str, settings: Settings) -> None:
    """Print, production and validity dates (B2 'הופק', A20 'הודפס') are decoys."""
    words = [
        _word("12/08/2026", (0.130, 0.110, 0.140, 0.190)),
        _word(label, (0.130, 0.200, 0.140, 0.250)),
    ]
    assert _dates(words, [STICKER], settings) == []


def test_column_header_date_rejected(settings: Settings) -> None:
    """A date under a 'תאריך' column header is the table's date (the SAP visit row)."""
    words = [
        _word("תאריך", (0.110, 0.120, 0.120, 0.170)),
        _word("16.06.2026", (0.128, 0.110, 0.140, 0.190)),
    ]
    assert _dates(words, [STICKER], settings) == []


def test_conflict_emits_both_with_reason(settings: Settings) -> None:
    words = [*_stamp_words(["18"]), _word("17.6.26", (0.128, 0.105, 0.140, 0.180))]
    found = candidates_receipt_date(
        OcrResult(reader=ReaderName.tesseract, words=words, text=""),
        [STAMP_A1, STICKER],
        settings=settings,
        clock=FixedClock(),
        size=SIZE,
    )
    assert {(c.value, c.source) for c in found} == {
        ("18/06/2026", SourceKind.stamp),
        ("17/06/2026", SourceKind.handwritten),
    }
    assert all(NOTE_STAMP_HANDWRITTEN_DIFFER in c.notes for c in found)


def test_agreeing_stamp_and_handwritten_carry_no_conflict(settings: Settings) -> None:
    words = [*_stamp_words(["18"]), _word("18.6.26", (0.128, 0.105, 0.140, 0.180))]
    found = candidates_receipt_date(
        OcrResult(reader=ReaderName.tesseract, words=words, text=""),
        [STAMP_A1, STICKER],
        settings=settings,
        clock=FixedClock(),
        size=SIZE,
    )
    assert {c.value for c in found} == {"18/06/2026"} and len(found) == 2
    assert all(NOTE_STAMP_HANDWRITTEN_DIFFER not in c.notes for c in found)


def test_future_date_flagged_not_accepted(settings: Settings) -> None:
    """The clock reads 2026-10-18: a stamp dated 2027 is emitted with ``date_future`` so S8
    flags it - never silently dropped, never accepted."""
    found = candidates_receipt_date(
        OcrResult(
            reader=ReaderName.tesseract, words=_stamp_words(["18"], ("-06-", "2027")), text=""
        ),
        [STAMP_A1],
        settings=settings,
        clock=FixedClock(),
        size=SIZE,
    )
    assert [(c.value, c.notes) for c in found] == [("18/06/2027", [NOTE_DATE_FUTURE])]


def test_impossible_date_flagged(settings: Settings) -> None:
    found = candidates_receipt_date(
        OcrResult(
            reader=ReaderName.tesseract, words=_stamp_words(["31"], ("-02-", "2026")), text=""
        ),
        [STAMP_A1],
        settings=settings,
        clock=FixedClock(),
        size=SIZE,
    )
    assert [(c.value, c.notes) for c in found] == [("31-02-2026", [NOTE_DATE_IMPOSSIBLE])]


def test_date_far_from_any_sticker_ignored(settings: Settings) -> None:
    """With no stamp, a date far from every sticker is the form's own date."""
    words = [_word("13.8.26", (0.700, 0.700, 0.712, 0.780))]
    assert _dates(words, [STICKER], settings) == []


def test_unlabelled_printed_date_a_little_away_from_sticker_ignored(settings: Settings) -> None:
    """A15: the letter's own date, with no label, stands 0.08 of the diagonal from the sticker.
    It is not a note beside the sticker; S8 closed it wrongly when it was admitted."""
    sticker = Region(
        kind=RegionKind.central_sticker,
        box=(0.24, 0.02, 0.33, 0.22),
        detector="rule-based",
        score=1.0,
    )
    words = [_word("06/08/2026", (0.139, 0.132, 0.147, 0.216))]
    assert _dates(words, [sticker], settings) == []
