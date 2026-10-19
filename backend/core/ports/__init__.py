"""Safe Line port Protocols — re-exported for convenience.

All ports are typing.Protocol so adapters need no inheritance.
Tests pass plain fakes (dataclasses) without subclassing anything.
"""

from __future__ import annotations

from backend.core.ports.barcode import BarcodePort
from backend.core.ports.clock import ClockPort
from backend.core.ports.document import DocumentPort
from backend.core.ports.exporter import ExporterPort
from backend.core.ports.imaging import ImagingPort
from backend.core.ports.learning import LearningStorePort
from backend.core.ports.ocr import OcrPort
from backend.core.ports.region_detector import RegionDetectorPort
from backend.core.ports.storage import StoragePort
from backend.core.ports.vision_llm import VisionLLMPort

__all__ = [
    "BarcodePort",
    "ClockPort",
    "DocumentPort",
    "ExporterPort",
    "ImagingPort",
    "LearningStorePort",
    "OcrPort",
    "RegionDetectorPort",
    "StoragePort",
    "VisionLLMPort",
]
