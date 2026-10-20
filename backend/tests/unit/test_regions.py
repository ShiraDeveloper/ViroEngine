"""Tests for S4 (T11): the rule-based region detector and the stage that merges learned hints."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import cv2
import numpy as np
import pytest

from backend.adapters.fakes import FakeLearningStore, FakeRegionDetector, FakeStorage
from backend.adapters.regions import finders
from backend.adapters.regions.rule_based import RuleBasedDetector
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.geometry import contains
from backend.core.models import (
    BarcodeHit,
    Box,
    LayoutHint,
    NormalizedPage,
    OcrResult,
    OcrWord,
    ReaderName,
    Region,
    RegionKind,
    SenderSpec,
)
from backend.core.pipeline.s4_regions import LEARNED_DETECTOR, detect_regions
from backend.settings import Settings
from backend.tests.fixtures.synthetic.pages import PAGE_HEIGHT, PAGE_WIDTH, Pixels

MakeNormalized = Callable[[Pixels], NormalizedPage]
SIZE = (PAGE_WIDTH, PAGE_HEIGHT)
BLUE_INK = (200, 60, 20)  # BGR


def _word(text: str, box: Box, conf: float = 0.9) -> OcrWord:
    return OcrWord(text=text, box=box, confidence=conf)


def _ocr(*words: OcrWord) -> OcrResult:
    return OcrResult(reader=ReaderName.tesseract, words=list(words), text="")


def _hit(payload: str, box: Box) -> BarcodeHit:
    return BarcodeHit(
        payload=payload, symbology="Code128", box=box, orientation_deg=0, nearby_text=[]
    )


def _canvas() -> Pixels:
    return np.full((PAGE_HEIGHT, PAGE_WIDTH, 3), 255, dtype=np.uint8)


def _px(box: Box) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) in pixels of a normalized box, for drawing."""
    return (
        round(box[1] * PAGE_WIDTH),
        round(box[0] * PAGE_HEIGHT),
        round(box[3] * PAGE_WIDTH),
        round(box[2] * PAGE_HEIGHT),
    )


def _stickers(settings: Settings, barcodes: Sequence[BarcodeHit], ocr: OcrResult) -> list[Region]:
    return finders.find_central_stickers(
        barcodes,
        ocr,
        radius=settings.proximity_radius,
        fuzz_min=settings.central_caption_fuzz,
        upright_fuzz_min=settings.upright_caption_fuzz,
        size=SIZE,
    )


def _detector(settings: Settings, storage: TmpdirStorage) -> RuleBasedDetector:
    return RuleBasedDetector(settings=settings, storage=storage)


def _of_kind(regions: Sequence[Region], kind: RegionKind) -> list[Region]:
    return [r for r in regions if r.kind is kind]


# ---------------------------------------------------------------- central stickers


def test_central_sticker_via_barcode(settings: Settings) -> None:
    bars: Box = (0.40, 0.30, 0.42, 0.50)
    caption = _word("מרכזי", (0.395, 0.51, 0.435, 0.525))  # tall: printed sideways
    digits = _word("937309", (0.425, 0.31, 0.44, 0.40))
    regions = _stickers(settings, [_hit("937309", bars)], _ocr(caption, digits))
    assert len(regions) == 1
    region = regions[0]
    assert region.kind is RegionKind.central_sticker and region.label_text == "מרכזי"
    assert region.score == pytest.approx(1.0)
    for part in (bars, caption.box, digits.box):
        assert contains(region.box, part)


def test_central_sticker_handwritten_no_barcode(settings: Settings) -> None:
    caption = _word("מרכזי", (0.170, 0.08, 0.190, 0.22))  # upright, wide
    number = _word("939158", (0.200, 0.10, 0.220, 0.20))
    regions = _stickers(settings, [], _ocr(caption, number))
    assert len(regions) == 1
    assert contains(regions[0].box, caption.box) and contains(regions[0].box, number.box)


def test_central_sticker_without_barcode_needs_a_number_nearby(settings: Settings) -> None:
    caption = _word("מרכזי", (0.170, 0.08, 0.190, 0.22))
    assert _stickers(settings, [], _ocr(caption)) == []


