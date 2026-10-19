"""S0 Intake — turn an untrusted upload into stored, hashed page rasters (PLAN §4 S0).

The chain runs in this order: magic bytes -> size -> ZIP safety -> PDF safety -> page count
-> render -> hash. Content decides the file type, never the extension; the extension is
only a tripwire, so a PNG renamed ``.pdf`` is refused.

Two failure classes are kept apart on purpose:
  * a safety violation (wrong type, oversized, encrypted or scripted PDF, ZIP bomb or path
    traversal, too many pages) refuses the whole upload by raising a SafeLineError;
  * a file or page that is merely unreadable (corrupt image, page too large to render) is
    reported in ``IntakeResult.failures`` and the rest of the batch continues.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import io
import math
import re
import time
from typing import Final, Literal
import zipfile
import zlib

import structlog

from backend.core.errors import (
    RenderFailed,
    SafeLineError,
    TooLarge,
    UnsupportedMedia,
)
from backend.core.models import PageRaw, Strict
from backend.core.ports.document import DocumentPort
from backend.core.ports.storage import StoragePort
from backend.settings import Settings

log = structlog.get_logger()

DocumentKind = Literal["pdf", "png", "jpeg", "zip"]

# Magic numbers of the only accepted types. Matched at offset 0 of the first 4 KB.
_SNIFF_BYTES: Final = 4096
_MAGIC: Final[tuple[tuple[bytes, DocumentKind], ...]] = (
    (b"%PDF-", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),  # empty archive: rejected later as "no documents"
)

# Extensions that claim a type. A claim that contradicts the content is refused.
_EXTENSION_KIND: Final[dict[str, DocumentKind]] = {
    ".pdf": "pdf",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".zip": "zip",
}

# Archive members that desktop tools add on their own; skipped, not rejected.
_JUNK_BASENAMES: Final = frozenset({"thumbs.db", "desktop.ini"})

# Display and page-id limits. Structural: keep ids short and filesystem-neutral.
_MAX_NAME_CHARS: Final = 255
_MAX_STEM_CHARS: Final = 64
_STEM_UNSAFE: Final = re.compile(r"[^\w.\-]+")
_DRIVE_PREFIX: Final = re.compile(r"^[A-Za-z]:")

# ZIP unix mode bits: a symlink entry would make the member's content a path, not a file.
_ZIP_S_IFMT: Final = 0o170000
_ZIP_S_IFLNK: Final = 0o120000
_ZIP_FLAG_ENCRYPTED: Final = 0x1

# Failures reading a ZIP member that mean "this entry is damaged", not "this is an attack".
_ZIP_READ_ERRORS: Final = (
    zipfile.BadZipFile,
    zlib.error,
    OSError,
    EOFError,
    RuntimeError,
    NotImplementedError,
)


class UploadedBlob(Strict):
    """One uploaded file: the client's filename (untrusted) and its bytes."""

    filename: str
    data: bytes


class PageFailure(Strict):
    """A file or page that could not be turned into a raster; the batch continued."""

    source_file: str
    page_index: int | None  # 0-based within the file; None = the whole file
    code: str  # stable SafeLineError.code
    recoverable: bool


class IntakeResult(Strict):
    """Pages in upload order plus everything that could not be rendered."""

    pages: list[PageRaw]
    failures: list[PageFailure]


@dataclass(frozen=True)
class _Leaf:
    """A single non-archive document ready for inspection."""

    name: str
    kind: DocumentKind
    data: bytes


