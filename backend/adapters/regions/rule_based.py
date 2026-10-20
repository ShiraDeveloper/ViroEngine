"""RuleBasedDetector - RegionDetectorPort from labels, geometry and ruled lines (PLAN §4 S4).

Text-driven regions come from the pure finders in ``finders``; this module adds what needs
pixels: snapping the patient block and the receipt stamp to the ruled frame that encloses
them, finding tables by their line intersections, and letting blue ink (when present) widen a
handwritten sticker region. The yellow highlighter is not used here - at runtime it does not
exist - and the absence of blue ink changes nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray
import structlog

from backend.adapters.imaging.codec import Pixels, decode_bgr
from backend.adapters.regions import finders
from backend.core.errors import RenderFailed
from backend.core.geometry import area, contains, diagonal, grow, page_size, union
from backend.core.models import BarcodeHit, Box, NormalizedPage, OcrResult, Region, RegionKind
from backend.core.ports.storage import StoragePort
from backend.settings import Settings

log = structlog.get_logger()

Mask = NDArray[np.uint8]

# A clinical answer can stand anywhere along its label's line (on the sample forms it is up to
# half a page away), so the region runs to the page edge on the value side. Too large only
# costs a crop; too small would let clinical text leave the machine.
_WHOLE_LINE: Final = 1.0

# Longest side of the working copy the line analysis runs on: ruled lines stay a few pixels
# thick and the analysis stays fast on a 300 DPI page.
_WORK_SIDE: Final = 1600
# Shortest ruling kept, as a share of the page width (horizontal) or height (vertical): long
# enough to be a frame edge or a table line, too long to be a letter stem or a hyphen.
_H_LINE_SHARE: Final = 0.05
_V_LINE_SHARE: Final = 0.015
# A vertical line taller than this share of the page is a scanner streak or the page edge,
# not a frame side: kept in, it would tie every frame on the page into one.
_MAX_STREAK_SHARE: Final = 0.5
# Pixels a line is thickened by, so a frame with a hairline gap still encloses its interior.
_GAP_CLOSE_PX: Final = 3
# An enclosed area larger than this share of the page is the page background leaking through
# a broken frame, not the frame's interior.
_MAX_FRAME_SHARE: Final = 0.5
# A table needs a divider besides its outline: two cells share 6 line crossings.
_TABLE_MIN_CROSSINGS: Final = 6
# Blue ink in HSV (OpenCV hue 0-179): PLAN §4 S4 step 8.
_BLUE_LOW: Final = (90, 60, 50)
_BLUE_HIGH: Final = (130, 255, 255)
# Ink specks smaller than this many pixels (at working scale) are scanner noise.
_BLUE_MIN_PIXELS: Final = 12


class RuleBasedDetector:
    """Detect the seven region kinds with rules; the baseline the YOLO experiment must beat."""

    name = finders.DETECTOR_NAME

    def __init__(self, *, settings: Settings, storage: StoragePort) -> None:
        self._settings = settings
        self._storage = storage

    def detect(
        self,
        page: NormalizedPage,
        ocr: OcrResult,
        barcodes: Sequence[BarcodeHit],
        *,
        commitment_labels: Sequence[str] = (),
    ) -> list[Region]:
        """All regions found on the page, best-supported first within each kind. Never raises:
        when the image cannot be read the text-driven regions are still returned."""
        s = self._settings
        size = page_size(page)
        regions: list[Region] = finders.find_central_stickers(
            barcodes,
            ocr,
            radius=s.proximity_radius,
            fuzz_min=s.central_caption_fuzz,
            upright_fuzz_min=s.upright_caption_fuzz,
            size=size,
        )
        stamp = finders.find_receipt_stamp(
            ocr,
            fuzz_min=s.receipt_stamp_fuzz,
            tolerance=s.line_height_tolerance,
            radius=s.proximity_radius,
            size=size,
        )
        patient = finders.find_patient_box(
            ocr,
            fuzz_min=s.label_fuzz,
            tolerance=s.line_height_tolerance,
            radius=s.proximity_radius,
            value_expand=s.label_value_expand,
            size=size,
        )
        regions += finders.find_commitment_labels(
            ocr,
            commitment_labels,
            fuzz_min=s.label_fuzz,
            tolerance=s.line_height_tolerance,
            value_expand=s.label_value_expand,
        )
        regions += finders.find_clinical(
            ocr,
            fuzz_min=s.label_fuzz,
            tolerance=s.line_height_tolerance,
            radius=s.proximity_radius,
            value_expand=_WHOLE_LINE,
            size=size,
        )
        regions.append(finders.letterhead(s.letterhead_fraction))

        pixels = self._load(page)
        if pixels is None:
            regions += [r for r in (stamp, patient[0] if patient else None) if r is not None]
            return regions
        try:
            regions += self._with_pixels(
                pixels, regions, stamp, patient, size, handwritten=not barcodes
            )
        except cv2.error as exc:
            log.warning("region_refinement_failed", error_type=type(exc).__name__)
            regions += [r for r in (stamp, patient[0] if patient else None) if r is not None]
        return regions

    # ------------------------------------------------------------------ internals

    def _load(self, page: NormalizedPage) -> Pixels | None:
        """The page's colour pixels, or None when they are missing or undecodable."""
        data = self._storage.get(page.color)
        if not data:
            return None
        try:
            return decode_bgr(data)
        except RenderFailed:
            return None

    def _with_pixels(
        self,
        pixels: Pixels,
        regions: list[Region],
        stamp: Region | None,
        patient: tuple[Region, Box] | None,
        size: tuple[int, int],
        *,
        handwritten: bool,
    ) -> list[Region]:
        """The pixel-dependent regions, and `regions` refined in place where ink helps."""
        work = _downscale(pixels, _WORK_SIDE)
        lines = _ruling(np.asarray(cv2.cvtColor(work, cv2.COLOR_BGR2GRAY), dtype=np.uint8))
        extra: list[Region] = []

        if stamp is not None:
            frame = _enclosing_frame(lines, stamp.box)
            extra.append(stamp.model_copy(update={"box": frame}) if frame else stamp)
        if patient is not None:
            region, anchor = patient
            frame = _enclosing_frame(lines, anchor)
            extra.append(region.model_copy(update={"box": frame}) if frame else region)

        extra += _tables(lines)

        for index, region in enumerate(regions):
            # A sticker with no barcode is handwritten: ink, when there is any, shows where.
            if handwritten and region.kind is RegionKind.central_sticker:
                reach = self._settings.proximity_radius * diagonal(size)
                widened = _widen_with_blue_ink(work, region.box, reach, size)
                if widened != region.box:
                    regions[index] = region.model_copy(update={"box": widened})
        return extra


