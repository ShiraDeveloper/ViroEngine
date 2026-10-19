"""RegionDetectorPort — contract for page-region detectors.

Returns a list of labelled bounding boxes. Empty list is valid
(pipeline falls back to whole-page search with a score penalty).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.core.models import NormalizedPage, OcrResult, Region


@runtime_checkable
class RegionDetectorPort(Protocol):
    """Detect labelled regions on a normalised page.

    name: 'rule-based' | 'yolov8n-onnx' — recorded in every Region.detector.
    """

    name: str

    def detect(self, page: NormalizedPage, ocr: OcrResult) -> list[Region]:
        """Detect regions on the page.

        Returns a (possibly empty) list of Region objects.
        Empty → whole-page candidate search with a score penalty.
        Never raises.
        """
        ...
