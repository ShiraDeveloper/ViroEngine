"""Tests for S3 (T11): page reading, reading order, the barcode sweep and nearby text."""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np
import pytest

from backend.adapters.barcode.zxing import ZxingAdapter
from backend.adapters.fakes import FakeBarcode, FakeOcr
from backend.adapters.imaging.cv import CvImaging
from backend.adapters.ocr.tesseract import parse_tsv
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.geometry import contains, grow, unrotate_box
from backend.core.models import BarcodeHit, Box, NormalizedPage, OcrWord
from backend.core.pipeline.s3_read_page import SIDEWAYS_PSM, caption_strips, nearby_text, read_page
from backend.core.reading_order import group_lines
from backend.settings import Settings
from backend.tests.fixtures.synthetic import pages
from backend.tests.fixtures.synthetic.barcodes import barcode_page
from backend.tests.fixtures.synthetic.doubles import ScriptedBarcode, ScriptedOcr
from backend.tests.fixtures.synthetic.pages import PAGE_HEIGHT, PAGE_WIDTH, Pixels

MakeNormalized = Callable[[Pixels], NormalizedPage]
SIZE = (PAGE_WIDTH, PAGE_HEIGHT)

_TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)


def _word(text: str, box: Box, conf: float = 0.9) -> OcrWord:
    return OcrWord(text=text, box=box, confidence=conf)


def _hit(payload: str, box: Box) -> BarcodeHit:
    return BarcodeHit(
        payload=payload, symbology="Code128", box=box, orientation_deg=0, nearby_text=[]
    )


def _read(
    page: NormalizedPage,
    settings: Settings,
    storage: TmpdirStorage,
    imaging: CvImaging,
    *,
    ocr: object,
    barcode: object,
) -> tuple[object, list[BarcodeHit]]:
    return read_page(
        page,
        settings=settings,
        ocr=ocr,  # type: ignore[arg-type]
        barcode=barcode,  # type: ignore[arg-type]
        imaging=imaging,
        storage=storage,
    )


# ---------------------------------------------------------------- OCR words and reading order


def test_word_boxes_normalized() -> None:
    tsv = "\n".join(
        [
            _TSV_HEADER,
            "4\t1\t1\t1\t1\t0\t90\t190\t320\t60\t-1\t",  # a line, not a word
            "5\t1\t1\t1\t1\t1\t100\t200\t300\t50\t96.0\t\u200fאבגד\u200e",
            "5\t1\t1\t1\t1\t2\t500\t200\t80\t50\t-1\t",  # no text
        ]
    )
    words = parse_tsv(tsv, 1000, 2000)
    assert len(words) == 1
    assert words[0].text == "אבגד"  # direction marks removed
    assert words[0].box == pytest.approx((0.10, 0.10, 0.125, 0.40))
    assert words[0].confidence == pytest.approx(0.96)


def test_reading_order_hebrew_rtl() -> None:
    hebrew = [
        _word("ראשון", (0.100, 0.70, 0.112, 0.80)),
        _word("שני", (0.101, 0.50, 0.113, 0.60)),
        _word("שלישי", (0.099, 0.20, 0.111, 0.30)),
    ]
    latin = [
        _word("alpha", (0.200, 0.10, 0.212, 0.20)),
        _word("beta", (0.201, 0.40, 0.213, 0.50)),
    ]
    lines = group_lines([*latin, *hebrew], tolerance=1.2)
    assert [[w.text for w in line] for line in lines] == [
        ["ראשון", "שני", "שלישי"],  # Hebrew: right to left
        ["alpha", "beta"],  # Latin: left to right, and below the Hebrew line
    ]


