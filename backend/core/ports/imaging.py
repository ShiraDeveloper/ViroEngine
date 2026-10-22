"""ImagingPort — contract for pixel measurement and targeted correction (S1, S2).

The core decides *what* to measure and *which* corrections to apply from configured
thresholds; this port only computes numbers and performs the named operations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from backend.core.models import Box
from backend.core.ports.document import EncodedImage

# The corrections S2 knows, in the only order they may be applied (PLAN §4 S2).
CORRECTION_OPS: Final[tuple[str, ...]] = (
    "rotate",
    "perspective",
    "deskew",
    "clahe",
    "denoise",
    "sharpen",
)


@dataclass(frozen=True)
class PixelMeasurements:
    """Raw per-page measurements. `measured` is False when the image was unusable.

    skew_deg: degrees, positive when the text baseline descends to the right (clockwise
        tilt in image coordinates), folded to (-45, 45] and bounded by the configured
        maximum; 0.0 when unreliable.
    blur_score: Laplacian variance of the full-resolution grey image, higher = sharper.
    illumination_cv: coefficient of variation of the estimated background, 0 = uniform.
    noise_score: share of ink pixels lying in isolated specks (blobs of at most 4 px), 0..1.
    perspective_deviation: largest page-corner offset from a true rectangle, as a fraction
        of the image diagonal; 0.0 when no page quadrilateral is found.
    """

    skew_deg: float
    blur_score: float
    illumination_cv: float
    noise_score: float
    perspective_deviation: float
    measured: bool = True

    @classmethod
    def unavailable(cls) -> PixelMeasurements:
        """Neutral result for an undecodable image: no correction can be justified."""
        return cls(
            skew_deg=0.0,
            blur_score=0.0,
            illumination_cv=0.0,
            noise_score=0.0,
            perspective_deviation=0.0,
            measured=False,
        )


@runtime_checkable
class ImagingPort(Protocol):
    """Measure and correct page images. All images travel as PNG bytes.

    name: recorded in logs so the implementation in use is traceable.
    """

    name: str

    def measure_pixels(self, image: bytes) -> PixelMeasurements:
        """Measure skew, blur, illumination, noise and perspective. Never raises; an image
        that cannot be decoded returns PixelMeasurements.unavailable()."""
        ...

    def apply_operation(self, image: bytes, op: str, *, angle_deg: float = 0.0) -> EncodedImage:
        """Apply one CORRECTION_OPS operation.

        angle_deg: for "rotate" the clockwise rotation (90, 180 or 270); for "deskew" the
        measured skew_deg to remove; ignored by the other operations.
        Raises RenderFailed when the operation cannot be applied to this image (the caller
        keeps the previous image) and ValueError for an unknown op or a bad angle.
        """
        ...

    def binarize(self, image: bytes) -> EncodedImage:
        """Sauvola-binarize into a single-channel PNG for local OCR and barcodes only.

        Raises RenderFailed when the image cannot be decoded."""
        ...

    def thumbnail(
        self, image: bytes, *, max_side: int, jpeg_quality: int | None = None
    ) -> EncodedImage:
        """Downscale so the longest side is at most `max_side` px; never upscales. PNG, or JPEG
        at `jpeg_quality` (1-100) when given - the vision-first page image.

        Raises RenderFailed when the image cannot be decoded."""
        ...

    def crop(self, image: bytes, box: Box, *, rotate_deg: int = 0) -> EncodedImage:
        """Cut `box` (normalized y_min, x_min, y_max, x_max) out of the image, then turn the
        piece `rotate_deg` degrees clockwise (0, 90, 180 or 270).

        Raises RenderFailed when the image cannot be decoded or the box holds no pixels, and
        ValueError for any other angle."""
        ...

    def fill_boxes(self, image: bytes, boxes: Sequence[Box], *, gray_level: int) -> EncodedImage:
        """Paint each normalized box solid grey (`gray_level`, 0-255) at full resolution, size
        unchanged. Used to redact a page BEFORE it is downscaled, so no glyph survives the
        resample. A box with no pixels is ignored.

        Raises RenderFailed when the image cannot be decoded."""
        ...
