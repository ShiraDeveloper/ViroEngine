"""S3 Full-page local OCR and barcode sweep (PLAN §4 S3).

Reads the page completely locally: every word with its box, plus every barcode of any
symbology and any payload length. Empty output is valid (a blank page, a failed engine).

* Word boxes are normalized to 0..1 by the OCR adapter. Words below the confidence floor are
  left out of the text stream but stay in ``words`` - they are positional evidence for S4.
* Reading order: words are grouped into lines, lines run top to bottom, and each line is read
  right to left when Hebrew dominates it and left to right otherwise.
* Every barcode gets ``nearby_text``: the words close to it, nearest first. A sticker's
  caption ("מרכזי") sits that close; the next unrelated label does not.
* That caption is printed sideways, and a full-page pass reads it as Latin noise (measured on
  the sample forms). So the strip beyond each end of every barcode is read again, turned to
  make the text upright, and the Hebrew words found are added to the page's words.
* The receipt stamp is faint, overprinted and set in a large display face: the page pass reads
  at most its small lab-name lines, never its date (measured on A1, T13). So the area around
  every group of stamp words is read again as sparse text, and the words found are added.

No payload is filtered by length or shape anywhere in this module.
"""

from __future__ import annotations

from collections.abc import Sequence
import time
from typing import Final

import structlog

from backend.core.errors import RenderFailed
from backend.core.geometry import (
    Size,
    center,
    clamp,
    contains,
    diagonal,
    distance_to_box,
    from_crop,
    grow,
    iou,
    page_size,
    union,
    unrotate_box,
)
from backend.core.models import BarcodeHit, Box, NormalizedPage, OcrResult, OcrWord
from backend.core.ports.barcode import BarcodePort
from backend.core.ports.imaging import ImagingPort
from backend.core.ports.ocr import OcrPort
from backend.core.ports.storage import StoragePort
from backend.core.reading_order import group_lines, has_hebrew
from backend.core.text_match import best_similarity, similarity
from backend.settings import Settings

log = structlog.get_logger()

# Stickers are often applied sideways on an upright page, so the sweep covers all four.
BARCODE_ORIENTATIONS: Final = (0, 90, 180, 270)

# Tesseract page-segmentation mode for the caption reads: sparse text, no layout assumed. A
# single-line mode returns nothing on a strip that also holds a stray bar or a highlight.
SIDEWAYS_PSM: Final = 11
# Captions are Hebrew; English data would only slow the read down and add Latin noise.
SIDEWAYS_LANGS: Final = "heb"

# Where the caption is looked for, measured on the 48 sample pages (41 central stickers): a
# strip beyond each end of the bars, this share of the proximity radius long. 0.4 found 32 of
# them, 0.6 found 30 and 0.9 found 29 - longer strips let in the neighbouring sticker's text,
# and Tesseract's layout analysis then gives up.
_STRIP_REACH: Final = 0.4
# Strip margin across the bars: the bars outgrow the box the decoder reports, and the caption
# is taller than the bars. A share of the bars' own cross size plus a share of the radius.
_STRIP_CROSS_OF_BARS: Final = 0.4
_STRIP_CROSS_OF_RADIUS: Final = 0.25

# A sideways word that overlaps an upright word this much, with the same text, is the same
# word read twice.
_SAME_WORD_IOU: Final = 0.5

# The printed words of the lab's receipt stamp ("התקבל" / "המעבדה המרכזית לנגיפים" /
# "המרכז הרפואי המשולב"), one token each: the page pass reads them as separate words, if at all.
STAMP_WORDS: Final = ("התקבל", "המעבדה", "המרכזית", "לנגיפים", "המשולב")
# The sticker caption is a near match for "המרכזית"; a word closer to it is a caption, not a
# stamp word.
_CENTRAL_CAPTION: Final = "מרכזי"
# Sparse-text mode for the stamp re-read: the stamp mixes a display word, gapped digits and
# small lines, and a block mode drops the gapped digits (measured on A1).
STAMP_PSM: Final = 11
# Stamp words closer than this many proximity radii belong to one stamp.
_STAMP_CLUSTER_RADII: Final = 2.0


