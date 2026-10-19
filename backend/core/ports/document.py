"""DocumentPort — contract for turning uploaded PDF/image bytes into rasters (S0).

The core never imports a PDF or imaging library; everything that touches pixels or PDF
structure sits behind this port so S0 stays a pure orchestration of safety checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EncodedImage:
    """A lossless PNG plus its size. width and height are pixels."""

    data: bytes
    width: int
    height: int


@runtime_checkable
class DocumentPort(Protocol):
    """Inspect and rasterise untrusted documents.

    name: recorded in logs so the renderer in use is traceable.
    """

    name: str

    def inspect_pdf(self, data: bytes) -> int:
        """Validate a PDF and return its page count.

        Raises EncryptedPdf for an encrypted file, ActiveContentPdf when the catalog holds
        /JavaScript, /JS, /Launch, /EmbeddedFile or /OpenAction, and RenderFailed when the
        file cannot be parsed at all. Never executes anything inside the PDF.
        """
        ...

    def render_pdf_page(self, data: bytes, index: int, *, dpi: int) -> EncodedImage:
        """Render page `index` (0-based) at `dpi` into a PNG on a white background.

        Raises RenderFailed when the page is missing, unreadable or larger than the
        configured pixel budget. The failure concerns this page only.
        """
        ...

    def decode_image(self, data: bytes) -> EncodedImage:
        """Decode a PNG/JPEG upload into an upright (EXIF-applied) RGB PNG.

        Raises RenderFailed when the bytes are not a decodable image or exceed the
        configured pixel budget.
        """
        ...
