"""Safe Line FastAPI application factory.

create_app(use_fakes=True)  — skeleton / tests: fake adapters, no real OCR/barcodes.
create_app(use_fakes=False) — production: real adapters (requires T9, T11 etc. merged).

The module-level `app` uses use_fakes=True for the Task 3 skeleton.
Replace with use_fakes=False once all real adapters are implemented.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import structlog

from backend.container import build_container
from backend.core.errors import SafeLineError
from backend.jobs import JobStore
from backend.logging_config import configure as configure_logging
from backend.queue import JobQueue
from backend.runner import load_known_hashes, make_pipeline
from backend.security import (
    BODY_OVERHEAD_MB,
    BodyLimitMiddleware,
    RateLimits,
    SecurityHeadersMiddleware,
    limit_requests,
)
from backend.settings import Settings

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


def _make_lifespan(settings: Settings, use_fakes: bool) -> Any:
    """Return a lifespan context manager bound to settings and use_fakes."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        configure_logging(
            settings.log_level,
            secrets=[
                settings.api_key,
                settings.app_access_code,
                settings.openrouter_api_key,
                settings.groq_api_key,
                *settings.gemini_keys_list,
            ],
        )

        container = build_container(settings, use_fakes=use_fakes)
        app.state.container = container
        store = JobStore(settings=settings, clock=container.clock, storage=container.storage)
        app.state.job_store = store
        app.state.stress_augmenter = None  # Stress Mode ships with T25 (see api/stress.py)

        app.state.job_queue = JobQueue(
            max_concurrency=settings.max_concurrency,
            pipeline=make_pipeline(store, container, known_hashes=load_known_hashes()),
        )
        app.state.job_queue.start()
        sweeper = asyncio.create_task(
            _sweep_forever(store, settings, container.vision), name="job-sweeper"
        )

        if not settings.api_key:
            log.warning("auth_disabled", reason="API_KEY is empty: every /v1 route is open")
        log.info(
            "app_started",
            local_only=settings.local_only,
            use_fakes=use_fakes,
            max_concurrency=settings.max_concurrency,
        )
        yield

        sweeper.cancel()
        app.state.job_queue.stop()
        store.shutdown()
        log.info("app_stopped")

    return lifespan


async def _sweep_forever(store: JobStore, settings: Settings, vision: Any) -> None:
    """Every `purge_sweep_minutes`, purge the jobs whose own timer was lost (e.g. by a restart) and
    delete the cached provider answers that have expired (they hold values read from crops)."""
    interval = settings.purge_sweep_minutes * 60.0
    while True:
        await asyncio.sleep(interval)
        try:
            store.sweep()
            expire = getattr(vision, "purge_expired", None)
            if callable(expire):
                expire()
        except Exception as exc:  # the sweeper must outlive any one failure
            log.error("sweep_failed", exc_type=type(exc).__name__)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(*, use_fakes: bool = False) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        use_fakes: When True, wire fake adapters (tests, skeleton).
                   When False, wire real adapters (production).
    """
    settings = Settings()

    app = FastAPI(
        title="Safe Line",
        description="Confidence-aware extraction engine for Israeli medical commitment forms",
        version="0.1.0",
        docs_url="/v1/docs",
        redoc_url="/v1/redoc",
        openapi_url="/v1/openapi.json",
        lifespan=_make_lifespan(settings, use_fakes),
        dependencies=[Depends(limit_requests)],
    )

    # --- Request budget (PLAN §8 T-7) ---------------------------------------
    app.state.rate_limits = RateLimits(
        settings.rate_limit_per_minute, settings.upload_rate_limit_per_minute
    )

    # --- Middleware, innermost first (each add wraps the previous one): the body cap refuses an
    # oversized request before it is buffered (T-5); CORS answers from an explicit allow-list and
    # never a wildcard (T-8), so even a 413 carries its CORS headers; the security headers are
    # outermost so every answer, a 413 or a 429 included, carries them (T-9).
    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=(settings.max_upload_mb + BODY_OVERHEAD_MB) * 1024 * 1024,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Access-Code", "Idempotency-Key"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    # --- SafeLineError handler — maps domain errors to HTTP status ---------
    @app.exception_handler(SafeLineError)
    async def _safeline_error_handler(request: Request, exc: SafeLineError) -> JSONResponse:
        log.warning(
            "safeline_error",
            code=exc.code,
            recoverable=exc.recoverable,
            http_status=exc.http_status,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": exc.code, "recoverable": exc.recoverable},
        )

    # --- Generic handler — no detail in body, correlation id in logs --------
    @app.exception_handler(Exception)
    async def _generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = str(uuid.uuid4())
        log.error(
            "unhandled_error",
            correlation_id=correlation_id,
            exc_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={"error": "INTERNAL_ERROR", "correlation_id": correlation_id},
        )

    # --- Routers -----------------------------------------------------------
    from backend.api.documents import router as documents_router
    from backend.api.export import router as export_router
    from backend.api.jobs import router as jobs_router
    from backend.api.learning import router as learning_router
    from backend.api.meta import router as meta_router
    from backend.api.review import router as review_router
    from backend.api.senders import router as senders_router
    from backend.api.stress import router as stress_router

    app.include_router(meta_router, prefix="/v1")
    app.include_router(documents_router, prefix="/v1")
    app.include_router(jobs_router, prefix="/v1")
    app.include_router(review_router, prefix="/v1")
    app.include_router(learning_router, prefix="/v1")
    app.include_router(export_router, prefix="/v1")
    app.include_router(stress_router, prefix="/v1")
    app.include_router(senders_router, prefix="/v1")

    return app


# ---------------------------------------------------------------------------
# Module-level app — skeleton uses fakes; update to use_fakes=False
# once all real adapters are implemented (T9, T11, T13, T18, T19, T24).
# ---------------------------------------------------------------------------
app = create_app(use_fakes=True)
