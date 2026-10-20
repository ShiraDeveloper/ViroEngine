"""ZxingAdapter - BarcodePort implemented with zxing-cpp (PLAN §4 S3 step 4-5).

Any symbology and any payload length: nothing here inspects a payload. The page is read at
each requested orientation with zxing's own rotation search switched off, so the angle at
which a code is found is meaningful (zxing alone would find it at 0 every time) and, joined to
the reading direction zxing reports, gives the rotation that makes the code upright. A code
that is located but cannot be decoded is retried once on an upscaled crop of the same image -
that recovers the grainy fax pages.

Boxes are reported in the frame of the image that was passed in, whatever angle the code
was found at. `nearby_text` is left empty: filling it needs the page's OCR, which is S3's.
"""

from __future__ import annotations

from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray
import structlog
import zxingcpp

from backend.adapters.imaging.codec import decode_bgr
from backend.core.errors import RenderFailed
from backend.core.geometry import iou
from backend.core.models import BarcodeHit, Box, ImageRef
from backend.core.ports.storage import StoragePort
from backend.settings import Settings

log = structlog.get_logger()

Grey = NDArray[np.uint8]

# Context kept around an undecoded code when it is cropped for the retry: zxing needs the
# quiet zone on both sides of the bars, which a tight crop would cut off.
_RETRY_MARGIN: Final = 0.5  # of the located code's longer side

# Two decodes of one payload whose boxes overlap this much are the same physical code seen
# at two angles (a 1D code reads in both directions; a 2D code at every angle).
_SAME_CODE_IOU: Final = 0.3

_SUPPORTED_ANGLES: Final = (0, 90, 180, 270)


class ZxingAdapter:
    """Find and decode every barcode on a page."""

    name = "zxing"

    def __init__(self, *, settings: Settings, storage: StoragePort) -> None:
        self._upscale = settings.bars_retry_scale
        self._storage = storage

    def sweep(
        self,
        image: ImageRef,
        orientations: tuple[int, ...] = (0, 90, 180, 270),
    ) -> list[BarcodeHit]:
        """Decode all barcodes at the given clockwise angles; see BarcodePort.sweep.

        orientation_deg is the clockwise rotation of the page that would make this code upright:
        the angle at which it decoded plus the reading direction zxing reports there (0 or 180,
        since a 1D code also decodes upside down). It is what S1 votes with. Hits are ordered
        top to bottom, then left to right.
        """
        data = self._storage.get(image)
        if not data:
            return []
        try:
            grey = np.asarray(cv2.cvtColor(decode_bgr(data), cv2.COLOR_BGR2GRAY), dtype=np.uint8)
        except (RenderFailed, cv2.error):
            return []

        found: list[BarcodeHit] = []
        for angle in orientations:
            if angle not in _SUPPORTED_ANGLES:
                continue
            for hit in self._read_at(grey, angle):
                if not any(_same_code(hit, seen) for seen in found):
                    found.append(hit)
        found.sort(key=lambda h: (h.box[0], h.box[1]))
        return found

    # ------------------------------------------------------------------ internals

    def _read_at(self, grey: Grey, angle: int) -> list[BarcodeHit]:
        """Hits at one angle, boxes mapped back to the unrotated frame."""
        height, width = grey.shape[:2]
        rotated = _rotate(grey, angle)
        hits: list[BarcodeHit] = []
        for result in _read(rotated):
            decoded = result if result.valid else self._retry(rotated, result)
            if decoded is None:
                continue
            hits.append(_hit(decoded, angle, width, height))
        return hits

    def _retry(self, rotated: Grey, located: zxingcpp.Barcode) -> _Decoded | None:
        """Decode a located-but-failed code again on an upscaled crop of its surroundings."""
        corners = _corners(located)
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        pad = _RETRY_MARGIN * max(max(xs) - min(xs), max(ys) - min(ys))
        x0, y0 = max(int(min(xs) - pad), 0), max(int(min(ys) - pad), 0)
        x1, y1 = (
            min(int(max(xs) + pad), rotated.shape[1]),
            min(int(max(ys) + pad), rotated.shape[0]),
        )
        if x1 <= x0 or y1 <= y0:
            return None
        crop = np.asarray(
            cv2.resize(
                rotated[y0:y1, x0:x1],
                None,
                fx=self._upscale,
                fy=self._upscale,
                interpolation=cv2.INTER_CUBIC,
            ),
            dtype=np.uint8,
        )
        for again in _read(crop):
            if again.valid:
                shifted = [
                    (x0 + px / self._upscale, y0 + py / self._upscale) for px, py in _corners(again)
                ]
                return _Decoded(again.text, again.format.name, shifted, again.orientation)
        return None


class _Decoded:
    """A decoded code reduced to what BarcodeHit needs: payload, symbology, corner points."""

    def __init__(
        self, text: str, symbology: str, corners: list[tuple[float, float]], direction: float
    ) -> None:
        self.text = text
        self.symbology = symbology
        self.corners = corners
        self.direction = direction  # degrees: the reading direction zxing reported


def _read(image: Grey) -> list[zxingcpp.Barcode]:
    """Everything zxing locates in `image`, valid or not, without its own rotation search."""
    try:
        return list(zxingcpp.read_barcodes(image, try_rotate=False, return_errors=True))
    except (RuntimeError, ValueError) as exc:
        log.warning("barcode_read_failed", error_type=type(exc).__name__)
        return []


def _corners(result: zxingcpp.Barcode) -> list[tuple[float, float]]:
    """The four corner points (x, y) of a located code."""
    pos = result.position
    return [
        (float(p.x), float(p.y))
        for p in (pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left)
    ]


def _rotate(grey: Grey, angle: int) -> Grey:
    """The image turned `angle` degrees clockwise."""
    codes = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }
    return grey if angle == 0 else np.asarray(cv2.rotate(grey, codes[angle]), dtype=np.uint8)


def _unrotate(x: float, y: float, angle: int, width: int, height: int) -> tuple[float, float]:
    """Map a point of the image rotated `angle` clockwise back to the original (width x height)."""
    if angle == 90:
        return y, height - x
    if angle == 180:
        return width - x, height - y
    if angle == 270:
        return width - y, x
    return x, y


def _hit(result: zxingcpp.Barcode | _Decoded, angle: int, width: int, height: int) -> BarcodeHit:
    """A BarcodeHit with its box in the unrotated frame, normalized to 0..1."""
    if isinstance(result, _Decoded):
        text, symbology, corners = result.text, result.symbology, result.corners
        direction = result.direction
    else:
        text, symbology, corners = result.text, result.format.name, _corners(result)
        direction = float(result.orientation)
    mapped = [_unrotate(x, y, angle, width, height) for x, y in corners]
    xs, ys = [p[0] for p in mapped], [p[1] for p in mapped]
    box: Box = (
        min(max(min(ys) / height, 0.0), 1.0),
        min(max(min(xs) / width, 0.0), 1.0),
        min(max(max(ys) / height, 0.0), 1.0),
        min(max(max(xs) / width, 0.0), 1.0),
    )
    upright = (angle + _quarter_turns(direction)) % 360
    return BarcodeHit(
        payload=text, symbology=symbology, box=box, orientation_deg=upright, nearby_text=[]
    )


def _quarter_turns(direction: float) -> int:
    """A reading direction in degrees, snapped to the nearest multiple of 90 within 0..359."""
    return round(direction / 90.0) * 90 % 360


def _same_code(a: BarcodeHit, b: BarcodeHit) -> bool:
    """True when two hits are one physical code found twice."""
    return a.payload == b.payload and iou(a.box, b.box) >= _SAME_CODE_IOU