def test_low_confidence_word_kept_in_words_not_text(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    page = make_normalized(pages.clean_page())
    ocr = ScriptedOcr(
        words=[
            _word("clear", (0.10, 0.10, 0.12, 0.20), conf=0.90),
            _word("smudge", (0.10, 0.30, 0.12, 0.40), conf=settings.ocr_min_word_confidence / 2),
        ]
    )
    result, _ = _read(page, settings, storage, imaging, ocr=ocr, barcode=FakeBarcode())
    assert [w.text for w in result.words] == ["clear", "smudge"]  # type: ignore[attr-defined]
    assert result.text == "clear"  # type: ignore[attr-defined]


def test_unreadable_page_yields_empty_result(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    page = make_normalized(pages.clean_page())
    result, hits = _read(page, settings, storage, imaging, ocr=FakeOcr(), barcode=FakeBarcode())
    assert result.words == [] and result.text == "" and hits == []  # type: ignore[attr-defined]


# ---------------------------------------------------------------- barcode sweep


@pytest.mark.parametrize("payload", ["1234", "123456", "123456789", "1234567890123"])
def test_barcode_any_length_accepted(
    payload: str,
    make_normalized: MakeNormalized,
    settings: Settings,
    storage: TmpdirStorage,
    imaging: CvImaging,
) -> None:
    image, box = barcode_page(payload)
    page = make_normalized(image)
    _, hits = _read(
        page,
        settings,
        storage,
        imaging,
        ocr=ScriptedOcr(),
        barcode=ZxingAdapter(settings=settings, storage=storage),
    )
    assert [h.payload for h in hits] == [payload]
    assert contains(grow(box, top=0.01, left=0.01, bottom=0.01, right=0.01), hits[0].box)


def test_barcode_found_in_rotated_sticker(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage
) -> None:
    image, box = barcode_page("937309", vertical=True, top=200, left=600)
    page = make_normalized(image)
    hits = ZxingAdapter(settings=settings, storage=storage).sweep(page.color)
    assert [h.payload for h in hits] == ["937309"]
    assert hits[0].orientation_deg in (90, 270)  # found only once the page was turned
    top, left, bottom, right = hits[0].box
    assert (bottom - top) * PAGE_HEIGHT > (right - left) * PAGE_WIDTH  # still tall, in px
    assert contains(grow(box, top=0.01, left=0.01, bottom=0.01, right=0.01), hits[0].box)


@pytest.mark.parametrize("scanned_clockwise", [0, 90, 180, 270])
def test_barcode_orientation_is_the_rotation_that_makes_it_upright(
    scanned_clockwise: int,
    make_normalized: MakeNormalized,
    settings: Settings,
    storage: TmpdirStorage,
) -> None:
    image, _ = barcode_page("937309")
    page = make_normalized(pages.rotated(image, scanned_clockwise))
    hits = ZxingAdapter(settings=settings, storage=storage).sweep(page.color)
    assert [h.payload for h in hits] == ["937309"]  # one hit, however many angles decoded it
    assert hits[0].orientation_deg == (360 - scanned_clockwise) % 360


def test_sweep_without_barcode_returns_nothing(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage
) -> None:
    page = make_normalized(pages.clean_page())
    assert ZxingAdapter(settings=settings, storage=storage).sweep(page.color) == []


# ---------------------------------------------------------------- nearby text


def test_nearby_text_radius(settings: Settings) -> None:
    hit = _hit("937309", (0.40, 0.30, 0.43, 0.50))
    radius_px = settings.proximity_radius * float(np.hypot(*SIZE))  # ~104 px on this page
    to_right = 80 / PAGE_WIDTH  # 80 px
    above = 80 / PAGE_HEIGHT
    words = [
        _word("right-in", (0.41, 0.50 + to_right, 0.42, 0.50 + to_right + 0.01)),
        _word("above-in", (0.40 - above - 0.01, 0.35, 0.40 - above, 0.40)),
        _word("right-out", (0.41, 0.50 + 1.5 * to_right, 0.42, 0.50 + 1.5 * to_right + 0.01)),
        _word("above-out", (0.40 - 1.5 * above - 0.01, 0.35, 0.40 - 1.5 * above, 0.40)),
        _word("inside", (0.41, 0.35, 0.42, 0.40)),
    ]
    near = nearby_text(hit, words, radius_px=radius_px, size=SIZE)
    assert near[0] == "inside"  # nearest first
    assert set(near) == {"inside", "right-in", "above-in"}  # same 80 px counts on both axes


# ---------------------------------------------------------------- sideways captions


def _into_turned_crop(page_box: Box, area: Box, turn: int) -> Box:
    """Where a page-frame box lands in a crop of `area` turned `turn` degrees clockwise,
    computed forward and independently of the code under test."""
    h, w = area[2] - area[0], area[3] - area[1]
    y0, x0 = (page_box[0] - area[0]) / h, (page_box[1] - area[1]) / w
    y1, x1 = (page_box[2] - area[0]) / h, (page_box[3] - area[1]) / w
    if turn == 270:  # (u, v) -> (v, 1 - u)
        return (1 - x1, y0, 1 - x0, y1)
    if turn == 90:  # (u, v) -> (1 - v, u)
        return (x0, 1 - y1, x1, 1 - y0)
    raise AssertionError("only the 270 and 90 turns are scripted in these tests")


def _radius_px(settings: Settings) -> float:
    return settings.proximity_radius * float(np.hypot(*SIZE))


def test_sideways_caption_is_read_and_placed_on_the_page(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    page = make_normalized(pages.clean_page())
    barcode_box: Box = (0.40, 0.30, 0.43, 0.50)
    caption_box: Box = (0.395, 0.51, 0.435, 0.525)  # tall and narrow, just right of the bars
    (area, turn), _ = caption_strips(barcode_box, _radius_px(settings), SIZE)
    assert turn == 270 and area[1] < caption_box[1] and caption_box[3] < area[3]
    ocr = ScriptedOcr(
        crop_script=[
            [
                _word("מרכזי", _into_turned_crop(caption_box, area, turn)),
                _word("ALCh", (0.1, 0.1, 0.2, 0.2)),  # Latin noise: ignored
                _word("937309", (0.3, 0.3, 0.4, 0.4)),  # the bars' digits: ignored
            ]
        ]
    )
    result, hits = _read(
        page,
        settings,
        storage,
        imaging,
        ocr=ocr,
        barcode=ScriptedBarcode([_hit("937309", barcode_box)]),
    )
    caption = [w for w in result.words if w.text == "מרכזי"]  # type: ignore[attr-defined]
    assert len(caption) == 1
    assert caption[0].box == pytest.approx(caption_box, abs=1e-3)
    assert {w.text for w in result.words} == {"מרכזי"}  # type: ignore[attr-defined]
    assert "מרכזי" in hits[0].nearby_text
    assert len(ocr.crop_calls) == 2  # one strip beyond each end of the bars
    assert all(c["psm"] == SIDEWAYS_PSM and c["langs"] == "heb" for c in ocr.crop_calls)


def test_sideways_caption_of_a_sticker_applied_upside_down(
    make_normalized: MakeNormalized, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    page = make_normalized(pages.clean_page())
    barcode_box: Box = (0.40, 0.30, 0.43, 0.50)
    caption_box: Box = (0.395, 0.275, 0.435, 0.29)  # left of the bars
    _, (area, turn) = caption_strips(barcode_box, _radius_px(settings), SIZE)
    assert turn == 90
    ocr = ScriptedOcr(
        crop_script=[[], [_word("מרכזי", _into_turned_crop(caption_box, area, turn))]]
    )
    result, _ = _read(
        page,
        settings,
        storage,
        imaging,
        ocr=ocr,
        barcode=ScriptedBarcode([_hit("937309", barcode_box)]),
    )
    assert [w.box for w in result.words] == [pytest.approx(caption_box, abs=1e-3)]  # type: ignore[attr-defined]


def test_caption_strips_of_tall_bars_lie_above_and_below() -> None:
    tall: Box = (0.30, 0.50, 0.50, 0.56)
    (below, turn_below), (above, turn_above) = caption_strips(tall, 100.0, SIZE)
    assert below[0] == pytest.approx(tall[2]) and turn_below == 180
    assert above[2] == pytest.approx(tall[0]) and turn_above == 0


@pytest.mark.parametrize("turn", [90, 180, 270])
def test_unrotate_box_inverts_a_turn(turn: int) -> None:
    mask = np.zeros((200, 100), dtype=np.uint8)  # height 200, width 100
    mask[40:80, 10:30] = 1
    turned = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }[turn]
    rotated = cv2.rotate(mask, turned)
    ys, xs = np.nonzero(rotated)
    h, w = rotated.shape
    box: Box = (ys.min() / h, xs.min() / w, (ys.max() + 1) / h, (xs.max() + 1) / w)
    assert unrotate_box(box, turn) == pytest.approx((40 / 200, 10 / 100, 80 / 200, 30 / 100))
