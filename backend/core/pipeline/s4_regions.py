"""S4 Regions - where on the page each field is read from (PLAN §4 S4).

The configured detector (rule-based by default, YOLO if T16 wins) finds the regions; this stage
adds the learning store's hints for the sender as extra regions. Hints compete with detections
instead of overriding them: they carry a modest score and the ``learned`` detector name.

An empty result is valid - S6 then searches the whole page with a score penalty.
"""

from __future__ import annotations

import time
from typing import Final

import structlog

from backend.core.models import BarcodeHit, FieldKey, NormalizedPage, OcrResult, Region, SenderSpec
from backend.core.ports.learning import LearningStorePort
from backend.core.ports.region_detector import RegionDetectorPort
from backend.settings import Settings

log = structlog.get_logger()

LEARNED_DETECTOR: Final = "learned"


def detect_regions(
    page: NormalizedPage,
    ocr: OcrResult,
    barcodes: list[BarcodeHit],
    *,
    settings: Settings,
    sender: SenderSpec | None,
    learning: LearningStorePort,
    detector: RegionDetectorPort,
) -> list[Region]:
    """Regions from the detector plus the sender's learned hints (when the sender is known).

    `sender` supplies the commitment labels the detector looks for and the key under which
    the learning store holds locations. Never raises; a detector or store that finds nothing
    contributes nothing.
    """
    started = time.perf_counter()
    labels = sender.commitment_labels if sender is not None else []
    regions = list(detector.detect(page, ocr, barcodes, commitment_labels=labels))
    learned = _learned(sender, settings, learning)
    regions += learned
    log.info(
        "stage_done",
        job_id=page.raw.image.job_id,
        page_id=page.raw.page_id,
        stage="S4",
        detector=detector.name,
        regions=len(regions),
        learned=len(learned),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return regions


def _learned(
    sender: SenderSpec | None, settings: Settings, learning: LearningStorePort
) -> list[Region]:
    """Stored locations for the sender as regions scored no higher than ``learned_region_score``.
    The same hint recorded under several fields yields one region."""
    if sender is None:
        return []
    seen: set[tuple[str, tuple[float, ...]]] = set()
    regions: list[Region] = []
    for field in FieldKey:
        for hint in learning.layout_hints(sender.canonical, field.value):
            key = (hint.region_kind.value, hint.box)
            if key in seen:
                continue
            seen.add(key)
            regions.append(
                Region(
                    kind=hint.region_kind,
                    box=hint.box,
                    detector=LEARNED_DETECTOR,
                    score=min(hint.confidence, settings.learned_region_score),
                )
            )
    return regions
