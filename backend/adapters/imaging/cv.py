"""CvImaging — ImagingPort implemented with OpenCV and NumPy (PLAN §4 S1-S2).

Measurement functions return raw numbers; the decision to correct is the core's, made from
configured thresholds. The constants below are algorithm parameters, not decision thresholds,
and each carries its justification.
"""

from __future__ import annotations

from collections.abc import Callable
import math
from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray
import structlog

from backend.adapters.imaging.codec import Pixels, decode_bgr, encode_png
from backend.core.errors import RenderFailed
from backend.core.ports.document import EncodedImage
from backend.core.ports.imaging import CORRECTION_OPS, PixelMeasurements
from backend.settings import Settings

log = structlog.get_logger()

Points = NDArray[np.float32]

# --- measurement parameters -------------------------------------------------

# Longest side of the working copy used for skew, illumination and page-quad analysis.
# Large enough to resolve a 0.1 degree tilt of a text line, small enough to be fast.
_WORK_SIDE: Final = 1600

# Two independent skew estimates closer than this are averaged; further apart they are
# contested and no skew is reported rather than a wrong one.
_SKEW_AGREEMENT_DEG: Final = 1.0
# Fewest Hough segments needed before their median counts as an estimate.
_MIN_HOUGH_LINES: Final = 3
# Horizontal closing width (px at working scale) that merges letters into line blobs,
# so a text line is measured through its long top and bottom edges rather than its glyphs.
_LINE_MERGE_PX: Final = 25
# Hough segments shorter than this share of the working side are glyph fragments.
_HOUGH_MIN_LENGTH_SHARE: Final = 0.12
_HOUGH_VOTES: Final = 60
_HOUGH_ANGLE_STEP: Final = math.pi / 720  # 0.25 degree resolution
_HOUGH_MAX_GAP_PX: Final = 12
# A contour smaller than this share of the image is a text block, not the page itself.
_MIN_PAGE_AREA_SHARE: Final = 0.25
# Douglas-Peucker tolerance, as a share of the page outline's perimeter, for a 4-corner fit.
_QUAD_EPSILON: Final = 0.02
# Largest blob (px, at ~300 DPI) still counted as a speck; a full stop is already ~10 px.
_SPECK_MAX_AREA: Final = 4
# Sigma (px at working scale) of the background blur; wider than any stamp or highlight.
_BACKGROUND_SIGMA: Final = 15.0
_BACKGROUND_DILATE_PX: Final = 7  # removes pen strokes (thinner than this) from the background

# --- correction parameters --------------------------------------------------

_CLAHE_CLIP_LIMIT: Final = 2.0  # PLAN §4 S2: contrast cap that keeps stamp edges from ringing
_CLAHE_TILES: Final = (8, 8)
_DENOISE_STRENGTH: Final = 7  # fastNlMeans h: removes fax grain without erasing thin strokes
_DENOISE_TEMPLATE: Final = 7
_DENOISE_SEARCH: Final = 21
_UNSHARP_AMOUNT: Final = 0.6  # "mild": a fax glyph gains edge contrast without halos
_UNSHARP_SIGMA: Final = 1.0
# Sauvola's R: dynamic range of the standard deviation for 8-bit images (skimage default).
_SAUVOLA_R: Final = 128.0
# Rows binarized at a time, so a 300 DPI page never needs several float32 full-size planes.
_BAND_ROWS: Final = 512
_WHITE: Final = (255, 255, 255)


