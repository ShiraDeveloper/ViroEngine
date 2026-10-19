"""Port doubles for the S1/S2 tests. They answer from the real pixels of a synthetic page, so
orientation voting is exercised end to end without Tesseract or a barcode library."""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from backend.adapters.imaging.cv import CvImaging
from backend.core.errors import RenderFailed
from backend.core.models import BarcodeHit, ImageRef, OcrResult, OcrWord, ReaderName
from backend.core.ports.document import EncodedImage
from backend.core.ports.storage import StoragePort
from backend.tests.fixtures.synthetic.pages import marker_corner_rotation


def _rotation_needed(storage: StoragePort, ref: ImageRef) -> int:
    """Clockwise rotation that makes the stored page upright, read from its marker."""
    data = storage.get(ref)
    page = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    return marker_corner_rotation(np.asarray(page, dtype=np.uint8))


@dataclass
class MarkerBarcode:
    """BarcodePort that 'decodes' one barcode at the angle that makes the page upright."""

    storage: StoragePort
    present: bool = True
    name: str = "marker-barcode"

    def sweep(
        self, image: ImageRef, orientations: tuple[int, ...] = (0, 90, 180, 270)
    ) -> list[BarcodeHit]:
        if not self.present:
            return []
        return [
            BarcodeHit(
                payload="123456",
                symbology="Code128",
                box=(0.1, 0.1, 0.2, 0.3),
                orientation_deg=_rotation_needed(self.storage, image),
                nearby_text=[],
            )
        ]


@dataclass
class MarkerOcr:
    """OcrPort whose OSD is truthful (or scripted) and which 'reads' Hebrew words only on a
    page that is upright, like Tesseract would."""

    storage: StoragePort
    osd: int | str | None = "truth"  # "truth" = correct, an int/None = scripted answer
    hebrew_words: int = 12
    probes: list[str] = field(default_factory=list)  # kind of every image read_page saw
    name: str = "marker-ocr"

    def read_page(self, image: ImageRef, langs: str = "heb+eng") -> OcrResult:
        self.probes.append(langs)
        upright = _rotation_needed(self.storage, image) == 0
        words = (
            [
                OcrWord(text="שלום", box=(0.1, 0.1, 0.12, 0.2), confidence=0.9)
                for _ in range(self.hebrew_words)
            ]
            if upright
            else []
        )
        return OcrResult(
            reader=ReaderName.tesseract, words=words, text=" ".join(w.text for w in words)
        )

    def read_crop(
        self, image: ImageRef, *, whitelist: str | None = None, psm: int = 7
    ) -> OcrResult:
        return OcrResult(reader=ReaderName.tesseract, words=[], text="")

    def detect_orientation(self, image: ImageRef) -> int | None:
        if self.osd == "truth":
            return _rotation_needed(self.storage, image)
        return self.osd if isinstance(self.osd, int) or self.osd is None else None


class FailingImaging(CvImaging):
    """CvImaging whose chosen operations raise RenderFailed, to test failure handling."""

    def __init__(self, *, fail: frozenset[str], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._fail = fail

    def apply_operation(self, image: bytes, op: str, *, angle_deg: float = 0.0) -> EncodedImage:
        if op in self._fail:
            raise RenderFailed(f"{op} failed on purpose")
        return super().apply_operation(image, op, angle_deg=angle_deg)

    def binarize(self, image: bytes) -> EncodedImage:
        if "binarize" in self._fail:
            raise RenderFailed("binarize failed on purpose")
        return super().binarize(image)
