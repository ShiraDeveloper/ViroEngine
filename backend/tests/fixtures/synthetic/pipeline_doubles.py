"""Per-page port doubles for the orchestrator tests: OCR, barcodes and regions scripted by page id.

An `ImageRef` carries the page id, so a double can answer differently for each page of a batch
while the images themselves are real synthetic PNGs. Nothing here touches the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.core.models import (
    BarcodeHit,
    ImageRef,
    NormalizedPage,
    OcrResult,
    OcrWord,
    ProviderHealth,
    ReaderName,
    Region,
    VisionRequest,
    VisionResponse,
)


@dataclass
class PageScriptedOcr:
    """OcrPort that returns the words scripted for the page an image belongs to.

    `crop_text[page_id]` is what a crop read of that page returns (a second, independent
    reading); `fail_on` names pages whose full-page read raises, to test failure isolation."""

    words: dict[str, list[OcrWord]] = field(default_factory=dict)
    crop_text: dict[str, str] = field(default_factory=dict)
    fail_on: frozenset[str] = frozenset()
    page_reads: list[str] = field(default_factory=list)
    name: str = "page-scripted-ocr"

    def read_page(self, image: ImageRef, langs: str = "heb+eng") -> OcrResult:
        if image.page_id in self.fail_on:
            raise RuntimeError("scripted OCR failure")
        self.page_reads.append(image.page_id)
        words = list(self.words.get(image.page_id, []))
        return OcrResult(reader=ReaderName.tesseract, words=words, text="")

    def read_crop(
        self,
        image: ImageRef,
        *,
        whitelist: str | None = None,
        psm: int = 7,
        langs: str | None = None,
    ) -> OcrResult:
        text = self.crop_text.get(image.page_id, "")
        words = [OcrWord(text=text, box=(0.0, 0.0, 1.0, 1.0), confidence=0.9)] if text else []
        return OcrResult(reader=ReaderName.tesseract, words=words, text=text)

    def detect_orientation(self, image: ImageRef) -> int | None:
        return None


@dataclass
class PageScriptedBarcode:
    """BarcodePort that returns the hits scripted for the page an image belongs to."""

    hits: dict[str, list[BarcodeHit]] = field(default_factory=dict)
    name: str = "page-scripted-barcode"

    def sweep(
        self, image: ImageRef, orientations: tuple[int, ...] = (0, 90, 180, 270)
    ) -> list[BarcodeHit]:
        return list(self.hits.get(image.page_id, []))


@dataclass
class PageScriptedRegions:
    """RegionDetectorPort that returns the regions scripted for a page."""

    regions: dict[str, list[Region]] = field(default_factory=dict)
    name: str = "page-scripted-regions"

    def detect(
        self,
        page: NormalizedPage,
        ocr: OcrResult,
        barcodes: Sequence[BarcodeHit],
        *,
        commitment_labels: Sequence[str] = (),
    ) -> list[Region]:
        return list(self.regions.get(page.raw.page_id, []))


@dataclass
class RecordingVision:
    """VisionLLMPort that counts calls; under LOCAL_ONLY it must stay at zero."""

    calls: int = 0
    name: str = "recording-vision"

    def extract(self, request: VisionRequest) -> VisionResponse:
        self.calls += 1
        raise AssertionError("a vision call was made")

    def health(self) -> ProviderHealth:
        self.calls += 1
        raise AssertionError("a vision call was made")
