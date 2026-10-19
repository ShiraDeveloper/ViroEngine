"""OcrPort — contract for all OCR readers.

Never raises on unreadable input; returns an empty OcrResult instead.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.core.models import ImageRef, OcrResult


@runtime_checkable
class OcrPort(Protocol):
    """Read text from a page image or a crop.

    name: recorded in the reading trail so the source is always traceable.
    """

    name: str

    def read_page(self, image: ImageRef, langs: str = "heb+eng") -> OcrResult:
        """Read a full-page image.

        langs: Tesseract language string (e.g. 'heb+eng').
        Never raises on unreadable input; returns an empty OcrResult.
        """
        ...

    def read_crop(
        self,
        image: ImageRef,
        *,
        whitelist: str | None = None,
        psm: int = 7,
    ) -> OcrResult:
        """Read a cropped region.

        psm=7 (single line) is the default for field crops.
        whitelist: Tesseract character whitelist (digits only for ID fields).
        Never raises; returns empty OcrResult.
        """
        ...
