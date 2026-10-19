"""Container — the single module permitted to import and construct adapters.

check_layering.py enforces that no other module imports from backend.adapters.
Dependency injection is done via build_container(); never construct adapters elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.core.ports.barcode import BarcodePort
from backend.core.ports.clock import ClockPort
from backend.core.ports.exporter import ExporterPort
from backend.core.ports.learning import LearningStorePort
from backend.core.ports.ocr import OcrPort
from backend.core.ports.region_detector import RegionDetectorPort
from backend.core.ports.storage import StoragePort
from backend.core.ports.vision_llm import VisionLLMPort
from backend.settings import Settings


@dataclass(frozen=True)
class Container:
    """Immutable set of port implementations for a running app instance."""

    settings: Settings
    ocr_printed: OcrPort
    ocr_digits: OcrPort | None
    barcode: BarcodePort
    regions: RegionDetectorPort
    vision: VisionLLMPort | None  # None when LOCAL_ONLY=true
    storage: StoragePort
    learning: LearningStorePort
    clock: ClockPort
    exporters: dict[str, ExporterPort]


def build_container(settings: Settings, *, use_fakes: bool = False) -> Container:
    """Construct and return a Container.

    The ONLY module permitted to import adapters.
    use_fakes=True (tests, CI skeleton): wires fake adapters — zero real imports.
    use_fakes=False (production): imports real adapters from backend/adapters/*.
                                   Fails with ImportError until each adapter is built.
    """
    if use_fakes:
        from backend.adapters.fakes import (
            FakeBarcode,
            FakeExporter,
            FakeLearningStore,
            FakeOcr,
            FakeRegionDetector,
            FakeStorage,
            FakeVision,
            FixedClock,
        )

        return Container(
            settings=settings,
            ocr_printed=FakeOcr(name="fake_tesseract"),
            ocr_digits=FakeOcr(name="fake_trocr"),
            barcode=FakeBarcode(),
            regions=FakeRegionDetector(),
            vision=None if settings.local_only else FakeVision(),
            storage=FakeStorage(data_dir=settings.data_dir),
            learning=FakeLearningStore(),
            clock=FixedClock(),
            exporters={
                "json": FakeExporter(fmt="json", media_type="application/json"),
                "xlsx": FakeExporter(
                    fmt="xlsx",
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
                "csv": FakeExporter(fmt="csv", media_type="text/csv"),
            },
        )

    # --- Real adapters — created in later tasks (T9, T11, T13, T18, T19, T24) ---
    # Each import is lazy so `use_fakes=True` callers never trigger these.
    from backend.adapters.barcode.zxing import ZxingAdapter  # T11
    from backend.adapters.clock.system import SystemClock  # T3
    from backend.adapters.export.csv_exp import CsvExporter  # T19
    from backend.adapters.export.json_exp import JsonExporter  # T19
    from backend.adapters.export.xlsx import XlsxExporter  # T19
    from backend.adapters.learning.sqlite_store import SqliteLearningStore  # T24
    from backend.adapters.ocr.tesseract import TesseractAdapter  # T9
    from backend.adapters.ocr.trocr_digits import TrOcrDigitsAdapter  # T13
    from backend.adapters.regions.rule_based import RuleBasedDetector  # T11
    from backend.adapters.storage.tempdir import TmpdirStorage  # T9

    vision: VisionLLMPort | None = None
    if not settings.local_only:
        from backend.adapters.vision.chain import VisionChainAdapter  # T18

        vision = VisionChainAdapter(settings=settings)

    return Container(
        settings=settings,
        ocr_printed=TesseractAdapter(settings=settings),
        ocr_digits=TrOcrDigitsAdapter(settings=settings),
        barcode=ZxingAdapter(),
        regions=RuleBasedDetector(),
        vision=vision,
        storage=TmpdirStorage(settings=settings),
        learning=SqliteLearningStore(settings=settings),
        clock=SystemClock(),
        exporters={
            "json": JsonExporter(),
            "xlsx": XlsxExporter(),
            "csv": CsvExporter(),
        },
    )
