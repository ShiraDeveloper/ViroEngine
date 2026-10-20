"""Synthetic pages carrying real, decodable barcodes (generated, never taken from samples)."""

from __future__ import annotations

import cv2
import numpy as np
import zxingcpp

from backend.core.models import Box
from backend.tests.fixtures.synthetic.pages import PAGE_HEIGHT, PAGE_WIDTH, Pixels


def barcode_page(
    payload: str,
    *,
    top: int = 300,
    left: int = 300,
    width: int = 420,
    height: int = 120,
    vertical: bool = False,
) -> tuple[Pixels, Box]:
    """A white page with a Code 128 barcode of `payload` at (top, left), upright or turned a
    quarter. Returns the page (BGR) and the normalized box the bars occupy."""
    bars = np.asarray(
        zxingcpp.write_barcode(zxingcpp.BarcodeFormat.Code128, payload, width=width, height=height),
        dtype=np.uint8,
    )
    if vertical:
        bars = np.asarray(np.rot90(bars), dtype=np.uint8)
    page = np.full((PAGE_HEIGHT, PAGE_WIDTH, 3), 255, dtype=np.uint8)
    h, w = bars.shape[:2]
    page[top : top + h, left : left + w] = cv2.cvtColor(bars, cv2.COLOR_GRAY2BGR)
    box: Box = (
        top / PAGE_HEIGHT,
        left / PAGE_WIDTH,
        (top + h) / PAGE_HEIGHT,
        (left + w) / PAGE_WIDTH,
    )
    return page, box
