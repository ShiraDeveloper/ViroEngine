"""Builders for upload bytes: PNG, JPEG, PDF and ZIP files, valid and hostile."""

from __future__ import annotations

import io
from typing import Final
import zipfile

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image
import pypdf

Pixels = NDArray[np.uint8]

PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"


def png_bytes(page: Pixels) -> bytes:
    """Lossless PNG of a BGR page."""
    ok, encoded = cv2.imencode(".png", page)
    assert ok
    return encoded.tobytes()


def jpeg_bytes(page: Pixels, *, quality: int = 90) -> bytes:
    """JPEG of a BGR page."""
    ok, encoded = cv2.imencode(".jpg", page, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return encoded.tobytes()


def corrupt_png() -> bytes:
    """Starts with the PNG signature but is not a decodable image."""
    return PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDRthis is not a real header"


def pdf_of_images(pages: list[Pixels]) -> bytes:
    """A raster PDF with one page per image (what a scanner produces)."""
    images = [Image.fromarray(cv2.cvtColor(p, cv2.COLOR_BGR2RGB)) for p in pages]
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", save_all=True, append_images=images[1:], resolution=150.0)
    return buffer.getvalue()


def pdf_with_page_sizes(sizes_pt: list[tuple[float, float]]) -> bytes:
    """A blank PDF whose pages have the given (width, height) in points."""
    writer = pypdf.PdfWriter()
    for width, height in sizes_pt:
        writer.add_blank_page(width=width, height=height)
    return _write(writer)


def encrypted_pdf() -> bytes:
    """A PDF protected with a password."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    return _write(writer)


def pdf_with_javascript() -> bytes:
    """A PDF whose catalog carries a document-level script."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_js("app.alert('hello');")
    return _write(writer)


def pdf_with_attachment() -> bytes:
    """A PDF with an embedded file."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_attachment("note.txt", b"payload")
    return _write(writer)


def pdf_with_open_action() -> bytes:
    """A PDF that runs a Launch action as soon as it is opened."""
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    action = DictionaryObject(
        {
            NameObject("/S"): NameObject("/Launch"),
            NameObject("/F"): TextStringObject("cmd.exe"),
        }
    )
    writer._root_object[NameObject("/OpenAction")] = writer._add_object(action)
    return _write(writer)


def zip_of(entries: dict[str, bytes], *, compress: bool = True) -> bytes:
    """A ZIP archive with the given member names and contents (names are not sanitised)."""
    buffer = io.BytesIO()
    method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(buffer, "w", method) as archive:
        for name, data in entries.items():
            archive.writestr(zipfile.ZipInfo(name), data, compress_type=method)
    return buffer.getvalue()


def zip_bomb(uncompressed_bytes: int = 8 * 1024 * 1024) -> bytes:
    """A small archive that expands enormously: a PNG signature followed by zeros."""
    payload = PNG_SIGNATURE + bytes(uncompressed_bytes)
    return zip_of({"bomb.png": payload})


def _write(writer: pypdf.PdfWriter) -> bytes:
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
