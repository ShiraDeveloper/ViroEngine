"""Safe Line FastAPI application factory.

create_app(use_fakes=True)  — skeleton / tests: fake adapters, no real OCR/barcodes.
create_app(use_fakes=False) — production: real adapters (requires T9, T11 etc. merged).

The module-level `app` uses use_fakes=True for the Task 3 skeleton.
Replace with use_fakes=False once all real adapters are implemented.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import structlog

from backend.container import build_container
from backend.core.errors import SafeLineError
from backend.logging_config import configure as configure_logging
from backend.queue import JobQueue
from backend.settings import Settings

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


def _make_lifespan(settings: Settings, use_fakes: bool) -> Any:
    """Return a lifespan context manager bound to settings and use_fakes."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        configure_logging(settings.log_level)

        app.state.container = build_container(settings, use_fakes=use_fakes)

        async def _noop_pipeline(job_id: str) -> None:
            """Skeleton pipeline — replaced by the real orchestrator in T15."""
            log.info("pipeline_noop", job_id=job_id)

        app.state.job_queue = JobQueue(
            max_concurrency=settings.max_concurrency,
            pipeline=_noop_pipeline,
        )
        app.state.job_queue.start()

        log.info(
            "app_started",
            local_only=settings.local_only,
            use_fakes=use_fakes,
            max_concurrency=settings.max_concurrency,
        )
        yield

        app.state.job_queue.stop()
        log.info("app_stopped")

    return lifespan


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
    )

    # --- CORS — never a wildcard (project rule §7) -------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Access-Code", "Idempotency-Key"],
    )

    # --- Rate limiter -------------------------------------------------------
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(
        RateLimitExceeded,
        cast(Any, _rate_limit_exceeded_handler),
    )

    # --- Security headers middleware ----------------------------------------
    @app.middleware("http")
    async def _security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response

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
    from backend.api.meta import router as meta_router
    from backend.api.senders import router as senders_router

    app.include_router(meta_router, prefix="/v1")
    app.include_router(documents_router, prefix="/v1")
    app.include_router(senders_router, prefix="/v1")

    return app


# ---------------------------------------------------------------------------
# Module-level app — skeleton uses fakes; update to use_fakes=False
# once all real adapters are implemented (T9, T11, T13, T18, T19, T24).
# ---------------------------------------------------------------------------
app = create_app(use_fakes=True)
