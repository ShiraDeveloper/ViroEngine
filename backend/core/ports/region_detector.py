"""RegionDetectorPort — contract for page-region detectors.

Returns a list of labelled bounding boxes. Empty list is valid
(pipeline falls back to whole-page search with a score penalty).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from backend.core.models import BarcodeHit, NormalizedPage, OcrResult, Region


@runtime_checkable
class RegionDetectorPort(Protocol):
    """Detect labelled regions on a normalised page.

    name: 'rule-based' | 'yolov8n-onnx' — recorded in every Region.detector.
    """

    name: str

    def detect(
        self,
        page: NormalizedPage,
        ocr: OcrResult,
        barcodes: Sequence[BarcodeHit],
        *,
        commitment_labels: Sequence[str] = (),
    ) -> list[Region]:
        """Detect regions on the page; all boxes are in the page's normalized frame.

        barcodes: S3 hits, with nearby_text filled (central stickers are found through them).
        commitment_labels: the active sender's labels; none yields no commitment_label region.
        Returns a (possibly empty) list of Region objects.
        Empty → whole-page candidate search with a score penalty.
        Never raises.
        """
        ...