def test_barcode_less_branch_does_not_run_on_a_page_with_barcodes(settings: Settings) -> None:
    caption = _word("מרכזי", (0.170, 0.08, 0.190, 0.22))
    number = _word("939158", (0.200, 0.10, 0.220, 0.20))
    elsewhere = _hit("123456", (0.70, 0.60, 0.72, 0.80))  # far from the caption, which has none
    assert _stickers(settings, [elsewhere], _ocr(caption, number)) == []


def test_three_central_stickers_detected(settings: Settings) -> None:
    tops = (0.620, 0.686, 0.747)  # stacked closer than the proximity radius, like A9
    bars = [(t, 0.47, t + 0.020, 0.55) for t in tops]
    words: list[OcrWord] = []
    for t in tops:
        words.append(_word("מרכזי", (t - 0.010, 0.56, t + 0.035, 0.575)))
        words.append(_word(f"9373{int(t * 100)}", (t + 0.022, 0.48, t + 0.037, 0.54)))
    hits = [_hit(f"hit{i}", b) for i, b in enumerate(bars)]
    regions = _stickers(settings, hits, _ocr(*words))
    assert len(regions) == 3
    for index, region in enumerate(sorted(regions, key=lambda r: r.box[0])):
        assert contains(region.box, bars[index])
        assert not any(contains(region.box, other) for j, other in enumerate(bars) if j != index)


@pytest.mark.parametrize("payload", ["12345", "1234567890123"])
def test_no_digit_count_filter(payload: str, settings: Settings) -> None:
    bars: Box = (0.40, 0.30, 0.42, 0.50)
    caption = _word("מרכזי", (0.395, 0.51, 0.435, 0.525))
    assert len(_stickers(settings, [_hit(payload, bars)], _ocr(caption))) == 1


def test_central_caption_wrong_letter_still_matches_when_sideways(settings: Settings) -> None:
    bars: Box = (0.40, 0.30, 0.42, 0.50)
    misread = _word("זרכזי", (0.395, 0.51, 0.435, 0.525))  # one letter wrong: similarity 80
    assert len(_stickers(settings, [_hit("937309", bars)], _ocr(misread))) == 1


def test_upright_word_near_barcode_is_not_a_caption(settings: Settings) -> None:
    # "מרכז רפואי" printed under a patient barcode is 89 similar to the caption.
    bars: Box = (0.40, 0.30, 0.42, 0.50)
    letterhead = _word("מרכז", (0.43, 0.30, 0.445, 0.40))
    assert _stickers(settings, [_hit("11786577", bars)], _ocr(letterhead)) == []


# ---------------------------------------------------------------- receipt stamp


def _stamp_words() -> list[OcrWord]:
    return [
        _word("התקכל", (0.760, 0.60, 0.785, 0.72), conf=0.4),  # faint: one letter wrong
        _word("01-06-2026", (0.790, 0.62, 0.805, 0.70)),
        _word("המעבדה", (0.820, 0.72, 0.832, 0.79)),
        _word("המרכזית", (0.820, 0.62, 0.832, 0.70)),
        _word("לנגיפים", (0.845, 0.64, 0.857, 0.72)),
    ]


def test_receipt_stamp_found_when_faint(settings: Settings) -> None:
    decoy = [  # the lab's name also appears in an address line, far from the stamp
        _word("המעבדה", (0.050, 0.72, 0.062, 0.79)),
        _word("המרכזית", (0.050, 0.62, 0.062, 0.70)),
    ]
    stamp = finders.find_receipt_stamp(
        _ocr(*decoy, *_stamp_words()),
        fuzz_min=settings.receipt_stamp_fuzz,
        tolerance=settings.line_height_tolerance,
        radius=settings.proximity_radius,
        size=SIZE,
    )
    assert stamp is not None and stamp.kind is RegionKind.receipt_stamp
    assert contains(stamp.box, (0.760, 0.60, 0.857, 0.79))  # spans the date printed between
    assert stamp.box[0] > 0.5  # and not the address line


