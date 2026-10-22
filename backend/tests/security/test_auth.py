"""Access control and the HTTP boundary (PLAN §8 T-5 body cap, T-6 auth, T-7 rate limit, T-8 CORS,
T-9 headers)."""

from __future__ import annotations

import io
from pathlib import Path
import secrets
from typing import Any

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

import backend.app as app_module
from backend.security import STATIC_HEADERS, SlidingWindow
from backend.settings import Settings
from backend.tests.fixtures.api import ACCESS_CODE, API_KEY, HEADERS, running_app
from backend.tests.fixtures.vision import FakeClock

EVIL = "https://evil.example"
GOOD_ORIGIN = "https://app.safeline.example"


def pdf() -> dict[str, Any]:
    return {"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 stub"), "application/pdf")}


# --- T-6 --------------------------------------------------------------------------------------


def test_requires_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with running_app(monkeypatch, tmp_path) as h:
        none = h.client.get("/v1/senders")
        wrong = h.client.get("/v1/senders", headers={"X-API-Key": API_KEY + "x"})
        right = h.client.get("/v1/senders", headers=HEADERS)

    assert (none.status_code, wrong.status_code, right.status_code) == (401, 401, 200)
    assert none.json() == {"error": "UNAUTHORIZED", "recoverable": False}


