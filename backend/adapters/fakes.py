"""Fake adapters for testing and development.

Every port has exactly one fake here. Fakes are plain dataclasses —
no inheritance, no mocking library. Tests pass them directly as the port type
(structural typing via Protocol).

FixedClock.now() returns 2026-10-18T09:00:00+03:00 so date-validation tests
are deterministic regardless of when CI runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Literal

from backend.core.models import (
    BarcodeHit,
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
    RunReport,
    VisionRequest,
    VisionResponse,
)

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
    ) -> OcrResult:
        """Always returns an empty OcrResult. Never raises."""
        return OcrResult(reader=ReaderName.tesseract, words=[], text="", candidates=[])


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

    def detect(self, page: NormalizedPage, ocr: OcrResult) -> list[Region]:
        """Always returns an empty list. Never raises."""
        return []


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
        kind: Literal["color", "gray_bin", "thumb", "crop"],  # type: ignore[override]
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