# ---------------------------------------------------------------------------------------
# pixel analysis
# ---------------------------------------------------------------------------------------


def _downscale(pixels: Pixels, max_side: int) -> Pixels:
    """Shrink so the longest side is at most `max_side`; never enlarges."""
    height, width = pixels.shape[:2]
    scale = max_side / max(height, width)
    if scale >= 1.0:
        return pixels
    return np.asarray(
        cv2.resize(pixels, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), dtype=np.uint8
    )


def _ruling(grey: Mask) -> Mask:
    """Mask of the long straight lines on the page (255 = line), thickened to close hairline
    gaps. Scanner streaks, which span much of the page, are left out."""
    height, width = grey.shape[:2]
    _, ink = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    horizontal = cv2.morphologyEx(
        ink,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(int(width * _H_LINE_SHARE), 2), 1)),
    )
    vertical = cv2.morphologyEx(
        ink,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(int(height * _V_LINE_SHARE), 2))),
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(vertical, connectivity=8)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_HEIGHT] > _MAX_STREAK_SHARE * height:
            vertical[labels == label] = 0
    lines = cv2.bitwise_or(horizontal, vertical)
    kernel = np.ones((_GAP_CLOSE_PX, _GAP_CLOSE_PX), dtype=np.uint8)
    return np.asarray(cv2.dilate(lines, kernel), dtype=np.uint8)