def read_page(
    page: NormalizedPage,
    *,
    settings: Settings,
    ocr: OcrPort,
    barcode: BarcodePort,
    imaging: ImagingPort,
    storage: StoragePort,
) -> tuple[OcrResult, list[BarcodeHit]]:
    """OCR the page (words normalized 0..1, text in reading order) and sweep it for barcodes.

    Text is read from `gray_bin` and barcodes from `color`. Never raises: the ports return
    empty results on failure, so an unreadable page yields no words and no hits.
    """
    started = time.perf_counter()
    raw = ocr.read_page(page.gray_bin, settings.ocr_langs)
    hits = barcode.sweep(page.color, BARCODE_ORIENTATIONS)

    size = page_size(page)
    radius_px = settings.proximity_radius * diagonal(size)
    words = list(raw.words)
    binarized = storage.get(page.gray_bin)
    for hit in hits:
        for extra in _caption_words(
            page, hit, binarized, radius_px, size, settings, ocr, imaging, storage
        ):
            if not _already_read(extra, words):
                words.append(extra)
    for extra in _stamp_words(page, words, binarized, size, settings, ocr, imaging, storage):
        if not _already_read(extra, words):
            words.append(extra)

    lines = group_lines(
        [w for w in words if w.confidence >= settings.ocr_min_word_confidence],
        tolerance=settings.line_height_tolerance,
    )
    result = raw.model_copy(
        update={"words": words, "text": "\n".join(" ".join(w.text for w in ln) for ln in lines)}
    )
    hits = [
        h.model_copy(update={"nearby_text": nearby_text(h, words, radius_px=radius_px, size=size)})
        for h in hits
    ]
    log.info(
        "stage_done",
        job_id=page.raw.image.job_id,
        page_id=page.raw.page_id,
        stage="S3",
        words=len(words),
        barcodes=len(hits),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return result, hits


def nearby_text(
    hit: BarcodeHit, words: Sequence[OcrWord], *, radius_px: float, size: Size
) -> list[str]:
    """Words whose centre lies within `radius_px` of the barcode's box, nearest first.

    Distance is measured to the box, not its centre: a wide sticker would otherwise push its
    own caption out of range. All words count, low-confidence ones included.
    """
    scored = sorted(
        (d, w.text)
        for w in words
        if (d := distance_to_box(center(w.box), hit.box, size)) <= radius_px
    )
    return [text for _, text in scored]


def caption_strips(box: Box, radius_px: float, size: Size) -> list[tuple[Box, int]]:
    """The two strips beyond the ends of a barcode where its caption would stand, each with
    the clockwise turn that makes text printed there upright.

    A sticker prints its caption beside the bars with the text running up the page; applied
    upside down or turned a quarter, the caption moves to the other end and the text turns
    with it. Wide bars (running left to right): right end 270, left end 90. Tall bars: bottom
    end 180, top end 0. Boxes are normalized and limited to the page.
    """
    y0, x0, y1, x1 = box
    width_px, height_px = (x1 - x0) * size[0], (y1 - y0) * size[1]
    reach = _STRIP_REACH * radius_px
    if width_px >= height_px:
        cross = (_STRIP_CROSS_OF_BARS * height_px + _STRIP_CROSS_OF_RADIUS * radius_px) / size[1]
        run = reach / size[0]
        return [
            (clamp((y0 - cross, x1, y1 + cross, x1 + run)), 270),
            (clamp((y0 - cross, x0 - run, y1 + cross, x0)), 90),
        ]
    cross = (_STRIP_CROSS_OF_BARS * width_px + _STRIP_CROSS_OF_RADIUS * radius_px) / size[0]
    run = reach / size[1]
    return [
        (clamp((y1, x0 - cross, y1 + run, x1 + cross)), 180),
        (clamp((y0 - run, x0 - cross, y0, x1 + cross)), 0),
    ]


def _caption_words(
    page: NormalizedPage,
    hit: BarcodeHit,
    binarized: bytes,
    radius_px: float,
    size: Size,
    settings: Settings,
    ocr: OcrPort,
    imaging: ImagingPort,
    storage: StoragePort,
) -> list[OcrWord]:
    """Hebrew words read in the caption strips of `hit`, placed back on the page frame.

    Only Hebrew words are kept: this pass exists for captions, and the bars would otherwise
    add spurious digits to the page. Confidence is not filtered - a small bold caption reads at
    0.25 and is still the caption; like every low-confidence word it stays in words and is
    left out of the text stream. Never raises.
    """
    if not binarized:
        return []
    found: list[OcrWord] = []
    for area, turn in caption_strips(hit.box, radius_px, size):
        try:
            strip = imaging.crop(binarized, area, rotate_deg=turn)
        except RenderFailed:
            continue  # the strip lies outside the page or the image is unreadable
        ref = storage.put_page_image(page.raw.image.job_id, page.raw.page_id, strip.data, "crop")
        for word in ocr.read_crop(ref, psm=SIDEWAYS_PSM, langs=SIDEWAYS_LANGS).words:
            if has_hebrew(word.text):
                box = from_crop(unrotate_box(word.box, turn), area)
                found.append(word.model_copy(update={"box": box}))
    return found


def stamp_areas(words: Sequence[OcrWord], size: Size, settings: Settings) -> list[Box]:
    """Areas to re-read for a receipt stamp: each group of stamp words grown by the stamp
    reach on every side, best-supported group first, at most ``stamp_reread_max`` of them.

    A group is found from its printed words alone, never from a date or a number. An area that
    lies inside one already chosen is not read twice.
    """
    anchors = [
        w
        for w in words
        if (score := best_similarity(w.text, STAMP_WORDS)) >= settings.receipt_stamp_fuzz
        and score > similarity(w.text, _CENTRAL_CAPTION)
    ]
    gap_px = _STAMP_CLUSTER_RADII * settings.proximity_radius * diagonal(size)
    groups: list[list[OcrWord]] = []
    for word in anchors:
        joined = [
            g
            for g in groups
            if any(distance_to_box(center(word.box), o.box, size) <= gap_px for o in g)
        ]
        merged = [word] + [w for g in joined for w in g]
        groups = [g for g in groups if g not in joined] + [merged]
    groups.sort(key=len, reverse=True)
    reach_px = settings.stamp_reread_reach * diagonal(size)
    dy, dx = reach_px / size[1], reach_px / size[0]
    areas: list[Box] = []
    for group in groups:
        area = grow(union(w.box for w in group), top=dy, left=dx, bottom=dy, right=dx)
        if any(contains(a, area) for a in areas):
            continue
        areas.append(area)
        if len(areas) >= settings.stamp_reread_max:
            break
    return areas


def _stamp_words(
    page: NormalizedPage,
    words: Sequence[OcrWord],
    binarized: bytes,
    size: Size,
    settings: Settings,
    ocr: OcrPort,
    imaging: ImagingPort,
    storage: StoragePort,
) -> list[OcrWord]:
    """Words read again in the stamp areas, placed back on the page frame. Every word is kept
    (the stamp's date is digits); confidence is left for the text-stream floor. Never raises."""
    if not binarized:
        return []
    found: list[OcrWord] = []
    for area in stamp_areas(words, size, settings):
        try:
            crop = imaging.crop(binarized, area)
        except RenderFailed:
            continue
        ref = storage.put_page_image(page.raw.image.job_id, page.raw.page_id, crop.data, "crop")
        for word in ocr.read_crop(ref, psm=STAMP_PSM, langs=settings.ocr_langs).words:
            found.append(word.model_copy(update={"box": from_crop(word.box, area)}))
    return found


def _already_read(word: OcrWord, words: Sequence[OcrWord]) -> bool:
    """True when `words` already holds this word at (nearly) the same place."""
    return any(w.text == word.text and iou(w.box, word.box) >= _SAME_WORD_IOU for w in words)