def test_receipt_stamp_absent_returns_none(settings: Settings) -> None:
    stamp = finders.find_receipt_stamp(
        _ocr(_word("שלום", (0.1, 0.1, 0.12, 0.2))),
        fuzz_min=settings.receipt_stamp_fuzz,
        tolerance=settings.line_height_tolerance,
        radius=settings.proximity_radius,
        size=SIZE,
    )
    assert stamp is None


# ---------------------------------------------------------------- patient box, labels, clinical


def _patient_labels() -> list[OcrWord]:
    return [
        _word("המבוטח", (0.60, 0.86, 0.62, 0.93)),
        _word("פרטי", (0.60, 0.78, 0.62, 0.84)),
    ]


def test_patient_box_found_by_label(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage
) -> None:
    page = make_normalized(_canvas())
    regions = _detector(settings, storage).detect(page, _ocr(*_patient_labels()), [])
    (patient,) = _of_kind(regions, RegionKind.patient_box)
    assert contains(patient.box, (0.60, 0.78, 0.62, 0.93))
    assert patient.box[1] == pytest.approx(0.78 - settings.label_value_expand, abs=0.01)


def test_patient_box_snaps_to_the_ruled_frame_around_it(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage
) -> None:
    frame: Box = (0.57, 0.10, 0.72, 0.95)
    canvas = _canvas()
    x0, y0, x1, y1 = _px(frame)
    cv2.rectangle(canvas, (x0, y0), (x1, y1), (0, 0, 0), 3)
    page = make_normalized(canvas)
    regions = _detector(settings, storage).detect(page, _ocr(*_patient_labels()), [])
    (patient,) = _of_kind(regions, RegionKind.patient_box)
    assert patient.box == pytest.approx(frame, abs=0.01)


def test_commitment_label_region_reaches_toward_the_value(settings: Settings) -> None:
    label = [_word("התחייבות", (0.20, 0.70, 0.22, 0.80)), _word("מספר", (0.20, 0.82, 0.22, 0.88))]
    value = _word("X132767", (0.20, 0.55, 0.22, 0.66))
    regions = finders.find_commitment_labels(
        _ocr(*label, value),
        ["מספר התחייבות"],
        fuzz_min=settings.label_fuzz,
        tolerance=settings.line_height_tolerance,
        value_expand=settings.label_value_expand,
    )
    assert len(regions) == 1 and regions[0].kind is RegionKind.commitment_label
    assert contains(regions[0].box, value.box)
    assert regions[0].box[3] == pytest.approx(0.88)  # nothing past the label on the other side


def test_no_commitment_labels_yields_no_commitment_region(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage
) -> None:
    page = make_normalized(_canvas())
    ocr = _ocr(_word("מספר", (0.2, 0.8, 0.22, 0.88)), _word("התחייבות", (0.2, 0.7, 0.22, 0.8)))
    regions = _detector(settings, storage).detect(page, ocr, [])
    assert _of_kind(regions, RegionKind.commitment_label) == []


def test_clinical_region_marked(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage
) -> None:
    label: Box = (0.20, 0.82, 0.22, 0.92)
    page = make_normalized(_canvas())
    regions = _detector(settings, storage).detect(page, _ocr(_word("אבחנות:", label)), [])
    (clinical,) = _of_kind(regions, RegionKind.clinical)
    assert contains(clinical.box, label)
    assert clinical.box[1] < label[1] and clinical.box[2] > label[2]  # toward and below the value


def test_letterhead_is_the_top_of_the_page(settings: Settings) -> None:
    head = finders.letterhead(settings.letterhead_fraction)
    assert head.kind is RegionKind.letterhead
    assert head.box == (0.0, 0.0, settings.letterhead_fraction, 1.0)


# ---------------------------------------------------------------- pixels: tables, ink, failures


def test_table_found_from_its_line_crossings(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage
) -> None:
    canvas = _canvas()
    for x in (150, 350, 550, 750):
        cv2.line(canvas, (x, 800), (x, 1100), (0, 0, 0), 3)
    for y in (800, 900, 1000, 1100):
        cv2.line(canvas, (150, y), (750, y), (0, 0, 0), 3)
    page = make_normalized(canvas)
    (table,) = _of_kind(_detector(settings, storage).detect(page, _ocr(), []), RegionKind.table)
    assert table.box == pytest.approx((800 / PAGE_HEIGHT, 0.15, 1100 / PAGE_HEIGHT, 0.75), abs=0.01)


