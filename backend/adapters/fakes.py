"""Fake adapters for testing and development.

Every port has exactly one fake here. Fakes are plain dataclasses —
no inheritance, no mocking library. Tests pass them directly as the port type
(structural typing via Protocol).

FixedClock.now() returns 2026-10-18T09:00:00+03:00 so date-validation tests
are deterministic regardless of when CI runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import struct
from typing import Any, Literal
import zlib

from backend.core.models import (
    BarcodeHit,
    Box,
    CommitmentRow,
    ImageRef,
    LayoutHint,
    LayoutObservation,
    NormalizedPage,
    OcrResult,
    PatternHint,
    ProviderHealth,
    ReaderName,
    Region,
    RunMetric,
    RunReport,
    SenderSpec,
    VisionRequest,
    VisionResponse,
)
from backend.core.ports.document import EncodedImage
from backend.core.ports.imaging import PixelMeasurements

# Pinned instant used by FixedClock — one hour after hackathon day 1 opens
_FIXED_INSTANT = datetime(2026, 10, 18, 9, 0, 0, tzinfo=timezone(timedelta(hours=3)))


# ---------------------------------------------------------------------------
# OcrPort fake
# ---------------------------------------------------------------------------


@dataclass
class FakeOcr:
    """Returns empty OcrResult for every call. Safe for all pipeline stages."""

    name: str = "fake_ocr"

    def read_page(self, image: ImageRef, langs: str = "heb+eng") -> OcrResult:
        """Always returns an empty OcrResult. Never raises."""
        return OcrResult(reader=ReaderName.tesseract, words=[], text="", candidates=[])

    def read_crop(
        self,
        image: ImageRef,
        *,
        whitelist: str | None = None,
        psm: int = 7,
        langs: str | None = None,
    ) -> OcrResult:
        """Always returns an empty OcrResult. Never raises."""
        return OcrResult(reader=ReaderName.tesseract, words=[], text="", candidates=[])

    def detect_orientation(self, image: ImageRef) -> int | None:
        """Always abstains: a fake cannot tell which way a page faces."""
        return None

    def languages(self) -> list[str] | None:
        """A fake always has the packs the product needs."""
        return ["eng", "heb", "osd"]


# ---------------------------------------------------------------------------
# BarcodePort fake
# ---------------------------------------------------------------------------


@dataclass
class FakeBarcode:
    """Returns an empty barcode list. Accepts any image without raising."""

    name: str = "fake_barcode"

    def sweep(
        self,
        image: ImageRef,
        orientations: tuple[int, ...] = (0, 90, 180, 270),
    ) -> list[BarcodeHit]:
        """Always returns an empty list. Never raises."""
        return []


# ---------------------------------------------------------------------------
# RegionDetectorPort fake
# ---------------------------------------------------------------------------


@dataclass
class FakeRegionDetector:
    """Returns an empty region list; triggers whole-page fallback in the pipeline."""

    name: str = "fake_region_detector"

    def detect(
        self,
        page: NormalizedPage,
        ocr: OcrResult,
        barcodes: Sequence[BarcodeHit],
        *,
        commitment_labels: Sequence[str] = (),
    ) -> list[Region]:
        """Always returns an empty list. Never raises."""
        return []


# ---------------------------------------------------------------------------
# DocumentPort and ImagingPort fakes
# ---------------------------------------------------------------------------


def _blank_png(width: int, height: int) -> bytes:
    """A valid all-white greyscale PNG, built with the standard library only."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    rows = (b"\x00" + b"\xff" * width) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


_FAKE_PAGE = EncodedImage(data=_blank_png(8, 8), width=8, height=8)


@dataclass
class FakeDocumentReader:
    """Every PDF has one page; every page and image is the same blank 8x8 raster."""

    name: str = "fake_documents"

    def inspect_pdf(self, data: bytes) -> int:
        """Always one page. Never raises."""
        return 1

    def render_pdf_page(self, data: bytes, index: int, *, dpi: int) -> EncodedImage:
        """Always the blank raster. Never raises."""
        return _FAKE_PAGE

    def decode_image(self, data: bytes) -> EncodedImage:
        """Always the blank raster. Never raises."""
        return _FAKE_PAGE


@dataclass
class FakeImaging:
    """Measures a flawless page and leaves every image unchanged."""

    name: str = "fake_imaging"

    def measure_pixels(self, image: bytes) -> PixelMeasurements:
        """A sharp, straight, evenly lit page: nothing needs correcting."""
        return PixelMeasurements(
            skew_deg=0.0,
            blur_score=1000.0,
            illumination_cv=0.0,
            noise_score=0.0,
            perspective_deviation=0.0,
        )

    def apply_operation(self, image: bytes, op: str, *, angle_deg: float = 0.0) -> EncodedImage:
        """Returns the input unchanged, sized from the fake raster."""
        return EncodedImage(data=image, width=_FAKE_PAGE.width, height=_FAKE_PAGE.height)

    def binarize(self, image: bytes) -> EncodedImage:
        """Returns the input unchanged."""
        return EncodedImage(data=image, width=_FAKE_PAGE.width, height=_FAKE_PAGE.height)

    def thumbnail(
        self, image: bytes, *, max_side: int, jpeg_quality: int | None = None
    ) -> EncodedImage:
        """Returns the input unchanged."""
        return EncodedImage(data=image, width=_FAKE_PAGE.width, height=_FAKE_PAGE.height)

    def crop(self, image: bytes, box: Box, *, rotate_deg: int = 0) -> EncodedImage:
        """Returns the input unchanged."""
        return EncodedImage(data=image, width=_FAKE_PAGE.width, height=_FAKE_PAGE.height)

    def fill_boxes(self, image: bytes, boxes: Sequence[Box], *, gray_level: int) -> EncodedImage:
        """Returns the input unchanged (the fake raster has nothing to redact)."""
        return EncodedImage(data=image, width=_FAKE_PAGE.width, height=_FAKE_PAGE.height)