class CvImaging:
    """Measure pixel quality and apply the named corrections with OpenCV."""

    name = "opencv"

    def __init__(self, *, settings: Settings) -> None:
        self._skew_max_deg = settings.skew_max_deg
        self._sharpen_scale = settings.sharpen_scale
        self._sauvola_window = settings.sauvola_window
        self._sauvola_k = settings.sauvola_k
        self._max_pixels = int(settings.max_render_megapixels * 1_000_000)

    # ------------------------------------------------------------------ measurement

    def measure_pixels(self, image: bytes) -> PixelMeasurements:
        """Measure the page; an undecodable image reports the neutral PixelMeasurements."""
        try:
            grey = _u8(cv2.cvtColor(decode_bgr(image), cv2.COLOR_BGR2GRAY))
            despeckled, noise = _despeckle(grey)
            small = _downscale(grey, _WORK_SIDE)
            return PixelMeasurements(
                skew_deg=_estimate_skew(small, self._skew_max_deg),
                blur_score=_laplacian_variance(despeckled),
                illumination_cv=_background_cv(small),
                noise_score=noise,
                perspective_deviation=_perspective_deviation(small),
            )
        except (RenderFailed, cv2.error) as exc:
            log.warning("pixel_measurement_failed", error_type=type(exc).__name__)
            return PixelMeasurements.unavailable()

    # ------------------------------------------------------------------ correction

    def apply_operation(self, image: bytes, op: str, *, angle_deg: float = 0.0) -> EncodedImage:
        """Apply one named correction; see ImagingPort.apply_operation for the contract."""
        if op not in CORRECTION_OPS:
            raise ValueError(f"unknown correction {op!r}")
        pixels = decode_bgr(image)
        try:
            if op == "rotate":
                result = _rotate(pixels, angle_deg)
            elif op == "perspective":
                result = self._perspective(pixels)
            elif op == "deskew":
                result = _deskew(pixels, angle_deg)
            elif op == "clahe":
                result = _clahe(pixels)
            elif op == "denoise":
                result = _denoise(pixels)
            else:
                result = self._sharpen(pixels)
        except cv2.error as exc:
            raise RenderFailed(f"{op} could not be applied") from exc
        return encode_png(result)

    def binarize(self, image: bytes) -> EncodedImage:
        """Sauvola-binarize (text black, paper white) for local OCR and barcode decoding."""
        try:
            grey = _u8(cv2.cvtColor(decode_bgr(image), cv2.COLOR_BGR2GRAY))
            return encode_png(_sauvola(grey, self._sauvola_window, self._sauvola_k))
        except cv2.error as exc:
            raise RenderFailed("binarization failed") from exc

    def thumbnail(self, image: bytes, *, max_side: int) -> EncodedImage:
        """Downscale to `max_side` on the longest side; smaller images are returned as is."""
        try:
            return encode_png(_downscale(decode_bgr(image), max_side))
        except cv2.error as exc:
            raise RenderFailed("thumbnail failed") from exc

    # ------------------------------------------------------------------ operations

    def _perspective(self, pixels: Pixels) -> Pixels:
        """Warp the detected page quadrilateral onto an upright rectangle."""
        small = _downscale(_u8(cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)), _WORK_SIDE)
        quad = _find_page_quad(small)
        if quad is None:
            raise RenderFailed("no page outline was found to correct")
        factor = pixels.shape[1] / small.shape[1]
        corners = _order_corners(quad * factor)
        top_left, top_right, bottom_right, bottom_left = corners
        width = max(
            np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left)
        )
        height = max(
            np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right)
        )
        out_w, out_h = round(float(width)), round(float(height))
        if out_w < 1 or out_h < 1 or out_w * out_h > self._max_pixels:
            raise RenderFailed("page outline gives an unusable size")
        target = np.array(
            [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype=np.float32
        )
        matrix = cv2.getPerspectiveTransform(corners, target)
        return np.asarray(
            cv2.warpPerspective(
                pixels,
                matrix,
                (out_w, out_h),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=_WHITE,
            ),
            dtype=np.uint8,
        )

    def _sharpen(self, pixels: Pixels) -> Pixels:
        """Cubic upscale then a mild unsharp mask. The upscale shrinks to fit the pixel budget."""
        height, width = pixels.shape[:2]
        scale = min(self._sharpen_scale, math.sqrt(self._max_pixels / (height * width)))
        if scale > 1.0:
            pixels = np.asarray(
                cv2.resize(pixels, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC),
                dtype=np.uint8,
            )
        blurred = cv2.GaussianBlur(pixels, (0, 0), _UNSHARP_SIGMA)
        return np.asarray(
            cv2.addWeighted(pixels, 1.0 + _UNSHARP_AMOUNT, blurred, -_UNSHARP_AMOUNT, 0),
            dtype=np.uint8,
        )


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------


def _u8(array: object) -> Pixels:
    """View an OpenCV result as the uint8 pixel array it is (OpenCV's stubs say 'any number')."""
    return np.asarray(array, dtype=np.uint8)


def _downscale(image: Pixels, max_side: int) -> Pixels:
    """Shrink so the longest side is at most `max_side`; never enlarges."""
    height, width = image.shape[:2]
    scale = max_side / max(height, width)
    if scale >= 1.0:
        return image
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return np.asarray(cv2.resize(image, size, interpolation=cv2.INTER_AREA), dtype=np.uint8)


def _laplacian_variance(grey: Pixels) -> float:
    """Variance of the Laplacian: the standard focus measure, higher = sharper."""
    _, std = cv2.meanStdDev(cv2.Laplacian(grey, cv2.CV_32F))
    return float(std[0][0]) ** 2


def _background_cv(small: Pixels) -> float:
    """Coefficient of variation of the paper background (ink removed, then smoothed)."""
    kernel = np.ones((_BACKGROUND_DILATE_PX, _BACKGROUND_DILATE_PX), np.uint8)
    background = cv2.GaussianBlur(cv2.dilate(small, kernel), (0, 0), _BACKGROUND_SIGMA)
    mean, std = cv2.meanStdDev(background)
    average = float(mean[0][0])
    return 0.0 if average <= 0.0 else float(std[0][0]) / average


def _despeckle(grey: Pixels) -> tuple[Pixels, float]:
    """Return the grey image with isolated specks painted white, and the speck share.

    A speck is an 8-connected ink blob of at most `_SPECK_MAX_AREA` pixels: fax grain and
    scanner dust, not strokes. The share (specks / all ink, 0..1) is the noise score.
    Removing the specks before measuring focus keeps grain from masquerading as sharpness.

    A 3x3 opening was tried first for the noise score and rejected: it also erases thin
    real strokes, so about half of clean 300 DPI sample pages scored above any usable
    threshold. A 3x3 median before the focus measure was rejected too: it pushed four
    sharp SAP screenshots under the blur threshold.
    """
    _, ink = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    total = cv2.countNonZero(ink)
    if total == 0:
        return grey, 0.0
    _, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    is_speck = stats[:, cv2.CC_STAT_AREA] <= _SPECK_MAX_AREA
    is_speck[0] = False  # label 0 is the background
    if not is_speck.any():
        return grey, 0.0
    noise = float(stats[is_speck, cv2.CC_STAT_AREA].sum()) / total
    mask = cv2.dilate(is_speck[labels].astype(np.uint8), np.ones((3, 3), np.uint8))
    cleaned = grey.copy()
    cleaned[mask > 0] = 255
    return cleaned, noise


def _fold_degrees(angle: float) -> float:
    """Fold an angle into [-45, 45): a page rotated by any multiple of 90 degrees has the
    same residual tilt, so skew is measured independently of orientation."""
    return (angle + 45.0) % 90.0 - 45.0


def _estimate_skew(small: Pixels, limit_deg: float) -> float:
    """Skew in degrees (positive = baseline descends to the right), 0.0 when unreliable.

    Two independent estimates are cross-checked: the median angle of long Hough segments
    over merged text lines, and the orientation of the minimum-area rectangle around all
    ink. Both must agree within a degree to be averaged; one alone is trusted, a
    contradiction reports 0.0, and anything beyond `limit_deg` is deemed a mis-measurement.
    """
    blurred = cv2.GaussianBlur(small, (5, 5), 0)
    ink = _u8(
        cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 15
        )
    )
    estimates = [e for e in (_hough_skew(ink), _rect_skew(ink)) if e is not None]
    if not estimates:
        return 0.0
    if len(estimates) == 2 and abs(estimates[0] - estimates[1]) > _SKEW_AGREEMENT_DEG:
        return 0.0
    skew = sum(estimates) / len(estimates)
    return skew if abs(skew) <= limit_deg else 0.0


