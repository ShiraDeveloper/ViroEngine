"""FastAPI dependency functions — inject Container and JobQueue from app state."""

from __future__ import annotations

from fastapi import HTTPException, Request

from backend.container import Container
from backend.queue import JobQueue


def get_container(request: Request) -> Container:
    """Return the Container built during app lifespan."""
    container: Container = request.app.state.container
    return container


def get_queue(request: Request) -> JobQueue:
    """Return the JobQueue started during app lifespan."""
    queue: JobQueue = request.app.state.job_queue
    return queue


def require_api_key(request: Request) -> None:
    """Enforce X-API-Key header when API_KEY is configured.

    Returns immediately when api_key is empty (dev / test without auth).
    Raises HTTP 401 when a key is configured and the header is missing or wrong.
    """
    container: Container = request.app.state.container
    expected = container.settings.api_key
    if not expected:
        return  # auth not configured — allow through
    provided = request.headers.get("X-API-Key", "")
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
