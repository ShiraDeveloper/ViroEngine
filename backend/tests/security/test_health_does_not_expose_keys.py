"""No credential reaches a client (PLAN §8 T-17): not through health, metrics, the OpenAPI document,
a provider failure or the generic error handler."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.tests.fixtures.api import HEADERS, Harness, running_app, seed_job

GEMINI = "AIzaSyTESTKEY0123456789abcdefghijk"
GEMINI_2 = "AIzaSyTESTKEY9876543210zyxwvutsrqp"
OPENROUTER = "sk-or-TESTKEY0123456789abcdef"
GROQ = "gsk_TESTKEY0123456789abcdefghij"
ACCESS = "TESTCODE_0002_abcdef"
SECRETS = (GEMINI, GEMINI_2, OPENROUTER, GROQ, ACCESS)


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    with running_app(
        monkeypatch,
        tmp_path,
        GEMINI_API_KEYS=f"{GEMINI},{GEMINI_2}",
        OPENROUTER_API_KEY=OPENROUTER,
        GROQ_API_KEY=GROQ,
        APP_ACCESS_CODE=ACCESS,
    ) as harness:
        yield harness


def assert_clean(text: str, where: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"a credential leaked through {where}"


def test_health_does_not_expose_keys(h: Harness) -> None:
    response = h.client.get("/v1/health")

    assert response.status_code == 200
    assert_clean(response.text, "/v1/health")
    assert_clean(str(response.headers), "the health headers")


def test_every_read_route_is_free_of_credentials(h: Harness) -> None:
    entry = seed_job(h)
    paths = [
        "/v1/health",
        "/v1/metrics",
        "/v1/metrics/eval",
        "/v1/metrics/learning",
        "/v1/learning/seed",
        "/v1/learning/proposals",
        "/v1/senders",
        "/v1/openapi.json",
        f"/v1/jobs/{entry.job_id}",
        f"/v1/jobs/{entry.job_id}/results",
        f"/v1/jobs/{entry.job_id}/audit",
    ]
    for path in paths:
        response = h.client.get(path, headers=HEADERS)
        assert_clean(response.text, path)
        assert_clean(str(response.headers), f"{path} headers")


def test_the_provider_chain_reports_labels_never_keys(h: Harness) -> None:
    providers = h.client.get("/v1/health").json()["providers"]
    chain = h.container.vision
    assert chain is not None or providers == {}
    if chain is not None:
        health = chain.health()
        assert_clean(health.model_dump_json(), "ProviderHealth")


def test_an_unhandled_error_answers_with_a_correlation_id_and_no_detail(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(f"provider call failed with key {GEMINI} and code {ACCESS}")

    monkeypatch.setattr(type(h.store), "lookup", explode)
    tolerant = TestClient(h.app, raise_server_exceptions=False)

    response = tolerant.get("/v1/jobs/anything", headers=HEADERS)

    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"error", "correlation_id"} and body["error"] == "INTERNAL_ERROR"
    assert_clean(response.text, "the generic error handler")


def test_a_validation_error_does_not_echo_a_credential_header(h: Harness) -> None:
    response = h.client.get(
        "/v1/jobs/x/pages/p/crop",
        params={"box": "not-a-box"},
        headers={**HEADERS, "X-Access-Code": ACCESS},
    )

    assert response.status_code == 422
    assert_clean(response.text, "a 422 body")
