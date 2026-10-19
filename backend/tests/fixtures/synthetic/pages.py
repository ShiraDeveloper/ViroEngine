"""Generators for synthetic page images (BGR uint8 arrays). Deterministic for a given seed.

Conventions used by the tests:
  * ``clean_page`` is upright, sharp, evenly lit and straight: S1 must report ``needs == []``.
  * ``rotated(img, k)`` is a page scanned k degrees clockwise; S1 must answer (360 - k) % 360.
  * ``skewed(img, d)`` turns the page d degrees counter-clockwise, so S1 reports skew -d.
"""

from __future__ import annotations

from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray

Pixels = NDArray[np.uint8]

PAGE_WIDTH: Final = 1000
PAGE_HEIGHT: Final = 1414  # A4 ratio

# Solid marker in the top-left margin; the text block stays clear of every corner patch.
_MARKER: Final = (20, 20, 80, 80)  # x0, y0, x1, y1
_MARGIN: Final = 120
_LINE_SPACING: Final = 44
_BLUE_INK: Final = (200, 60, 20)  # BGR
_PALE_STAMP: Final = (215, 170, 235)  # BGR, faint magenta


def clean_page(*, seed: int = 7, width: int = PAGE_WIDTH, height: int = PAGE_HEIGHT) -> Pixels:
    """An upright form-like page: ruled box, text lines, blue handwriting and a pale stamp."""
    rng = np.random.default_rng(seed)
    page = np.full((height, width, 3), 255, dtype=np.uint8)
    x0, x1 = _MARGIN, width - _MARGIN
    cv2.rectangle(page, (x0, _MARGIN + 10), (x1, _MARGIN + 150), (0, 0, 0), 3)
    cv2.line(page, (x0, _MARGIN + 80), (x1, _MARGIN + 80), (0, 0, 0), 3)
    words = ("FORM", "LAB", "SAMPLE", "RECEIVED", "NUMBER", "DATE", "CENTRAL", "UNIT", "ID")
    y = _MARGIN + 230
    while y < height - _MARGIN - 40:
        line = " ".join(words[i] for i in rng.integers(0, len(words), size=int(rng.integers(3, 6))))
        cv2.putText(page, line, (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2, cv2.LINE_AA)
        y += _LINE_SPACING
    # Blue "handwriting": a wandering polyline inside the ruled box.
    xs = np.linspace(x0 + 30, x1 - 30, 14).astype(int)
    ys = (_MARGIN + 45 + rng.integers(-18, 18, size=14)).astype(int)
    cv2.polylines(page, [np.column_stack([xs, ys])], False, _BLUE_INK, 3, cv2.LINE_AA)
    # Pale stamp, lower right, in the text block but clear of the bottom-right corner patch.
    cv2.rectangle(page, (x1 - 330, y - 230), (x1 - 20, y - 130), _PALE_STAMP, 3)
    cv2.putText(
        page,
        "RECEIVED",
        (x1 - 310, y - 165),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        _PALE_STAMP,
        2,
        cv2.LINE_AA,
    )
    mx0, my0, mx1, my1 = _MARKER
    cv2.rectangle(page, (mx0, my0), (mx1, my1), (0, 0, 0), -1)
    return page


def rotated(page: Pixels, clockwise_deg: int) -> Pixels:
    """The page as scanned `clockwise_deg` (0/90/180/270) clockwise from upright."""
    codes = {
        0: None,
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }
    code = codes[clockwise_deg]
    return page if code is None else np.asarray(cv2.rotate(page, code), dtype=np.uint8)


def skewed(page: Pixels, ccw_deg: float) -> Pixels:
    """The page turned `ccw_deg` degrees counter-clockwise, corners filled with white."""
    height, width = page.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), ccw_deg, 1.0)
    return np.asarray(
        cv2.warpAffine(
            page, matrix, (width, height), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255)
        ),
        dtype=np.uint8,
    )


def blurred(page: Pixels, sigma: float = 4.0) -> Pixels:
    """A defocused page."""
    return np.asarray(cv2.GaussianBlur(page, (0, 0), sigma), dtype=np.uint8)


def shadowed(page: Pixels, darkest: float = 0.45) -> Pixels:
    """Uneven lighting: brightness falls off smoothly from the left edge to `darkest`."""
    width = page.shape[1]
    ramp = np.linspace(1.0, darkest, width, dtype=np.float32)[None, :, None]
    return np.asarray(page.astype(np.float32) * ramp, dtype=np.uint8)


def fax(page: Pixels, *, seed: int = 11) -> Pixels:
    """Fax-grade scan: soft focus plus isolated black single-pixel dust on the paper."""
    rng = np.random.default_rng(seed)
    soft = cv2.GaussianBlur(cv2.cvtColor(page, cv2.COLOR_BGR2GRAY), (0, 0), 3.0)
    soft[(rng.random(soft.shape) < 0.012) & (soft > 240)] = 0  # dust lands on bare paper
    return np.asarray(cv2.cvtColor(soft, cv2.COLOR_GRAY2BGR), dtype=np.uint8)


def photographed(page: Pixels, *, squeeze: float = 0.12) -> Pixels:
    """The page photographed at an angle: a trapezoid on a dark table.

    `squeeze` is the share of the width the top edge is pulled in on each side.
    """
    height, width = page.shape[:2]
    canvas_w, canvas_h = int(width * 1.3), int(height * 1.2)
    table = np.full((canvas_h, canvas_w, 3), (45, 52, 60), dtype=np.uint8)
    ox, oy = (canvas_w - width) // 2, (canvas_h - height) // 2
    source = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    pull = squeeze * width
    target = np.array(
        [[ox + pull, oy], [ox + width - pull, oy], [ox + width, oy + height], [ox, oy + height]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(source, target)
    warped = cv2.warpPerspective(page, matrix, (canvas_w, canvas_h), borderValue=(0, 0, 0))
    mask = cv2.warpPerspective(
        np.full((height, width), 255, dtype=np.uint8), matrix, (canvas_w, canvas_h)
    )
    table[mask > 0] = warped[mask > 0]
    return table


def marker_corner_rotation(page: Pixels) -> int:
    """Clockwise rotation that would bring the top-left marker back to the top-left corner.

    The marker is the darkest of the four corner patches. This is the 'truth' the port
    doubles report, so S1's voting can be tested against a real image without Tesseract.
    """
    height, width = page.shape[:2]
    patch = max(8, round(0.1 * min(height, width)))
    grey = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    corners = {
        0: grey[:patch, :patch],  # top-left: already upright
        270: grey[:patch, -patch:],  # top-right: scanned 90 clockwise -> needs 270
        180: grey[-patch:, -patch:],  # bottom-right
        90: grey[-patch:, :patch],  # bottom-left: scanned 270 clockwise -> needs 90
    }
    return min(corners, key=lambda angle: float(corners[angle].mean()))
