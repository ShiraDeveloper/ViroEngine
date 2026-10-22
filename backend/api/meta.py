"""Meta endpoints — /v1/health, /v1/metrics.

Health reports per-adapter readiness.
Keys and credential values are NEVER included in the response — only labels and counts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
import structlog

from backend.api.deps import get_container, get_store, require_api_key
from backend.container import Container
from backend.jobs import JobStore
from backend.readiness import ocr_readiness

log = structlog.get_logger()

router = APIRouter(tags=["meta"])

# Structural: where `eval/run_eval.py --emit-json` writes the latest report (git-ignored).
EVAL_LATEST: Final = Path(__file__).resolve().parents[2] / "eval" / "results" / "latest.json"


@router.get("/health")
async def health(
    request: Request,
    container: Container = Depends(get_container),
) -> JSONResponse:
    """Liveness and readiness. 200 with `status: ok` when the engine can read a form, 503 with
    `status: unready` when it cannot (a missing Hebrew language pack, an unreachable Tesseract).
    API keys are never exposed, only credential counts and circuit state."""
    settings = container.settings
    ocr = ocr_readiness(container)

    providers: dict[str, Any] = {}
    if container.vision is not None:
        ph = container.vision.health()
        providers[ph.name] = {"credentials": ph.credentials, "state": ph.state}

    response: dict[str, Any] = {
        "status": "ok" if ocr.ready else "unready",
        "ocr": "ready" if ocr.ready else "unavailable",
        "ocr_reader": container.ocr_printed.name,
        "ocr_langs": ocr.installed,
        "ocr_missing": ocr.missing,
        "models": {
            "trocr": "loaded" if container.ocr_digits is not None else "not_loaded",
            "yolo": "not_loaded",  # loaded in T16 when the experiment runs
        },
        "providers": providers,
        "local_only": settings.local_only,
        "queue_size": request.app.state.job_queue.qsize,
    }

    log.info("health_check", status=response["status"], missing=len(ocr.missing))
    return JSONResponse(response, status_code=200 if ocr.ready else 503)


@router.get("/metrics", dependencies=[Depends(require_api_key)])
async def metrics(
    container: Container = Depends(get_container),
    store: JobStore = Depends(get_store),
) -> dict[str, Any]:
    """Process-level aggregated counters since start-up. Counters only: no value, no PII."""
    m = store.metrics
    return {
        "jobs_total": m.jobs_total,
        "jobs_active": store.active_count(),
        "fields_total": m.fields_total,
        "fields_closed_local": m.fields_closed_local,
        "fields_escalated": m.fields_escalated,
        "fields_flagged": m.fields_flagged,
        "cloud_calls": m.cloud_calls,
        "local_only": container.settings.local_only,
    }


@router.get("/metrics/eval", dependencies=[Depends(require_api_key)])
async def metrics_eval() -> dict[str, Any]:
    """The latest evaluation report (`eval/results/latest.json`), or `{"available": false}` when
    no run has produced one. The report holds counts and rates, never a field value."""
    try:
        data = json.loads(EVAL_LATEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False}
    return {"available": True, "report": data}
