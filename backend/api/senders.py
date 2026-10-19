"""Senders endpoint — GET /v1/senders.

Stub for Task 6 (Shira: build_senders.py).
Returns the closed list of known sender institutions from config/senders.json.
Full implementation populates SenderSpec entries per PLAN.md §2.6.

This route is auth-protected so the Docker CI smoke test can verify
that the API key guard is live.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from backend.api.deps import get_container, require_api_key
from backend.container import Container

router = APIRouter(tags=["senders"])

_SENDERS_JSON = Path(__file__).resolve().parents[2] / "config" / "senders.json"


@router.get("/senders", dependencies=[Depends(require_api_key)])
async def list_senders(
    container: Container = Depends(get_container),
) -> list[Any]:
    """Return the closed list of known sender institutions.

    Requires X-API-Key header when API_KEY is configured.
    Returns an empty list until Task 6 populates config/senders.json.
    """
    try:
        data: list[Any] = json.loads(_SENDERS_JSON.read_text(encoding="utf-8"))
        # Filter out _comment/_note stub entries that are not real SenderSpec objects
        return [s for s in data if isinstance(s, dict) and "canonical" in s]
    except Exception:
        return []
