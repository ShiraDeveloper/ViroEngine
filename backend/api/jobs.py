"""Reading a job: its record, progress stream, rows, page images and crops, and its deletion.

Images are served from the job's own storage and never leave it by any other route. The crop
route renders a region on demand from the corrected page and caps its long edge, so it cannot be
used to pull a full-resolution page out (PLAN §3.4).
"""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sse_starlette.sse import EventSourceResponse

from backend.api.deps import get_container, get_store, require_api_key
from backend.api.errors import BadBox, PageNotFound
from backend.api.schemas import JobView
from backend.container import Container
from backend.core.models import Box, CommitmentRow, ImageRef
from backend.jobs import JobEntry, JobStore, event_stream

router = APIRouter(tags=["jobs"], dependencies=[Depends(require_api_key)])

# Structural: one coordinate of a box: 0, 1, 1.0, .35, 0.35. Anything else is refused at the route.
_COORD: Final = r"(?:0(?:\.\d+)?|1(?:\.0+)?|\.\d+)"
BOX_PATTERN: Final = rf"^{_COORD},{_COORD},{_COORD},{_COORD}$"
# Structural: the longest crop edge served, in pixels (PLAN §3.4 step 4).
CROP_MAX_SIDE: Final = 1200
_BOX_PARTS: Final = 4
_NO_STORE: Final = {"Cache-Control": "no-store"}


def parse_box(text: str, pad: float) -> Box:
    """Normalized (y0, x0, y1, x1) from "y0,x0,y1,x1", grown by `pad` on every side and clamped to
    the page. Raises BadBox (422) for four numbers that do not describe a non-empty region."""
    if re.fullmatch(BOX_PATTERN, text) is None:
        raise BadBox("the box must be four numbers between 0 and 1")
    y0, x0, y1, x1 = (float(p) for p in text.split(","))
    if y1 <= y0 or x1 <= x0:
        raise BadBox("the box is empty")
    return (max(0.0, y0 - pad), max(0.0, x0 - pad), min(1.0, y1 + pad), min(1.0, x1 + pad))


def _image_ref(entry: JobEntry, page_id: str) -> ImageRef:
    ref = entry.page_images.get(page_id) or entry.raw_images.get(page_id)
    if ref is None:
        raise PageNotFound("unknown page")
    return ref


@router.get("/jobs/{job_id}", response_model=JobView)
async def get_job(job_id: str, store: JobStore = Depends(get_store)) -> JobView:
    """The job as the UI sees it. A purged job answers with status `purged` for a while, then 404."""
    return store.lookup(job_id).view()


@router.get("/jobs/{job_id}/results", response_model=list[CommitmentRow])
async def get_results(job_id: str, store: JobStore = Depends(get_store)) -> list[CommitmentRow]:
    """The rows only: the integration payload."""
    return store.live(job_id).record.rows


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    store: JobStore = Depends(get_store),
    container: Container = Depends(get_container),
) -> EventSourceResponse:
    """Server-sent progress: every event so far, then live ones, ending after `done`, on
    disconnect or after the configured ceiling. A `: ping` comment keeps proxies from closing it."""
    entry = store.live(job_id)
    settings = container.settings
    stream = event_stream(
        entry,
        wait_seconds=float(settings.sse_ping_seconds),
        max_seconds=float(settings.sse_max_seconds),
    )
    return EventSourceResponse(stream, ping=settings.sse_ping_seconds)


@router.get("/jobs/{job_id}/pages/{page_id}/image")
async def get_image(
    job_id: str,
    page_id: str,
    kind: Literal["color", "thumb"] = "color",
    store: JobStore = Depends(get_store),
    container: Container = Depends(get_container),
) -> Response:
    """The corrected page (`color`) or its thumbnail, as PNG. Never cached."""
    ref = _image_ref(store.live(job_id), page_id)
    data = container.storage.get(ref)
    if not data:
        raise PageNotFound("the page image is gone")
    if kind == "thumb":
        data = container.imaging.thumbnail(data, max_side=container.settings.thumb_max_side).data
    return Response(data, media_type="image/png", headers=_NO_STORE)


@router.get("/jobs/{job_id}/pages/{page_id}/crop")
async def get_crop(
    job_id: str,
    page_id: str,
    box: Annotated[str, Query(pattern=BOX_PATTERN)],
    pad: Annotated[float, Query(ge=0.0, le=0.2)] = 0.02,
    store: JobStore = Depends(get_store),
    container: Container = Depends(get_container),
) -> Response:
    """PNG of the region `box` (`y0,x0,y1,x1`, fractions of the page) plus `pad`, rendered on
    demand from the corrected page. The long edge is capped at 1200 px."""
    region = parse_box(box, pad)
    ref = _image_ref(store.live(job_id), page_id)
    data = container.storage.get(ref)
    if not data:
        raise PageNotFound("the page image is gone")
    piece = container.imaging.crop(data, region)
    capped = container.imaging.thumbnail(piece.data, max_side=CROP_MAX_SIDE)
    return Response(capped.data, media_type="image/png", headers=_NO_STORE)


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str, store: JobStore = Depends(get_store)) -> Response:
    """Purge the job's artefacts and data now. The job then answers `purged` for a while."""
    store.live(job_id)
    store.purge(job_id)
    return Response(status_code=204)
