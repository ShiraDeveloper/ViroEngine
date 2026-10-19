"""DocumentReader — DocumentPort over pypdf (safety), pypdfium2 (render) and Pillow (images).

Nothing inside an uploaded PDF is ever executed: pypdf only parses structure, and pdfium is
used without its form/JavaScript engine. The catalog is still scanned for scripts and
embedded files, because a scanned commitment form has no legitimate use for either.
"""

from __future__ import annotations

import io
import math
import threading
from typing import Final

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps
import pypdf
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject
import pypdfium2 as pdfium
import structlog

from backend.adapters.imaging.codec import encode_png
from backend.core.errors import ActiveContentPdf, EncryptedPdf, RenderFailed, UnsupportedMedia
from backend.core.ports.document import EncodedImage
from backend.settings import Settings

log = structlog.get_logger()

# PDF names whose presence in the catalog graph means active or embedded content.
_ACTIVE_NAMES: Final = frozenset(
    {"/JavaScript", "/JS", "/Launch", "/EmbeddedFile", "/EmbeddedFiles", "/OpenAction"}
)

# Upper bound on objects visited while scanning the catalog. A real 200-page form PDF has
# a few thousand; a hostile file could chain millions to burn CPU.
_MAX_PDF_NODES: Final = 500_000

# PDF user space is 1/72 inch.
_POINTS_PER_INCH: Final = 72.0

# pdfium is not thread-safe; the job queue renders from worker threads, so calls serialise.
_PDFIUM_LOCK: Final = threading.Lock()

_WHITE: Final = 255
_ALPHA_MODES: Final = frozenset({"RGBA", "LA", "PA"})
_SIXTEEN_BIT_MODES: Final = frozenset({"I;16", "I;16L", "I;16B", "I;16N"})


class DocumentReader:
    """Inspect PDFs, render their pages and decode image uploads."""

    name = "pypdf+pdfium+pillow"

    def __init__(self, *, settings: Settings) -> None:
        self._max_pixels = int(settings.max_render_megapixels * 1_000_000)

    # ------------------------------------------------------------------ DocumentPort

    def inspect_pdf(self, data: bytes) -> int:
        """Return the page count after refusing encrypted or scripted PDFs."""
        try:
            reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
            encrypted = reader.is_encrypted
        except Exception as exc:  # pypdf raises many types on damaged input; re-raised named
            raise RenderFailed("PDF could not be parsed") from exc
        if encrypted:
            raise EncryptedPdf("PDF is encrypted")
        try:
            has_active_content = _has_active_content(reader)
            pages = len(reader.pages)
        except UnsupportedMedia:
            raise
        except Exception as exc:  # same: damaged structure surfaces as arbitrary types
            raise RenderFailed("PDF structure could not be read") from exc
        if has_active_content:
            raise ActiveContentPdf("PDF contains scripts, launch actions or embedded files")
        return pages

    def render_pdf_page(self, data: bytes, index: int, *, dpi: int) -> EncodedImage:
        """Render one page to a white-background BGR PNG within the pixel budget."""
        with _PDFIUM_LOCK:
            try:
                document = pdfium.PdfDocument(data)
            except Exception as exc:  # pdfium raises PdfiumError and ValueError variants
                raise RenderFailed("PDF could not be opened") from exc
            try:
                if not 0 <= index < len(document):
                    raise RenderFailed("page index is outside the document")
                page = document[index]
                try:
                    return self._render(page, dpi)
                finally:
                    page.close()
            except RenderFailed:
                raise
            except Exception as exc:  # pdfium surfaces render errors as several types
                raise RenderFailed("page could not be rendered") from exc
            finally:
                document.close()

    def decode_image(self, data: bytes) -> EncodedImage:
        """Decode PNG/JPEG into an upright BGR PNG, flattening transparency onto white."""
        try:
            with Image.open(io.BytesIO(data)) as source:
                width, height = source.size
                if width < 1 or height < 1 or width * height > self._max_pixels:
                    raise RenderFailed("image exceeds the pixel budget")
                upright = ImageOps.exif_transpose(source)
                upright.load()
                pixels = _to_bgr(upright)
        except RenderFailed:
            raise
        except Exception as exc:  # Pillow raises OSError, SyntaxError, ValueError, bomb errors
            raise RenderFailed("image could not be decoded") from exc
        return encode_png(pixels)

    # ------------------------------------------------------------------ internals

    def _render(self, page: pdfium.PdfPage, dpi: int) -> EncodedImage:
        """Render an open pdfium page; the pixel budget is checked before any allocation."""
        width_pt, height_pt = page.get_size()
        scale = dpi / _POINTS_PER_INCH
        pixels = width_pt * scale * height_pt * scale
        if not math.isfinite(pixels) or pixels < 1 or pixels > self._max_pixels:
            raise RenderFailed("page size exceeds the pixel budget")
        bitmap = page.render(scale=scale)  # white background, BGR channel order
        array = np.ascontiguousarray(bitmap.to_numpy())
        if array.ndim != 3 or array.shape[2] != 3:
            raise RenderFailed("renderer returned an unexpected pixel layout")
        return encode_png(array)


def _to_bgr(image: Image.Image) -> NDArray[np.uint8]:
    """Convert any Pillow image to a contiguous BGR uint8 array (alpha flattened to white)."""
    if image.mode in _SIXTEEN_BIT_MODES:
        grey = np.asarray(image, dtype=np.uint16) // 257  # 65535 -> 255
        rgb = np.repeat(grey.astype(np.uint8)[:, :, None], 3, axis=2)
        return np.ascontiguousarray(rgb[:, :, ::-1])
    if image.mode in _ALPHA_MODES or "transparency" in image.info:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, (_WHITE, _WHITE, _WHITE, _WHITE))
        image = Image.alpha_composite(canvas, rgba)
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    return np.ascontiguousarray(rgb[:, :, ::-1])


def _has_active_content(reader: pypdf.PdfReader) -> bool:
    """Walk the catalog graph looking for scripts, launch actions and embedded files.

    Iterative with a visited set and a node budget, so cyclic or enormous structures cannot
    exhaust the stack or the CPU. Names are matched both as dictionary keys and as values
    (an action is ``<< /S /JavaScript >>``).
    """
    stack: list[object] = [reader.trailer["/Root"]]
    seen: set[tuple[int, int]] = set()
    visited = 0
    while stack:
        node = stack.pop()
        visited += 1
        if visited > _MAX_PDF_NODES:
            raise UnsupportedMedia("PDF structure is too large to inspect")
        if isinstance(node, IndirectObject):
            reference = (node.idnum, node.generation)
            if reference in seen:
                continue
            seen.add(reference)
            try:
                node = node.get_object()
            except Exception:  # unresolvable reference: nothing there to inspect
                log.debug("pdf_unresolvable_reference")
                continue
        if isinstance(node, NameObject):
            if str(node) in _ACTIVE_NAMES:
                return True
        elif isinstance(node, DictionaryObject):
            for key, value in node.items():
                if str(key) in _ACTIVE_NAMES:
                    return True
                stack.append(value)
        elif isinstance(node, ArrayObject):
            stack.extend(node)
    return False
