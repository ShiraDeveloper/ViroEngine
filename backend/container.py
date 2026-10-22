"""Container — the single module permitted to import and construct adapters.

check_layering.py enforces that no other module imports from backend.adapters.
Dependency injection is done via build_container(); never construct adapters elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.pipeline.orchestrator import PipelineConfig
from backend.core.ports.barcode import BarcodePort
from backend.core.ports.clock import ClockPort
from backend.core.ports.document import DocumentPort
from backend.core.ports.exporter import ExporterPort
from backend.core.ports.imaging import ImagingPort
from backend.core.ports.learning import LearningStorePort
from backend.core.ports.logo import LogoMatcherPort
from backend.core.ports.ocr import OcrPort
from backend.core.ports.region_detector import RegionDetectorPort
from backend.core.ports.senders import SenderIndexPort
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
    documents: DocumentPort
    imaging: ImagingPort
    learning: LearningStorePort
    clock: ClockPort
    exporters: dict[str, ExporterPort]
    senders: SenderIndexPort  # the closed list of sending institutions (T6)
    logo: LogoMatcherPort | None  # ORB letterhead fallback; None when unavailable
    pipeline: PipelineConfig  # thresholds and layer switches from config/models.yaml
    # The blind second reader of the vision-first engine (ADR-017); None when LOCAL_ONLY=true.
    vision_verify: VisionLLMPort | None = None


DEFAULT_MODELS_PATH = Path(__file__).resolve().parents[1] / "config" / "models.yaml"


def load_pipeline_config(
    path: Path | None = None, *, layers: dict[str, bool] | None = None
) -> PipelineConfig:
    """Parse `config/models.yaml` into a PipelineConfig. `layers` overrides entries of the
    `layers:` section (the ablation switches), so a layer is toggled without a code edit.
    Raises FileNotFoundError, yaml.YAMLError or pydantic.ValidationError on a bad file."""
    import yaml  # type: ignore[import-untyped,unused-ignore]  # local: only the composition root reads files

    data: dict[str, Any] = yaml.safe_load((path or DEFAULT_MODELS_PATH).read_text(encoding="utf-8"))
    if layers:
        data["layers"] = {**data.get("layers", {}), **layers}
    return PipelineConfig.from_mapping(data)


DEFAULT_SEED_PATH = Path(__file__).resolve().parents[1] / "config" / "learning_seed.json"


def load_learning_seed(path: Path | None = None) -> dict[str, Any]:
    """The committed PII-free layout seed (PLAN §7.4), or an empty seed when there is none or it
    cannot be read. It only ever fills an empty learning store."""
    import json

    try:
        data = json.loads((path or DEFAULT_SEED_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
            FakeDocumentReader,
            FakeExporter,
            FakeImaging,
            FakeLearningStore,
            FakeOcr,
            FakeRegionDetector,
            FakeSenders,
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
            documents=FakeDocumentReader(),
            imaging=FakeImaging(),
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
            senders=FakeSenders(),
            logo=None,
            pipeline=load_pipeline_config(),
            vision_verify=None if settings.local_only else FakeVision(),
        )

    # --- Real adapters — created in later tasks (T9, T11, T13, T18, T19, T24) ---
    # Each import is lazy so `use_fakes=True` callers never trigger these.
    from backend.adapters.barcode.zxing import ZxingAdapter  # T11
    from backend.adapters.clock.system import SystemClock  # T3
    from backend.adapters.export.csv_exp import CsvExporter  # T19
    from backend.adapters.export.json_exp import JsonExporter  # T19
    from backend.adapters.export.xlsx import XlsxExporter  # T19
    from backend.adapters.imaging.cv import CvImaging  # T9
    from backend.adapters.imaging.pdf import DocumentReader  # T9
    from backend.adapters.learning.sqlite_store import SqliteLearningStore  # T24
    from backend.adapters.ocr.tesseract import TesseractAdapter  # T11
    from backend.adapters.ocr.trocr_digits import TrOcrDigits  # T13
    from backend.adapters.regions.rule_based import RuleBasedDetector  # T11
    from backend.adapters.senders.loader import SenderIndex  # T6
    from backend.adapters.senders.logo_match import OrbLogoMatcher  # T10
    from backend.adapters.storage.tempdir import TmpdirStorage  # T9

    storage = TmpdirStorage(settings=settings)
    learning = SqliteLearningStore(settings=settings)
    learning.import_seed(load_learning_seed())
    vision: VisionLLMPort | None = None
    vision_verify: VisionLLMPort | None = None
    if not settings.local_only:
        from backend.adapters.vision.chain import VisionChainAdapter  # T18

        # Two chains over the same keys: the page reader and the blind verifier try their models
        # in different orders, so the two readings of a value come from different models.
        vision = VisionChainAdapter(
            settings=settings, gemini_models=settings.gemini_page_models_list or None
        )
        vision_verify = VisionChainAdapter(
            settings=settings, gemini_models=settings.gemini_verify_models_list or None
        )

    regions: RegionDetectorPort = RuleBasedDetector(settings=settings, storage=storage)
    if settings.region_detector == "yolo_onnx":
        from backend.adapters.regions.yolo_onnx import YoloPreferred, YoloRegionDetector  # T16

        regions = YoloPreferred(
            yolo=YoloRegionDetector(settings=settings, storage=storage),
            fallback=regions,
            kinds=settings.yolo_kinds_list,
        )

    return Container(
        settings=settings,
        ocr_printed=TesseractAdapter(settings=settings, storage=storage),
        ocr_digits=TrOcrDigits(settings=settings, storage=storage),
        barcode=ZxingAdapter(settings=settings, storage=storage),
        regions=regions,
        vision=vision,
        storage=storage,
        documents=DocumentReader(settings=settings),
        imaging=CvImaging(settings=settings),
        learning=learning,
        clock=SystemClock(),
        exporters={
            "json": JsonExporter(),
            "xlsx": XlsxExporter(),
            "csv": CsvExporter(),
        },
        senders=SenderIndex.load(),
        logo=OrbLogoMatcher(storage),
        pipeline=load_pipeline_config(),
        vision_verify=vision_verify,
    )
