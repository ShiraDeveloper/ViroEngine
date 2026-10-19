"""FastAPI dependency functions — inject Container and JobQueue from app state."""

from __future__ import annotations

from fastapi import Request

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
