"""Export: `GET /v1/jobs/{id}/export?format=xlsx|csv|json`, rendered on demand and never stored.

This route only resolves the format and streams the bytes; the exporters live behind
`ExporterPort` (T19). The rendered bytes hold patient data, so the response is not cacheable.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response

from backend.api.deps import get_container, get_store, require_api_key
from backend.api.errors import JobNotReady
from backend.container import Container
from backend.jobs import JobStore

router = APIRouter(tags=["export"], dependencies=[Depends(require_api_key)])


@router.get("/jobs/{job_id}/export")
async def export_job(
    job_id: str,
    format: Literal["xlsx", "csv", "json"] = "xlsx",
    store: JobStore = Depends(get_store),
    container: Container = Depends(get_container),
) -> Response:
    """The rows (and the run counters) as a file. 409 until the job has a report."""
    entry = store.live(job_id)
    report = entry.record.report
    if report is None:
        raise JobNotReady("the job has not finished")
    exporter = container.exporters[format]
    body = exporter.render(report, list(entry.record.rows))
    return Response(
        body,
        media_type=exporter.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="safeline-{job_id[:8]}.{exporter.fmt}"',
            "Cache-Control": "no-store",
        },
    )