def _hough_skew(ink: Pixels) -> float | None:
    """Length-weighted median angle of long segments along merged text lines."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (_LINE_MERGE_PX, 1))
    lines_mask = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel)
    edges = cv2.Canny(lines_mask, 50, 150)
    side = max(ink.shape[:2])
    segments = cv2.HoughLinesP(
        edges,
        1,
        _HOUGH_ANGLE_STEP,
        _HOUGH_VOTES,
        minLineLength=_HOUGH_MIN_LENGTH_SHARE * side,
        maxLineGap=_HOUGH_MAX_GAP_PX,
    )
    if segments is None or len(segments) < _MIN_HOUGH_LINES:
        return None
    weighted: list[tuple[float, float]] = []
    for x1, y1, x2, y2 in segments.reshape(-1, 4):
        angle = _fold_degrees(math.degrees(math.atan2(float(y2 - y1), float(x2 - x1))))
        weighted.append((angle, math.hypot(float(x2 - x1), float(y2 - y1))))
    weighted.sort()
    half = sum(length for _, length in weighted) / 2.0
    running = 0.0
    for angle, length in weighted:
        running += length
        if running >= half:
            return angle
    return None


def _rect_skew(ink: Pixels) -> float | None:
    """Orientation of the minimum-area rectangle around the ink, folded to [-45, 45)."""
    cleaned = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    points = cv2.findNonZero(cleaned)
    if points is None or len(points) < 4:
        return None
    box = cv2.boxPoints(cv2.minAreaRect(points))
    edge = box[1] - box[0]
    return _fold_degrees(math.degrees(math.atan2(float(edge[1]), float(edge[0]))))


def _find_page_quad(small: Pixels) -> Points | None:
    """The four corners of the page outline, or None when no page-sized quadrilateral exists.

    The page is the largest bright region (paper against a darker background). A scan whose
    paper fills the frame yields the frame itself, a rectangle.
    """
    blurred = cv2.GaussianBlur(small, (5, 5), 0)
    _, paper = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(paper, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    outline = max(contours, key=cv2.contourArea)
    if cv2.contourArea(outline) < _MIN_PAGE_AREA_SHARE * small.shape[0] * small.shape[1]:
        return None
    hull = cv2.convexHull(outline)
    approx = cv2.approxPolyDP(hull, _QUAD_EPSILON * cv2.arcLength(hull, True), True)
    if len(approx) != 4:
        return None
    return np.asarray(approx.reshape(4, 2), dtype=np.float32)


def _order_corners(quad: Points) -> Points:
    """Order corners top-left, top-right, bottom-right, bottom-left."""
    sums = quad.sum(axis=1)
    diffs = np.diff(quad, axis=1).ravel()  # y - x
    return np.asarray(
        [
            quad[np.argmin(sums)],
            quad[np.argmin(diffs)],
            quad[np.argmax(sums)],
            quad[np.argmax(diffs)],
        ],
        dtype=np.float32,
    )


def _perspective_deviation(small: Pixels) -> float:
    """Largest page-corner offset from its best-fit rectangle, as a share of the diagonal.

    A straight or merely rotated sheet fits a rectangle exactly (0.0); a photographed sheet
    with converging edges does not. 0.0 when no page outline is found.
    """
    quad = _find_page_quad(small)
    if quad is None:
        return 0.0
    rectangle = cv2.boxPoints(cv2.minAreaRect(quad))
    offset = max(min(float(np.linalg.norm(corner - fit)) for fit in rectangle) for corner in quad)
    return offset / math.hypot(small.shape[0], small.shape[1])


# ---------------------------------------------------------------------------
# Correction helpers
# ---------------------------------------------------------------------------


def _rotate(pixels: Pixels, angle_deg: float) -> Pixels:
    """Rotate clockwise by 0, 90, 180 or 270 degrees."""
    turns = {
        0: None,
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }
    quarter = round(angle_deg)
    if quarter not in turns or abs(angle_deg - quarter) > 1e-6:
        raise ValueError(f"rotation must be 0, 90, 180 or 270 degrees, got {angle_deg}")
    code = turns[quarter]
    return pixels if code is None else np.asarray(cv2.rotate(pixels, code), dtype=np.uint8)


def _deskew(pixels: Pixels, skew_deg: float) -> Pixels:
    """Remove a measured skew. A positive skew (clockwise tilt) is undone by a positive
    OpenCV angle, which turns the image counter-clockwise. Corners fill with white paper."""
    height, width = pixels.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), skew_deg, 1.0)
    return np.asarray(
        cv2.warpAffine(
            pixels,
            matrix,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=_WHITE,
        ),
        dtype=np.uint8,
    )


def _on_luminance(pixels: Pixels, transform: Callable[[Pixels], Pixels]) -> Pixels:
    """Run `transform` on the L channel of Lab only, leaving colour untouched.

    Working in Lab keeps the blue ink and the pale stamp exactly as scanned, which is why
    contrast and denoising never go through the colour channels.
    """
    lab = cv2.cvtColor(pixels, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = transform(np.ascontiguousarray(lab[:, :, 0]))
    return np.asarray(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), dtype=np.uint8)


def _flatten_and_equalize(luminance: Pixels) -> Pixels:
    """Divide out the slowly varying background, then apply CLAHE (PLAN §4 S2)."""
    small = _downscale(luminance, max(1, max(luminance.shape[:2]) // 4))
    kernel = np.ones((_BACKGROUND_DILATE_PX, _BACKGROUND_DILATE_PX), np.uint8)
    background = cv2.GaussianBlur(cv2.dilate(small, kernel), (0, 0), _BACKGROUND_SIGMA / 4.0)
    background = cv2.resize(
        background, (luminance.shape[1], luminance.shape[0]), interpolation=cv2.INTER_LINEAR
    )
    flat = cv2.divide(luminance, background, scale=255)
    clahe = cv2.createCLAHE(clipLimit=_CLAHE_CLIP_LIMIT, tileGridSize=_CLAHE_TILES)
    return np.asarray(clahe.apply(flat), dtype=np.uint8)


def _clahe(pixels: Pixels) -> Pixels:
    """Even out illumination and local contrast on the luminance channel only."""
    return _on_luminance(pixels, _flatten_and_equalize)


def _denoise(pixels: Pixels) -> Pixels:
    """Non-local-means denoising of the luminance channel only."""

    def denoise_luminance(luminance: Pixels) -> Pixels:
        return np.asarray(
            cv2.fastNlMeansDenoising(
                luminance, None, _DENOISE_STRENGTH, _DENOISE_TEMPLATE, _DENOISE_SEARCH
            ),
            dtype=np.uint8,
        )

    return _on_luminance(pixels, denoise_luminance)


def _sauvola(grey: Pixels, window: int, k: float) -> Pixels:
    """Sauvola local threshold, T = m * (1 + k * (s / R - 1)), computed in row bands.

    Local mean and standard deviation come from box filters, so cost is independent of the
    window size; bands (with a window-radius overlap) bound memory on a large page.
    """
    height = grey.shape[0]
    radius = window // 2
    out = np.empty_like(grey)
    for start in range(0, height, _BAND_ROWS):
        stop = min(start + _BAND_ROWS, height)
        lo, hi = max(0, start - radius), min(height, stop + radius)
        band = grey[lo:hi].astype(np.float32)
        mean = cv2.boxFilter(band, -1, (window, window), borderType=cv2.BORDER_REPLICATE)
        mean_sq = cv2.boxFilter(band * band, -1, (window, window), borderType=cv2.BORDER_REPLICATE)
        std = np.sqrt(np.maximum(mean_sq - mean * mean, 0.0))
        threshold = mean * (1.0 + k * (std / _SAUVOLA_R - 1.0))
        keep = slice(start - lo, stop - lo)
        out[start:stop] = np.where(band[keep] > threshold[keep], 255, 0).astype(np.uint8)
    return out