def _enclosing_frame(lines: Mask, anchor: Box) -> Box | None:
    """The interior of the ruled frame that encloses `anchor`, or None.

    The interior is a connected piece of paper bounded by lines. If the frame leaks (a gap
    wider than the closing allows) that piece is the whole page background and is rejected.
    """
    height, width = lines.shape[:2]
    count, labels = cv2.connectedComponents(cv2.bitwise_not(lines), connectivity=4)
    y0, x0 = int(anchor[0] * height), int(anchor[1] * width)
    y1, x1 = max(int(anchor[2] * height), y0 + 1), max(int(anchor[3] * width), x0 + 1)
    inside = labels[y0:y1, x0:x1]
    candidates = inside[inside > 0]
    if candidates.size == 0 or count < 2:
        return None
    label = int(np.bincount(candidates).argmax())
    ys, xs = np.nonzero(labels == label)
    box: Box = (
        ys.min() / height,
        xs.min() / width,
        (ys.max() + 1) / height,
        (xs.max() + 1) / width,
    )
    if area(box) > _MAX_FRAME_SHARE or not contains(
        grow(box, top=0.005, left=0.005, bottom=0.005, right=0.005), anchor
    ):
        return None
    return box


def _tables(lines: Mask) -> list[Region]:
    """Tables: ruled structures whose lines cross at least `_TABLE_MIN_CROSSINGS` times."""
    height, width = lines.shape[:2]
    horizontal = cv2.morphologyEx(
        lines,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(int(width * _H_LINE_SHARE), 2), 1)),
    )
    vertical = cv2.morphologyEx(
        lines,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(int(height * _V_LINE_SHARE), 2))),
    )
    crossings = cv2.bitwise_and(horizontal, vertical)
    _, _, _, centroids = cv2.connectedComponentsWithStats(crossings, connectivity=8)
    _, labels, stats, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
    per_structure: dict[int, int] = {}
    for cx, cy in centroids[1:]:
        label = int(labels[int(cy), int(cx)])
        if label > 0:
            per_structure[label] = per_structure.get(label, 0) + 1
    regions: list[Region] = []
    for label, crossing_count in per_structure.items():
        if crossing_count < _TABLE_MIN_CROSSINGS:
            continue
        x, y = int(stats[label, cv2.CC_STAT_LEFT]), int(stats[label, cv2.CC_STAT_TOP])
        w, h = int(stats[label, cv2.CC_STAT_WIDTH]), int(stats[label, cv2.CC_STAT_HEIGHT])
        regions.append(
            Region(
                kind=RegionKind.table,
                box=(y / height, x / width, (y + h) / height, (x + w) / width),
                detector=finders.DETECTOR_NAME,
                # Evidence grows with the number of crossings: 0.5 at the minimum, towards 1.
                score=crossing_count / (crossing_count + _TABLE_MIN_CROSSINGS),
            )
        )
    return sorted(regions, key=lambda r: r.score, reverse=True)


def _widen_with_blue_ink(work: Pixels, box: Box, reach_px: float, size: tuple[int, int]) -> Box:
    """`box` widened to cover blue ink within `reach_px` of it; unchanged when there is none.

    A hint only: handwriting the OCR could not read still shows up as ink, so the crop taken
    from this region will contain it. No ink, no change.
    """
    height, width = work.shape[:2]
    margin_y, margin_x = reach_px / size[1], reach_px / size[0]
    zone = grow(box, top=margin_y, bottom=margin_y, left=margin_x, right=margin_x)
    y0, x0 = int(zone[0] * height), int(zone[1] * width)
    y1, x1 = max(int(zone[2] * height), y0 + 1), max(int(zone[3] * width), x0 + 1)
    hsv = cv2.cvtColor(work[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    ink = cv2.inRange(
        hsv, np.array(_BLUE_LOW, dtype=np.uint8), np.array(_BLUE_HIGH, dtype=np.uint8)
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    blobs = [
        (
            (y0 + stats[i, cv2.CC_STAT_TOP]) / height,
            (x0 + stats[i, cv2.CC_STAT_LEFT]) / width,
            (y0 + stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT]) / height,
            (x0 + stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH]) / width,
        )
        for i in range(1, count)
        if stats[i, cv2.CC_STAT_AREA] >= _BLUE_MIN_PIXELS
    ]
    return union([box, *blobs]) if blobs else box
