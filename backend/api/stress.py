"""Stress Mode: `POST /v1/jobs/{id}/stress` re-runs a job's original pages, degraded, as a new job.

The route and the derived-job plumbing are here; the degradations themselves are the augmenter
(`eval/augment.py`, T25), reached through `app.state.stress_augmenter`: a callable
`(png_bytes, op, seed) -> png_bytes`. Without one the route answers 501, so nothing is faked.
A derived job may send crops to a provider only if the original did (consent, or every page a known
sample): degrading a document does not make it one of the samples.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from backend.api.deps import get_container, get_queue, get_store, require_api_key
from backend.api.documents import _enqueue, _response
from backend.api.errors import BadStress, StressUnavailable
from backend.api.schemas import StressRequest, UploadResponse
from backend.container import Container
from backend.core.pipeline.s0_intake import UploadedBlob
from backend.jobs import JobStore
from backend.queue import JobQueue

router = APIRouter(tags=["stress"], dependencies=[Depends(require_api_key)])

# Structural: the operations Stress Mode knows (PLAN §10.3).
STRESS_OPS: Final = ("nohl", "rot", "blur", "jpeg", "fax")

Augmenter = Callable[[bytes, str, int], bytes]


@router.post("/jobs/{job_id}/stress", status_code=201, response_model=UploadResponse)
async def stress_job(
    job_id: str,
    body: StressRequest,
    request: Request,
    store: JobStore = Depends(get_store),
    container: Container = Depends(get_container),
    queue: JobQueue = Depends(get_queue),
) -> JSONResponse:
    """A new job over the source job's original pages, each degraded by each requested op.
    422 for an unknown op, 501 when no augmenter is installed."""
    unknown = [op for op in body.ops if op not in STRESS_OPS]
    if unknown or not body.ops:
        raise BadStress("ops must be a non-empty subset of the known operations")
    augment: Augmenter | None = getattr(request.app.state, "stress_augmenter", None)
    if augment is None:
        raise StressUnavailable("Stress Mode is not installed")
    source = store.live(job_id)
    uploads: list[UploadedBlob] = []
    seed = container.settings.stress_seed
    for number, ref in enumerate(source.raw_images.values(), start=1):
        original = container.storage.get(ref)
        if not original:
            continue
        for op in body.ops:
            uploads.append(
                UploadedBlob(filename=f"{op}-{number}.png", data=augment(original, op, seed))
            )
    entry = store.create(
        uploads,
        idempotency_key=None,
        consent=source.consent or source.all_known_samples,
        derived_from=source.job_id,
    )
    await _enqueue(entry, store, queue)
    return _response(entry, replay=False)
