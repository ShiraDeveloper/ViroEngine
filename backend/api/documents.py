"""Uploads: `POST /v1/documents` (one file) and `POST /v1/batches` (many files or a ZIP).

An upload is validated for content type and size, registered as a job and queued; the extraction
runs asynchronously and is followed through the job routes. `Idempotency-Key` makes a repeat
return the existing job (`200`, `idempotent_replay: true`) instead of creating a second one.
The bytes are held in memory until the pipeline takes them (never written outside the job's
storage) and the full magic-byte inspection happens in S0.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Final

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
import structlog

from backend.api.deps import (
    get_container,
    get_queue,
    get_store,
    require_access_code,
    require_api_key,
)
from backend.api.schemas import UploadResponse
from backend.container import Container
from backend.core.errors import TooLarge, UnsupportedMedia
from backend.core.pipeline.s0_intake import UploadedBlob
from backend.jobs import JobEntry, JobStore
from backend.queue import JobQueue
from backend.security import limit_uploads

log = structlog.get_logger()

router = APIRouter(tags=["documents"], dependencies=[Depends(require_api_key)])

# Structural: accepted by content type here; the content itself is checked by magic bytes in S0.
ACCEPTED_TYPES: Final = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "application/zip",
        "application/x-zip-compressed",
    }
)
# Structural: bytes read per chunk while enforcing the size limit.
_CHUNK: Final = 1024 * 1024
_RETRY_AFTER: Final = "30"
_BYTES_PER_MB: Final = 1024 * 1024


async def read_limited(upload: UploadFile, remaining: int, limit_mb: int) -> bytes:
    """The upload's bytes, refusing as soon as more than `remaining` have arrived. Raises
    UnsupportedMedia (415) for a content type outside ACCEPTED_TYPES and TooLarge (413)."""
    if upload.content_type not in ACCEPTED_TYPES:
        raise UnsupportedMedia(f"content type {upload.content_type!r} is not supported")
    data = bytearray()
    while chunk := await upload.read(_CHUNK):
        data.extend(chunk)
        if len(data) > remaining:
            raise TooLarge(f"the upload exceeds the {limit_mb} MB limit")
    return bytes(data)


async def _enqueue(entry: JobEntry, store: JobStore, queue: JobQueue) -> None:
    """Queue the job, or forget it and answer 429 + Retry-After when the queue is full."""
    try:
        await queue.enqueue(entry.job_id)
    except asyncio.QueueFull:
        store.discard(entry.job_id)
        log.warning("queue_full", job_id=entry.job_id)
        raise HTTPException(
            status_code=429,
            headers={"Retry-After": _RETRY_AFTER},
            detail={"error": "QUEUE_FULL", "recoverable": True},
        ) from None


def _response(entry: JobEntry, *, replay: bool) -> JSONResponse:
    body = UploadResponse(
        job_id=entry.job_id,
        status=entry.record.status,
        page_count=entry.record.page_count,
        idempotent_replay=replay,
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if replay else 201)


async def _accept(
    uploads: list[UploadFile],
    *,
    idempotency_key: str | None,
    consent: bool,
    container: Container,
    store: JobStore,
    queue: JobQueue,
) -> JSONResponse:
    if idempotency_key:
        existing = store.find_by_key(idempotency_key)
        if existing is not None:
            return _response(existing, replay=True)
    limit_mb = container.settings.max_upload_mb
    remaining = limit_mb * _BYTES_PER_MB
    blobs: list[UploadedBlob] = []
    for upload in uploads:
        data = await read_limited(upload, remaining, limit_mb)
        remaining -= len(data)
        blobs.append(UploadedBlob(filename=upload.filename or "upload", data=data))
    entry = store.create(blobs, idempotency_key=idempotency_key, consent=consent)
    await _enqueue(entry, store, queue)
    log.info("document_accepted", job_id=entry.job_id, files=len(blobs), consent=consent)
    return _response(entry, replay=False)


@router.post(
    "/documents",
    status_code=201,
    response_model=UploadResponse,
    dependencies=[Depends(limit_uploads)],
)
async def create_document(
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    cloud_consent: Annotated[bool, Form()] = False,
    _: None = Depends(require_access_code),
    container: Container = Depends(get_container),
    store: JobStore = Depends(get_store),
    queue: JobQueue = Depends(get_queue),
) -> JSONResponse:
    """One document. 201 with the job, or 200 with `idempotent_replay` for a repeated key.

    Raises UnsupportedMedia (415), TooLarge (413); answers 429 + Retry-After when the queue is
    full. `cloud_consent` is the per-job consent of PLAN §8.5 (default: no)."""
    return await _accept(
        [file],
        idempotency_key=idempotency_key,
        consent=cloud_consent,
        container=container,
        store=store,
        queue=queue,
    )


@router.post(
    "/batches",
    status_code=201,
    response_model=UploadResponse,
    dependencies=[Depends(limit_uploads)],
)
async def create_batch(
    files: Annotated[list[UploadFile], File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    cloud_consent: Annotated[bool, Form()] = False,
    _: None = Depends(require_access_code),
    container: Container = Depends(get_container),
    store: JobStore = Depends(get_store),
    queue: JobQueue = Depends(get_queue),
) -> JSONResponse:
    """Many documents (repeated `files` parts) or a ZIP, as one job. Same statuses as
    `/documents`; the total size is bounded by `MAX_UPLOAD_MB`."""
    return await _accept(
        files,
        idempotency_key=idempotency_key,
        consent=cloud_consent,
        container=container,
        store=store,
        queue=queue,
    )
