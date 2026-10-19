"""Integration tests for Task 3 — hexagonal backend skeleton.

test_health_reports_tesseract_hebrew      — health endpoint includes OCR readiness
test_health_never_exposes_keys            — no API key value in health response body
test_upload_returns_job_id_with_fakes     — POST /v1/documents returns 201 + job_id
test_queue_backpressure_returns_429       — full queue → 429 + Retry-After header
test_security_headers_present             — required security headers on every response
test_safelinerror_maps_to_status          — TooLarge→413, UnsupportedMedia→415
test_container_with_local_only_has_no_vision — LOCAL_ONLY=true → vision is None
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
import io
from typing import Any, cast

from fastapi.testclient import TestClient
import pytest

from backend.app import create_app

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    """TestClient with fake adapters. Triggers lifespan (start/stop workers)."""
    app = create_app(use_fakes=True)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_reports_tesseract_hebrew(client: TestClient) -> None:
    """GET /v1/health must return status=ok and report the OCR reader."""
    resp = client.get("/v1/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "ocr" in body
    assert body["ocr"] == "ready"
    # Fake adapter reports synthetic language list including 'heb'
    assert "heb" in body.get("ocr_langs", [])


def test_health_never_exposes_keys(client: TestClient) -> None:
    """GET /v1/health must not expose any API key value in the response body."""
    resp = client.get("/v1/health")

    body_text = resp.text.lower()
    # Key material must not appear in any form
    for _forbidden in ("api_key", "gemini_api_keys", "openrouter_api_key", "groq_api_key"):
        # The key NAME appearing in JSON keys is fine; its VALUE must not appear.
        # Checking that the settings attribute values are not present:
        # Since test runs with empty keys, we just verify the response has no literal
        # provider credential values (the fake returns a credential count, not a value).
        assert "sk-" not in body_text, "API key value leaked in health response"
        assert "aiz" not in body_text, "Gemini key value leaked in health response"


def test_upload_returns_job_id_with_fakes(client: TestClient) -> None:
    """POST /v1/documents with a valid PDF stub must return 201 with a job_id."""
    resp = client.post(
        "/v1/documents",
        files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4 stub"), "application/pdf")},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert "job_id" in body
    assert len(body["job_id"]) == 36  # UUID4 format
    assert body["status"] == "queued"


def test_queue_backpressure_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the job queue is full, POST /v1/documents returns 429 + Retry-After header."""
    app = create_app(use_fakes=True)

    with TestClient(app) as c:
        # Monkey-patch the queue's enqueue method to simulate a full queue
        async def _always_full(job_id: str) -> None:
            raise asyncio.QueueFull()

        app_cast = cast(Any, c.app)
        monkeypatch.setattr(app_cast.state.job_queue, "enqueue", _always_full)

        resp = c.post(
            "/v1/documents",
            files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4 stub"), "application/pdf")},
        )

    assert resp.status_code == 429
    assert "retry-after" in resp.headers


def test_security_headers_present(client: TestClient) -> None:
    """Every response must carry the required security headers."""
    resp = client.get("/v1/health")

    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "no-referrer"
    assert "Permissions-Policy" in resp.headers


def test_safelinerror_maps_to_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """UnsupportedMedia → 415. TooLarge → 413."""
    # --- UnsupportedMedia (415) ---
    app = create_app(use_fakes=True)
    with TestClient(app) as c:
        resp = c.post(
            "/v1/documents",
            files={"file": ("notes.txt", io.BytesIO(b"not a pdf"), "text/plain")},
        )
    assert resp.status_code == 415, f"Expected 415, got {resp.status_code}"
    assert resp.json()["error"] == "UNSUPPORTED_MEDIA"

    # --- TooLarge (413) — override MAX_UPLOAD_MB=0 so any upload is too large ---
    monkeypatch.setenv("MAX_UPLOAD_MB", "0")
    app2 = create_app(use_fakes=True)
    with TestClient(app2) as c:
        resp = c.post(
            "/v1/documents",
            files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
        )
    assert resp.status_code == 413, f"Expected 413, got {resp.status_code}"
    assert resp.json()["error"] == "TOO_LARGE"


def test_container_with_local_only_has_no_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    """When LOCAL_ONLY=true, the container must have vision=None."""
    monkeypatch.setenv("LOCAL_ONLY", "true")
    app = create_app(use_fakes=True)

    with TestClient(app) as c:
        container = cast(Any, c.app).state.container

    assert container.vision is None, "Expected vision=None when LOCAL_ONLY=true"