def test_key_comparison_is_constant_time(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[int] = []
    real = secrets.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr("backend.api.deps.secrets.compare_digest", spy)
    with running_app(monkeypatch, tmp_path) as h:
        h.client.get("/v1/senders", headers={"X-API-Key": "nope"})

    assert calls, "the key was compared with secrets.compare_digest, not =="


def test_requires_access_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with running_app(monkeypatch, tmp_path, APP_ACCESS_CODE=ACCESS_CODE) as h:
        missing = h.client.post("/v1/documents", files=pdf(), headers=HEADERS)
        batch = h.client.post("/v1/batches", files=[("files", pdf()["file"])], headers=HEADERS)
        read = h.client.get("/v1/senders", headers=HEADERS)  # reads need only the key

    assert (missing.status_code, batch.status_code, read.status_code) == (401, 401, 200)


def test_health_needs_no_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with running_app(monkeypatch, tmp_path, APP_ACCESS_CODE=ACCESS_CODE) as h:
        assert h.client.get("/v1/health").status_code == 200


# --- T-7 --------------------------------------------------------------------------------------


def test_rate_limit_blocks_burst(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with running_app(monkeypatch, tmp_path, RATE_LIMIT_PER_MINUTE="5") as h:
        statuses = [h.client.get("/v1/senders", headers=HEADERS).status_code for _ in range(7)]
        blocked = h.client.get("/v1/senders", headers=HEADERS)
        health = h.client.get("/v1/health")

    assert statuses == [200] * 5 + [429] * 2
    assert int(blocked.headers["Retry-After"]) >= 1
    assert health.status_code == 200, "a probe is never rate limited"


def test_uploads_have_a_stricter_budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with running_app(monkeypatch, tmp_path, UPLOAD_RATE_LIMIT_PER_MINUTE="2") as h:
        codes = [
            h.client.post("/v1/documents", files=pdf(), headers=HEADERS).status_code
            for _ in range(4)
        ]
        reads = h.client.get("/v1/senders", headers=HEADERS).status_code

    assert codes == [201, 201, 429, 429]
    assert reads == 200, "reading is unaffected by the upload budget"


def test_window_forgets_old_requests() -> None:
    clock = FakeClock()
    window = SlidingWindow(2, 60.0, clock=clock)

    assert [window.hit("a"), window.hit("a")] == [None, None]
    assert window.hit("a") == pytest.approx(60.0)
    assert window.hit("b") is None, "clients have their own budgets"
    clock.advance(61.0)
    assert window.hit("a") is None


# --- T-8 --------------------------------------------------------------------------------------


def test_cors_rejects_unknown_origin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with running_app(monkeypatch, tmp_path, ALLOWED_ORIGINS=GOOD_ORIGIN) as h:

        def preflight(origin: str) -> Any:
            return h.client.options(
                "/v1/senders",
                headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
            )

        evil = preflight(EVIL)
        good = preflight(GOOD_ORIGIN)
        plain = h.client.get("/v1/senders", headers={**HEADERS, "Origin": EVIL})

    assert "access-control-allow-origin" not in evil.headers
    assert good.headers["access-control-allow-origin"] == GOOD_ORIGIN
    assert "access-control-allow-origin" not in plain.headers


def test_a_wildcard_origin_is_refused_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example, *")

    with pytest.raises(ValidationError, match="not allowed"):
        Settings()
    with pytest.raises(ValidationError):
        app_module.create_app(use_fakes=True)


# --- T-9 --------------------------------------------------------------------------------------


def test_security_headers_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with running_app(monkeypatch, tmp_path) as h:
        for path in ("/v1/health", "/v1/senders", "/v1/jobs/nope"):
            response = h.client.get(path, headers=HEADERS)
            for name, value in STATIC_HEADERS.items():
                assert response.headers.get(name) == value, f"{name} missing on {path}"


def test_hsts_only_over_tls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with running_app(monkeypatch, tmp_path) as h:
        plain = h.client.get("/v1/health")
        forwarded = h.client.get("/v1/health", headers={"X-Forwarded-Proto": "https"})
        secure = TestClient(h.app, base_url="https://testserver").get("/v1/health")

    assert "strict-transport-security" not in plain.headers
    assert "max-age=" in forwarded.headers["strict-transport-security"]
    assert "includeSubDomains" in secure.headers["strict-transport-security"]


def test_refusals_carry_the_headers_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with running_app(monkeypatch, tmp_path, RATE_LIMIT_PER_MINUTE="1") as h:
        h.client.get("/v1/senders", headers=HEADERS)
        limited = h.client.get("/v1/senders", headers=HEADERS)
        unauthorised = h.client.get("/v1/jobs/x")

    assert limited.status_code == 429 and unauthorised.status_code in (401, 429)
    for response in (limited, unauthorised):
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["content-security-policy"] == "default-src 'self'"


def test_api_docs_get_a_csp_that_lets_them_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with running_app(monkeypatch, tmp_path) as h:
        docs = h.client.get("/v1/docs")
        api = h.client.get("/v1/health")

    assert "cdn.jsdelivr.net" in docs.headers["content-security-policy"]
    assert api.headers["content-security-policy"] == "default-src 'self'"


# --- T-5 --------------------------------------------------------------------------------------


def test_body_over_the_cap_is_refused_before_it_is_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with running_app(monkeypatch, tmp_path, MAX_UPLOAD_MB="1") as h:  # cap = 1 + 2 MB
        response = h.client.post(
            "/v1/documents",
            content=b"x" * (4 * 1024 * 1024),
            headers={**HEADERS, "Content-Type": "multipart/form-data; boundary=x"},
        )

    assert response.status_code == 413
    assert response.json() == {"error": "TOO_LARGE", "recoverable": False}
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_chunked_body_cannot_dodge_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def chunks() -> Any:
        for _ in range(8):
            yield b"x" * (512 * 1024)

    with running_app(monkeypatch, tmp_path, MAX_UPLOAD_MB="1") as h:
        response = h.client.post(
            "/v1/documents",
            content=chunks(),
            headers={**HEADERS, "Content-Type": "multipart/form-data; boundary=x"},
        )

    assert response.status_code == 413


def test_a_normal_upload_passes_the_cap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with running_app(monkeypatch, tmp_path, MAX_UPLOAD_MB="1") as h:
        response = h.client.post("/v1/documents", files=pdf(), headers=HEADERS)

    assert response.status_code == 201