def test_single_frame_is_not_a_table(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage
) -> None:
    canvas = _canvas()
    cv2.rectangle(canvas, (150, 800), (750, 1100), (0, 0, 0), 3)
    page = make_normalized(canvas)
    assert _of_kind(_detector(settings, storage).detect(page, _ocr(), []), RegionKind.table) == []


def _handwritten_page_words() -> tuple[OcrWord, OcrWord]:
    return (
        _word("מרכזי", (0.170, 0.08, 0.190, 0.22)),
        _word("939158", (0.200, 0.10, 0.220, 0.20)),
    )


def test_blue_mask_absent_does_not_break(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage
) -> None:
    caption, number = _handwritten_page_words()
    page = make_normalized(_canvas())  # no ink of any colour
    (sticker,) = _of_kind(
        _detector(settings, storage).detect(page, _ocr(caption, number), []),
        RegionKind.central_sticker,
    )
    assert sticker.box == pytest.approx((0.170, 0.08, 0.220, 0.22))  # exactly the words


def test_blue_ink_widens_a_handwritten_region_to_cover_it(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage
) -> None:
    caption, number = _handwritten_page_words()
    canvas = _canvas()
    cv2.polylines(canvas, [np.array([[100, 335], [180, 350], [260, 335]])], False, BLUE_INK, 5)
    page = make_normalized(canvas)
    (sticker,) = _of_kind(
        _detector(settings, storage).detect(page, _ocr(caption, number), []),
        RegionKind.central_sticker,
    )
    assert sticker.box[2] >= 350 / PAGE_HEIGHT  # now reaches the ink below the words


def test_detector_returns_text_regions_when_the_image_is_unreadable(
    make_normalized: MakeNormalized, settings: Settings
) -> None:
    page = make_normalized(_canvas())
    blind = RuleBasedDetector(settings=settings, storage=FakeStorage())  # holds no pixels
    regions = blind.detect(page, _ocr(*_patient_labels(), *_stamp_words()), [])
    kinds = {r.kind for r in regions}
    assert {RegionKind.patient_box, RegionKind.receipt_stamp, RegionKind.letterhead} <= kinds


# ---------------------------------------------------------------- the stage


class _RecordingDetector:
    name = "recording"

    def __init__(self) -> None:
        self.labels: Sequence[str] | None = None

    def detect(
        self,
        page: NormalizedPage,
        ocr: OcrResult,
        barcodes: Sequence[BarcodeHit],
        *,
        commitment_labels: Sequence[str] = (),
    ) -> list[Region]:
        self.labels = commitment_labels
        return []


class _HintStore(FakeLearningStore):
    def layout_hints(self, sender: str, field: str) -> list[LayoutHint]:
        return [
            LayoutHint(
                region_kind=RegionKind.patient_box, box=(0.5, 0.1, 0.7, 0.9), confidence=0.95
            )
        ]


_SENDER = SenderSpec(
    canonical="מאוחדת",
    family="hmo",
    aliases=[],
    keywords=[],
    commitment_labels=["מספר התחייבות"],
)


def test_stage_passes_the_senders_labels_and_adds_one_capped_learned_region(
    make_normalized: MakeNormalized, settings: Settings
) -> None:
    page = make_normalized(_canvas())
    detector = _RecordingDetector()
    regions = detect_regions(
        page,
        _ocr(),
        [],
        settings=settings,
        sender=_SENDER,
        learning=_HintStore(),
        detector=detector,
    )
    assert detector.labels == ["מספר התחייבות"]
    (learned,) = regions  # the same hint stored under every field becomes one region
    assert learned.detector == LEARNED_DETECTOR
    assert learned.score == pytest.approx(settings.learned_region_score)  # capped below 0.95


def test_stage_without_a_sender_adds_no_learned_regions(
    make_normalized: MakeNormalized, settings: Settings
) -> None:
    page = make_normalized(_canvas())
    regions = detect_regions(
        page,
        _ocr(),
        [],
        settings=settings,
        sender=None,
        learning=_HintStore(),
        detector=FakeRegionDetector(),
    )
    assert regions == []
