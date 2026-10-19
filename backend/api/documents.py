"""Document upload endpoints — POST /v1/documents and POST /v1/batches.

This is a skeleton stub. Full intake (S0-S2) is implemented in Task 9.
The stub validates content type and size, creates a job record in memory,
and enqueues a no-op pipeline call so the queue back-pressure test can work.

In-memory job store here is replaced by the real StoragePort in Task 22.
"""

from __future__ import annotations

import asyncio
from typing import Any
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
import structlog

from backend.api.deps import get_container, get_queue
from backend.container import Container
from backend.core.errors import TooLarge, UnsupportedMedia
from backend.queue import JobQueue

log = structlog.get_logger()

router = APIRouter(tags=["documents"])

# Accepted MIME types — also checked by magic bytes in S0 (T9)
_ACCEPTED_TYPES: frozenset[str] = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "application/zip"}
)

# Skeleton: in-memory job store (T22 replaces with StoragePort-backed persistence)
_jobs: dict[str, dict[str, Any]] = {}


@router.post("/documents", status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    container: Container = Depends(get_container),
    queue: JobQueue = Depends(get_queue),
) -> dict[str, Any]:
    """Accept a single document and create an extraction job.

    Returns job_id and initial status immediately; extraction runs async.

    Raises:
        UnsupportedMedia (415): content type is not supported.
        TooLarge (413): file size exceeds MAX_UPLOAD_MB.
        HTTPException 429: job queue is at capacity (Retry-After: 30).
    """
    # --- Content-type validation (magic bytes check added in T9) ---
    if file.content_type not in _ACCEPTED_TYPES:
        raise UnsupportedMedia(
            f"Content type {file.content_type!r} is not supported. "
            f"Accepted: {sorted(_ACCEPTED_TYPES)}"
        )

    # --- Size validation ---
    data = await file.read()
    max_bytes = container.settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise TooLarge(
            f"File size {len(data)} bytes exceeds the {container.settings.max_upload_mb} MB limit"
        )

    # --- Create job ---
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "filename": file.filename or "upload",
        "size_bytes": len(data),
    }

    # --- Enqueue ---
    try:
        await queue.enqueue(job_id)
    except asyncio.QueueFull:
        del _jobs[job_id]
        log.warning("queue_full", job_id=job_id)
        raise HTTPException(
            status_code=429,
            headers={"Retry-After": "30"},
            detail={"error": "QUEUE_FULL", "recoverable": True},
        ) from None

    log.info("document_accepted", job_id=job_id, filename=file.filename)
    return {"job_id": job_id, "status": "queued"}
