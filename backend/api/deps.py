"""FastAPI dependencies: the container, the queue, the job registry and the two credentials.

`X-API-Key` guards every `/v1` route except `/health`; uploads also need `X-Access-Code`. A
credential that is not configured is not enforced (development and tests without auth); a
configured one is compared in constant time. A deployment without `API_KEY` is open: the
application logs that once at startup.
"""

from __future__ import annotations

import secrets

from fastapi import Request

from backend.api.errors import Unauthorized
from backend.container import Container
from backend.jobs import JobStore
from backend.queue import JobQueue


def get_container(request: Request) -> Container:
    """Return the Container built during app lifespan."""
    container: Container = request.app.state.container
    return container


def get_queue(request: Request) -> JobQueue:
    """Return the JobQueue started during app lifespan."""
    queue: JobQueue = request.app.state.job_queue
    return queue


def get_store(request: Request) -> JobStore:
    """Return the job registry created during app lifespan."""
    store: JobStore = request.app.state.job_store
    return store


def _matches(expected: str, provided: str) -> bool:
    return secrets.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def require_api_key(request: Request) -> None:
    """Enforce `X-API-Key` when `API_KEY` is configured. Raises Unauthorized (401)."""
    expected = get_container(request).settings.api_key
    if expected and not _matches(expected, request.headers.get("X-API-Key", "")):
        raise Unauthorized("invalid or missing API key")


def require_access_code(request: Request) -> None:
    """Enforce `X-Access-Code` on uploads when `APP_ACCESS_CODE` is configured. Raises
    Unauthorized (401)."""
    expected = get_container(request).settings.app_access_code
    if expected and not _matches(expected, request.headers.get("X-Access-Code", "")):
        raise Unauthorized("invalid or missing access code")