def intake(
    uploads: Sequence[UploadedBlob],
    *,
    settings: Settings,
    documents: DocumentPort,
    storage: StoragePort,
    job_id: str,
    known_hashes: frozenset[str] = frozenset(),
) -> IntakeResult:
    """Validate an upload and render it into one stored page image per page.

    Raises UnsupportedMedia, TooLarge, EncryptedPdf or ActiveContentPdf (all refuse the
    whole upload). Unreadable files or pages come back in `failures`, never as an
    exception. `known_hashes` marks sample pages (sha256 of the rendered bytes);
    pages not in it stay `known_sample=False`, the safe default (PLAN §8.5).
    """
    started = time.perf_counter()
    if not uploads:
        raise UnsupportedMedia("the upload holds no file")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    kinds = [_classify(blob.filename, blob.data) for blob in uploads]  # 1. magic bytes
    total = sum(len(blob.data) for blob in uploads)
    if total > max_bytes:  # 2. size
        raise TooLarge(f"upload of {total} bytes exceeds the {settings.max_upload_mb} MB limit")

    failures: list[PageFailure] = []
    leaves: list[_Leaf] = []
    zip_budget = max_bytes - sum(
        len(blob.data) for blob, kind in zip(uploads, kinds, strict=True) if kind != "zip"
    )
    for blob, kind in zip(uploads, kinds, strict=True):
        if kind == "zip":  # 3. ZIP safety
            members, used = _expand_zip(blob, settings=settings, byte_budget=zip_budget)
            zip_budget -= used
            leaves.extend(members.leaves)
            failures.extend(members.failures)
        else:
            leaves.append(_Leaf(name=_basename(blob.filename), kind=kind, data=blob.data))

    counts = _inspect_leaves(leaves, documents=documents, failures=failures)  # 4. PDF safety
    if sum(counts) > settings.max_pages:  # 5. page count
        raise TooLarge(f"{sum(counts)} pages exceed the {settings.max_pages}-page limit")

    pages = _render_leaves(  # 6. render, 7. hash
        leaves,
        counts,
        settings=settings,
        documents=documents,
        storage=storage,
        job_id=job_id,
        known_hashes=known_hashes,
        failures=failures,
    )
    log.info(
        "stage_done",
        job_id=job_id,
        stage="S0",
        pages=len(pages),
        failures=len(failures),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return IntakeResult(pages=pages, failures=failures)


# ---------------------------------------------------------------------------
# Step 1 - classify by content
# ---------------------------------------------------------------------------


def _classify(filename: str, data: bytes) -> DocumentKind:
    """Return the content type from magic bytes; refuse unknown content and any extension
    that claims a different type than the bytes prove."""
    head = data[:_SNIFF_BYTES]
    kind = next((candidate for magic, candidate in _MAGIC if head.startswith(magic)), None)
    if kind is None:
        raise UnsupportedMedia("content is not a PDF, PNG, JPEG or ZIP")
    base = _basename(filename)
    dot = base.rfind(".")
    claimed = _EXTENSION_KIND.get(base[dot:].lower()) if dot >= 0 else None
    if claimed is not None and claimed != kind:
        raise UnsupportedMedia("file content does not match its extension")
    return kind


def _basename(name: str) -> str:
    """Filename without any directory part or control characters; never empty."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(ch for ch in base if ch.isprintable())[:_MAX_NAME_CHARS]
    return cleaned or "upload"


# ---------------------------------------------------------------------------
# Step 3 - ZIP safety
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Expanded:
    leaves: list[_Leaf]
    failures: list[PageFailure]


def _expand_zip(
    blob: UploadedBlob, *, settings: Settings, byte_budget: int
) -> tuple[_Expanded, int]:
    """Validate an archive and return its documents plus the uncompressed bytes used.

    Refuses path escapes, encrypted or symlink entries, nested archives, a compression
    ratio above `zip_max_ratio`, more entries than `max_pages` and more uncompressed bytes
    than the remaining budget. A damaged member is reported as a failure instead.
    """
    name = _basename(blob.filename)
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob.data))
    except zipfile.BadZipFile:
        return _Expanded([], [_failure(name, None, RenderFailed())]), 0

    with archive:
        members: list[zipfile.ZipInfo] = []
        for info in archive.infolist():
            _check_member_path(info.filename)
            if not info.is_dir() and not _is_junk(info.filename):
                members.append(info)
        if not members:
            raise UnsupportedMedia("the archive holds no documents")
        if len(members) > settings.max_pages:
            raise TooLarge(f"{len(members)} archive entries exceed the page limit")

        uncompressed = 0
        for info in members:
            if info.flag_bits & _ZIP_FLAG_ENCRYPTED:
                raise UnsupportedMedia("encrypted archive entry")
            if (info.external_attr >> 16) & _ZIP_S_IFMT == _ZIP_S_IFLNK:
                raise UnsupportedMedia("archive entry is a symbolic link")
            if info.file_size > 0:
                ratio = math.inf if info.compress_size == 0 else info.file_size / info.compress_size
                if ratio > settings.zip_max_ratio:
                    raise TooLarge("archive compression ratio looks like a ZIP bomb")
            uncompressed += info.file_size
        if uncompressed > byte_budget:
            raise TooLarge("archive expands beyond the upload size limit")

        leaves: list[_Leaf] = []
        failures: list[PageFailure] = []
        for info in members:
            entry = _basename(info.filename)
            try:
                data = archive.read(info)  # bounded by the declared, already-budgeted size
            except _ZIP_READ_ERRORS:
                failures.append(_failure(entry, None, RenderFailed()))
                continue
            kind = _classify(info.filename, data)
            if kind == "zip":
                raise UnsupportedMedia("nested archives are not accepted")
            leaves.append(_Leaf(name=entry, kind=kind, data=data))
    return _Expanded(leaves, failures), uncompressed


def _check_member_path(name: str) -> None:
    """Refuse an entry that is absolute, has a drive letter, a NUL, or climbs with '..'."""
    normalized = name.replace("\\", "/")
    if (
        "\x00" in name
        or normalized.startswith("/")
        or _DRIVE_PREFIX.match(normalized) is not None
        or ".." in normalized.split("/")
    ):
        raise UnsupportedMedia("archive entry path escapes the archive")


def _is_junk(name: str) -> bool:
    """True for metadata members that archivers add (``__MACOSX/``, dotfiles, Thumbs.db)."""
    parts = name.replace("\\", "/").split("/")
    base = parts[-1]
    return parts[0] == "__MACOSX" or base.startswith(".") or base.lower() in _JUNK_BASENAMES


# ---------------------------------------------------------------------------
# Steps 4-5 - PDF safety and page count
# ---------------------------------------------------------------------------


def _inspect_leaves(
    leaves: Sequence[_Leaf], *, documents: DocumentPort, failures: list[PageFailure]
) -> list[int]:
    """Return the page count of every leaf (0 for an unreadable file, which is recorded).

    EncryptedPdf and ActiveContentPdf propagate: they refuse the whole upload.
    """
    counts: list[int] = []
    for leaf in leaves:
        if leaf.kind != "pdf":
            counts.append(1)
            continue
        try:
            pages = documents.inspect_pdf(leaf.data)
        except RenderFailed as exc:
            failures.append(_failure(leaf.name, None, exc))
            counts.append(0)
            continue
        if pages < 1:
            failures.append(_failure(leaf.name, None, RenderFailed()))
            counts.append(0)
            continue
        counts.append(pages)
    return counts


# ---------------------------------------------------------------------------
# Steps 6-7 - render and hash
# ---------------------------------------------------------------------------


def _render_leaves(
    leaves: Sequence[_Leaf],
    counts: Sequence[int],
    *,
    settings: Settings,
    documents: DocumentPort,
    storage: StoragePort,
    job_id: str,
    known_hashes: frozenset[str],
    failures: list[PageFailure],
) -> list[PageRaw]:
    """Render every page, store it, and build its PageRaw. A bad page is recorded and
    skipped; it never stops the pages after it."""
    pages: list[PageRaw] = []
    stems_used: dict[str, int] = {}
    for leaf, page_count in zip(leaves, counts, strict=True):
        stem = _unique_stem(leaf.name, stems_used)
        for index in range(page_count):
            page_id = f"{stem}#{index + 1}"
            try:
                if leaf.kind == "pdf":
                    image = documents.render_pdf_page(leaf.data, index, dpi=settings.render_dpi)
                else:
                    image = documents.decode_image(leaf.data)
            except RenderFailed as exc:
                failures.append(_failure(leaf.name, index, exc))
                log.warning(
                    "page_failed", job_id=job_id, page_id=page_id, stage="S0", code=exc.code
                )
                continue
            digest = hashlib.sha256(image.data).hexdigest()
            ref = storage.put_page_image(job_id, page_id, image.data, "color")
            pages.append(
                PageRaw(
                    page_id=page_id,
                    source_file=leaf.name,
                    page_index=index,
                    sha256=digest,
                    image=ref,
                    known_sample=digest in known_hashes,
                )
            )
    return pages


def _unique_stem(display_name: str, used: dict[str, int]) -> str:
    """Filesystem-neutral stem for page ids; a repeated stem gets a ``~N`` suffix ('~' can
    never occur in a sanitised stem, so suffixed ids cannot collide with real ones)."""
    dot = display_name.rfind(".")
    raw = display_name[:dot] if dot > 0 else display_name
    stem = _STEM_UNSAFE.sub("_", raw).strip("._")[:_MAX_STEM_CHARS] or "file"
    seen = used.get(stem, 0)
    used[stem] = seen + 1
    return stem if seen == 0 else f"{stem}~{seen + 1}"


def _failure(source_file: str, page_index: int | None, error: SafeLineError) -> PageFailure:
    """Describe a recoverable failure by its stable code only (no detail text)."""
    return PageFailure(
        source_file=source_file,
        page_index=page_index,
        code=error.code,
        recoverable=error.recoverable,
    )