# ---------------------------------------------------------------------------
# VisionLLMPort fake
# ---------------------------------------------------------------------------

_FAKE_VISION_RESPONSE = VisionResponse(
    raw_text="",
    candidates=[],
    provider=ReaderName.gemini,
    model_id="fake-model-v0",
    prompt_version="v0",
    duration_ms=0,
)


@dataclass
class FakeVision:
    """Returns a fixed schema-valid VisionResponse. Simulates a healthy provider."""

    name: str = "fake_vision"

    def extract(self, request: VisionRequest) -> VisionResponse:
        """Returns a fixed empty response. Never raises, never makes network calls."""
        return _FAKE_VISION_RESPONSE

    def health(self) -> ProviderHealth:
        """Reports one fake credential, circuit closed."""
        return ProviderHealth(name=self.name, credentials=1, state="ok")


# ---------------------------------------------------------------------------
# StoragePort fake
# ---------------------------------------------------------------------------


@dataclass
class FakeStorage:
    """In-memory image store. Suitable for unit and integration tests."""

    data_dir: str = "./.tmp"
    _store: dict[str, bytes] = field(default_factory=dict)

    def put_page_image(
        self,
        job_id: str,
        page_id: str,
        data: bytes,
        kind: Literal["color", "gray_bin", "thumb", "crop"],
    ) -> ImageRef:
        """Store bytes in memory and return a reference."""
        path = f"{job_id}/{page_id}/{kind}"
        self._store[path] = data
        sha = hashlib.sha256(data).hexdigest()
        return ImageRef(
            job_id=job_id,
            page_id=page_id,
            kind=kind,
            path=path,
            width=0,
            height=0,
            sha256=sha,
        )

    def get(self, ref: ImageRef) -> bytes:
        """Return stored bytes, or empty bytes if not found."""
        return self._store.get(ref.path, b"")

    def purge_job(self, job_id: str) -> int:
        """Remove all entries for job_id. Returns count removed."""
        keys = [k for k in self._store if k.startswith(f"{job_id}/")]
        for k in keys:
            del self._store[k]
        return len(keys)


# ---------------------------------------------------------------------------
# LearningStorePort fake
# ---------------------------------------------------------------------------


@dataclass
class FakeLearningStore:
    """No-op learning store — observations are accepted and discarded."""

    def record_confirmation(self, obs: LayoutObservation) -> None:
        """Accept and discard the observation."""

    def layout_hints(self, sender: str, field: str) -> list[LayoutHint]:
        """Always returns an empty list."""
        return []

    def pattern_hints(self, sender: str, field: str) -> list[PatternHint]:
        """Always returns an empty list."""
        return []

    def label_synonyms(self, sender: str, field: str) -> list[str]:
        """Always returns an empty list."""
        return []

    def record_run(self, metric: RunMetric) -> None:
        """Accept and discard the metric."""

    def run_metrics(self, limit: int = 200) -> list[dict[str, Any]]:
        """No runs are kept."""
        return []

    def proposals(self) -> dict[str, list[dict[str, Any]]]:
        """No proposals are kept."""
        return {"labels": [], "senders": []}

    def approve(self, kind: Literal["label", "sender"], proposal_id: int) -> bool:
        """There is nothing to approve."""
        return False

    def seed(self) -> dict[str, list[dict[str, Any]]]:
        """Nothing was learned."""
        return {"layout_hints": [], "pattern_hints": []}


# ---------------------------------------------------------------------------
# SenderIndexPort fake
# ---------------------------------------------------------------------------


@dataclass
class FakeSenders:
    """An empty closed list: nothing ever matches, so every sender is reported as unlisted."""

    min_ratio: int = 88
    specs: tuple[SenderSpec, ...] = ()

    def get(self, canonical: str) -> SenderSpec | None:
        """Always None: the list is empty."""
        return None

    def match(self, text: str, *, min_ratio: int | None = None) -> None:
        """Always None: nothing in an empty list can match. Never raises."""
        return None


# ---------------------------------------------------------------------------
# ExporterPort fake
# ---------------------------------------------------------------------------


@dataclass
class FakeExporter:
    """Returns a minimal valid JSON blob regardless of the export format."""

    fmt: str = "json"
    media_type: str = "application/json"

    def render(self, report: RunReport, rows: list[CommitmentRow]) -> bytes:
        """Return a minimal valid payload. Never raises."""
        return b'{"rows":[],"report":{}}'


# ---------------------------------------------------------------------------
# ClockPort fake
# ---------------------------------------------------------------------------


@dataclass
class FixedClock:
    """Returns a fixed datetime so date-validation tests are deterministic.

    Fixed instant: 2026-10-18T09:00:00+03:00
    """

    _fixed: datetime = field(default_factory=lambda: _FIXED_INSTANT)

    def now(self) -> datetime:
        """Return the pinned instant."""
        return self._fixed
