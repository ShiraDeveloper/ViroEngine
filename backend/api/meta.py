"""Meta endpoints — /v1/health, /v1/metrics.

Health reports per-adapter readiness.
Keys and credential values are NEVER included in the response — only labels and counts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from fastapi import APIRouter, Depends, Request
import structlog

from backend.api.deps import get_container, get_store, require_api_key
from backend.container import Container
from backend.jobs import JobStore

log = structlog.get_logger()

router = APIRouter(tags=["meta"])

# Structural: where `eval/run_eval.py --emit-json` writes the latest report (git-ignored).
EVAL_LATEST: Final = Path(__file__).resolve().parents[2] / "eval" / "results" / "latest.json"


@router.get("/health")
async def health(
    request: Request,
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    """Liveness and adapter readiness check.

    Returns per-adapter status. API keys are never exposed —
    only credential counts and circuit state.
    """
    settings = container.settings

    # OCR readiness — real adapter checks the tesseract binary; fake always ready
    ocr_status = _probe_ocr(container)

    # Vision provider health (None when LOCAL_ONLY)
    providers: dict[str, Any] = {}
    if container.vision is not None:
        ph = container.vision.health()
        providers[ph.name] = {"credentials": ph.credentials, "state": ph.state}

    response: dict[str, Any] = {
        "status": "ok",
        "ocr": ocr_status["status"],
        "ocr_reader": container.ocr_printed.name,
        "ocr_langs": ocr_status.get("langs", []),
        "models": {
            "trocr": "loaded" if container.ocr_digits is not None else "not_loaded",
            "yolo": "not_loaded",  # loaded in T16 when the experiment runs
        },
        "providers": providers,
        "local_only": settings.local_only,
        "queue_size": request.app.state.job_queue.qsize,
    }

    log.info("health_check", status=response["status"], ocr=ocr_status["status"])
    return response


def _probe_ocr(container: Container) -> dict[str, Any]:
    """Check OCR readiness. Returns status and language list.

    Fake adapters are always ready with a synthetic lang list.
    Real Tesseract health probing is implemented in T9 TesseractAdapter
    and surfaced here once that adapter exposes a .health() method.
    """
    name = container.ocr_printed.name
    if name.startswith("fake_"):
        # Fake adapter — report as ready with a synthetic language list
        return {"status": "ready", "langs": ["heb", "eng"]}

    # Real adapter health check delegates to the adapter in T9.
    # For now, report ready without probing to avoid direct pytesseract import
    # from the API layer (only adapters may import tesseract — §8 layering rule).
    return {"status": "ready", "langs": []}


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
