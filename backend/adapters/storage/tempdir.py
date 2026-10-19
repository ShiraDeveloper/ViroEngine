"""TmpdirStorage — StoragePort backed by a per-job temporary directory (PLAN §3.2, §8).

Layout: ``<data_dir>/jobs/<job_id>/<page-hash>/<kind>-<content-hash>.png``.

* Directories are created mode 0700 and files 0600, so only the service user can read the
  page images that hold patient data.
* File names are derived from content (and page ids from a hash), never from user input, so
  a hostile file name cannot escape the job directory or collide with another page.
* Writing identical bytes twice is a no-op, which also lets S2 keep every variant alongside
  the raw page: an ImageRef stays valid for as long as its job lives.
* ``purge_job`` removes the whole job directory and is idempotent.

Everything is purged after JOB_TTL_MINUTES by the sweeper (T22); a restart loses nothing
that was meant to survive, because nothing is.
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Final, Literal

from PIL import Image
import structlog

from backend.core.models import ImageRef
from backend.settings import Settings

log = structlog.get_logger()

# Job ids are minted by the service (uuid4) and arrive as path components, so only a
# conservative alphabet is accepted. Anything else is a bug or an attack.
_JOB_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Hex characters kept from each hash used in a path: 64 bits make a collision between two
# different images of one page, or two page ids of one job, practically impossible.
_HASH_CHARS: Final = 16

_DIR_MODE: Final = 0o700


class TmpdirStorage:
    """Store page images on local disk, per job, for the lifetime of the job only."""

    def __init__(self, *, settings: Settings) -> None:
        self._root = (Path(settings.data_dir) / "jobs").resolve()

    # ------------------------------------------------------------------ StoragePort

    def put_page_image(
        self,
        job_id: str,
        page_id: str,
        data: bytes,
        kind: Literal["color", "gray_bin", "thumb", "crop"],
    ) -> ImageRef:
        """Write `data` (a PNG) and return its reference.

        ImageRef.path is absolute so adapters can read the file directly; ImageRef never
        leaves the backend (the API exposes crops through its own route).
        Raises ValueError for a job id outside the allowed alphabet.
        """
        job_dir = self._job_dir(job_id)
        digest = hashlib.sha256(data).hexdigest()
        page_dir = job_dir / hashlib.sha256(page_id.encode("utf-8")).hexdigest()[:_HASH_CHARS]
        target = page_dir / f"{kind}-{digest[:_HASH_CHARS]}.png"
        if not target.exists():
            self._write_private(page_dir, target, data)
        width, height = _png_size(data)
        return ImageRef(
            job_id=job_id,
            page_id=page_id,
            kind=kind,
            path=str(target),
            width=width,
            height=height,
            sha256=digest,
        )

    def get(self, ref: ImageRef) -> bytes:
        """Return the stored bytes, or b"" when purged, missing or outside the storage root."""
        try:
            path = Path(ref.path).resolve()
            if not path.is_relative_to(self._root) or not path.is_file():
                return b""
            return path.read_bytes()
        except OSError:
            return b""

    def purge_job(self, job_id: str) -> int:
        """Delete every artefact of `job_id`; returns the number of files removed.

        Safe to call repeatedly and for unknown or malformed ids (returns 0).
        """
        if _JOB_ID.fullmatch(job_id) is None:
            return 0
        job_dir = self._root / job_id
        if job_dir.is_symlink():
            job_dir.unlink()
            return 0
        if not job_dir.is_dir():
            return 0
        before = sum(1 for p in job_dir.rglob("*") if p.is_file())
        try:
            shutil.rmtree(job_dir)
        except OSError as exc:
            log.warning("purge_incomplete", job_id=job_id, error_type=type(exc).__name__)
        after = sum(1 for p in job_dir.rglob("*") if p.is_file()) if job_dir.exists() else 0
        return before - after

    # ------------------------------------------------------------------ internals

    def _job_dir(self, job_id: str) -> Path:
        """The job directory path; the id is validated before it touches a path."""
        if _JOB_ID.fullmatch(job_id) is None:
            raise ValueError("job id contains characters outside [A-Za-z0-9_-]")
        return self._root / job_id

    def _write_private(self, page_dir: Path, target: Path, data: bytes) -> None:
        """Atomically write `data` to `target`, creating private directories as needed.

        Written to a temp file in the same directory and renamed into place, so a reader
        never observes a half-written image.
        """
        page_dir.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        # mkdir applies `mode` to the leaf only and the umask can widen it, so every
        # directory from the storage root down is tightened explicitly.
        for directory in (self._root, page_dir.parent, page_dir):
            os.chmod(directory, _DIR_MODE)
        fd, tmp_name = tempfile.mkstemp(dir=page_dir, prefix=".part-")  # created 0600
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise


def _png_size(data: bytes) -> tuple[int, int]:
    """(width, height) in pixels read from the image header; (0, 0) if unreadable."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            return width, height
    except (OSError, ValueError, SyntaxError):
        return (0, 0)
