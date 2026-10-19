"""StoragePort — contract for page-image and job-artefact storage.

All artefacts are purged after JOB_TTL_MINUTES (default 30 min).
Images are stored locally — never at a remote URL — to satisfy the
privacy requirement that only crops leave the machine.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from backend.core.models import ImageRef


@runtime_checkable
class StoragePort(Protocol):
    """Store, retrieve and purge page images."""

    def put_page_image(
        self,
        job_id: str,
        page_id: str,
        data: bytes,
        kind: Literal["color", "gray_bin", "thumb", "crop"],
    ) -> ImageRef:
        """Persist image bytes and return a reference.

        Returns an ImageRef with the canonical path and sha256.
        Never raises on valid input.
        """
        ...

    def get(self, ref: ImageRef) -> bytes:
        """Retrieve image bytes by reference.

        Returns empty bytes when the artefact has been purged or is missing.
        """
        ...

    def purge_job(self, job_id: str) -> int:
        """Delete all artefacts for job_id. Returns the number of files removed.

        Called by the TTL background task and by DELETE /v1/jobs/{id}.
        Safe to call more than once — idempotent.
        """
        ...
