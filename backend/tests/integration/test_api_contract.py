"""The HTTP contract of the API (PLAN §3.4, T22): routes, auth, uploads, crops, SSE, back-pressure."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import threading
import time
from typing import Any

import httpx
from PIL import Image
import pytest
import uvicorn

from backend.api.schemas import SseDoneData, SseDoneEvent, SsePageData, SsePageEvent
import backend.app as app_module
from backend.core.models import JobStatus
from backend.core.pipeline.orchestrator import SseEvent
from backend.tests.fixtures.api import (
    ACCESS_CODE,
    API_KEY,
    HEADERS,
    Harness,
    running_app,
    seed_job,
)
from backend.tests.fixtures.synthetic.pages import clean_page

PDF = ("test.pdf", io.BytesIO(b"%PDF-1.4 stub"), "application/pdf")
OPENAPI = Path(__file__).resolve().parents[3] / "docs" / "openapi.json"

# The routes PLAN §3.4 lists that exist at this task (learning routes belong to T24).
DOCUMENTED = {
    ("post", "/v1/documents"),
    ("post", "/v1/batches"),
    ("get", "/v1/jobs/{job_id}"),
    ("get", "/v1/jobs/{job_id}/events"),
    ("get", "/v1/jobs/{job_id}/results"),
    ("get", "/v1/jobs/{job_id}/pages/{page_id}/image"),
    ("get", "/v1/jobs/{job_id}/pages/{page_id}/crop"),
    ("patch", "/v1/jobs/{job_id}/rows/{row_id}/fields/{field}"),
    ("post", "/v1/jobs/{job_id}/rows/{row_id}/undo"),
    ("post", "/v1/jobs/{job_id}/confirm"),
    ("get", "/v1/jobs/{job_id}/audit"),
    ("get", "/v1/jobs/{job_id}/export"),
    ("delete", "/v1/jobs/{job_id}"),
    ("post", "/v1/jobs/{job_id}/stress"),
    ("get", "/v1/senders"),
    ("get", "/v1/metrics"),
    ("get", "/v1/metrics/eval"),
    ("get", "/v1/metrics/learning"),
    ("get", "/v1/learning/seed"),
    ("get", "/v1/health"),
}


def pdf_file(size: int | None = None) -> dict[str, Any]:
    data = b"%PDF-1.4 stub" if size is None else b"%PDF-1.4 " + b"x" * size
    return {"file": ("test.pdf", io.BytesIO(data), "application/pdf")}


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    with running_app(monkeypatch, tmp_path) as harness:
        yield harness


def test_all_routes_match_openapi(h: Harness) -> None:
    schema = h.app.openapi()
    served = {(m.lower(), p) for p, item in schema["paths"].items() for m in item}

    assert DOCUMENTED <= served, f"routes missing from the API: {sorted(DOCUMENTED - served)}"
    committed = json.loads(OPENAPI.read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(schema)), "docs/openapi.json is stale"


def test_every_route_but_health_requires_the_api_key(h: Harness) -> None:
    unauthenticated = {
        ("get", "/v1/health"),
        ("get", "/v1/docs"),
    }
    for method, path in sorted(DOCUMENTED - unauthenticated):
        url = path.replace("{job_id}", "x").replace("{page_id}", "p").replace("{row_id}", "r")
        url = url.replace("{field}", "first_name")
        response = h.client.request(method, url)
        assert response.status_code in (401, 422), (
            f"{method} {path} answered {response.status_code}"
        )
        if response.status_code == 401:
            assert response.json()["error"] == "UNAUTHORIZED"


def test_missing_api_key_is_401(h: Harness) -> None:
    assert h.client.get("/v1/jobs/x").status_code == 401
    assert h.client.get("/v1/jobs/x", headers={"X-API-Key": "wrong"}).status_code == 401
    assert h.client.get("/v1/health").status_code == 200


def test_missing_access_code_on_upload_is_401(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with running_app(monkeypatch, tmp_path, APP_ACCESS_CODE=ACCESS_CODE) as h:
        refused = h.client.post("/v1/documents", files=pdf_file(), headers=HEADERS)
        wrong = h.client.post(
            "/v1/documents", files=pdf_file(), headers={**HEADERS, "X-Access-Code": "nope"}
        )
        accepted = h.client.post(
            "/v1/documents", files=pdf_file(), headers={**HEADERS, "X-Access-Code": ACCESS_CODE}
        )

    assert (refused.status_code, wrong.status_code, accepted.status_code) == (401, 401, 201)


def test_idempotency_replay_returns_same_job(h: Harness) -> None:
    headers = {**HEADERS, "Idempotency-Key": "demo-1"}

    first = h.client.post("/v1/documents", files=pdf_file(), headers=headers)
    second = h.client.post("/v1/documents", files=pdf_file(), headers=headers)
    other = h.client.post(
        "/v1/documents", files=pdf_file(), headers={**HEADERS, "Idempotency-Key": "demo-2"}
    )

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["job_id"] == second.json()["job_id"]
    assert first.json()["idempotent_replay"] is False and second.json()["idempotent_replay"] is True
    assert other.json()["job_id"] != first.json()["job_id"]
    assert h.store.metrics.jobs_total == 2, "the same file twice with one key creates one job"


def test_oversized_upload_is_413(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with running_app(monkeypatch, tmp_path, MAX_UPLOAD_MB="1") as h:
        response = h.client.post("/v1/documents", files=pdf_file(2 * 1024 * 1024), headers=HEADERS)

    assert response.status_code == 413
    assert response.json()["error"] == "TOO_LARGE"


def test_wrong_content_type_is_415(h: Harness) -> None:
    response = h.client.post(
        "/v1/documents",
        files={"file": ("notes.txt", io.BytesIO(b"not a pdf"), "text/plain")},
        headers=HEADERS,
    )

    assert response.status_code == 415
    assert response.json()["error"] == "UNSUPPORTED_MEDIA"


def test_batch_upload_creates_one_job_for_many_files(h: Harness) -> None:
    files = [
        ("files", ("a.pdf", io.BytesIO(b"%PDF-1.4 a"), "application/pdf")),
        ("files", ("b.pdf", io.BytesIO(b"%PDF-1.4 b"), "application/pdf")),
    ]

    response = h.client.post("/v1/batches", files=files, headers=HEADERS)

    assert response.status_code == 201
    assert h.store.metrics.jobs_total == 1


def test_queue_backpressure_429(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    async def full(job_id: str) -> None:
        raise asyncio.QueueFull

    monkeypatch.setattr(h.app.state.job_queue, "enqueue", full)

    response = h.client.post(
        "/v1/documents", files=pdf_file(), headers={**HEADERS, "Idempotency-Key": "k"}
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert h.store.find_by_key("k") is None, "a refused job leaves nothing behind"


def test_full_flow_upload_poll_edit_confirm_export(h: Harness) -> None:
    created = h.client.post("/v1/documents", files=pdf_file(), headers=HEADERS)
    job_id = created.json()["job_id"]
    deadline = time.monotonic() + 15
    status = created.json()["status"]
    while status in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.05)
        status = h.client.get(f"/v1/jobs/{job_id}", headers=HEADERS).json()["status"]

    assert status not in ("queued", "running"), "the pipeline never finished"
    events = h.client.get(f"/v1/jobs/{job_id}/events", headers=HEADERS)
    assert events.status_code == 200
    assert "event: done" in events.text


# --- crop and image ---------------------------------------------------------------------------


@pytest.fixture
def with_page(h: Harness) -> Any:
    entry = seed_job(h, images={"p1": clean_page(width=3000, height=4000)})
    return h, entry


@pytest.mark.parametrize(
    "box",
    [
        "abc",
        "0.1,0.2,0.3",
        "0.1,0.2,0.3,0.4,0.5",
        "2,2,3,3",
        "-0.1,0,0.5,0.5",
        "0.5,0.5,0.1,0.1",
        "",
    ],
)
def test_crop_box_pattern_rejected(with_page: Any, box: str) -> None:
    h, entry = with_page

    response = h.client.get(
        f"/v1/jobs/{entry.job_id}/pages/p1/crop", params={"box": box}, headers=HEADERS
    )

    assert response.status_code == 422


@pytest.mark.parametrize("box", ["0.1,0.1,0.3,0.4", "0,0,1,1", ".25,.25,.75,.75", "0,0,1.0,1.0"])
def test_crop_accepts_valid_boxes(with_page: Any, box: str) -> None:
    h, entry = with_page

    response = h.client.get(
        f"/v1/jobs/{entry.job_id}/pages/p1/crop", params={"box": box}, headers=HEADERS
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"


def test_crop_output_capped_at_1200px(with_page: Any) -> None:
    h, entry = with_page

    response = h.client.get(
        f"/v1/jobs/{entry.job_id}/pages/p1/crop",
        params={"box": "0,0,1,1", "pad": 0},
        headers=HEADERS,
    )

    width, height = Image.open(io.BytesIO(response.content)).size
    assert max(width, height) <= 1200
    assert max(width, height) > 600, "capped, not shrunk to nothing"


def test_crop_pad_out_of_range_rejected(with_page: Any) -> None:
    h, entry = with_page

    response = h.client.get(
        f"/v1/jobs/{entry.job_id}/pages/p1/crop",
        params={"box": "0.1,0.1,0.3,0.3", "pad": 0.9},
        headers=HEADERS,
    )

    assert response.status_code == 422


def test_unknown_page_is_404(with_page: Any) -> None:
    h, entry = with_page

    response = h.client.get(
        f"/v1/jobs/{entry.job_id}/pages/zzz/crop", params={"box": "0,0,1,1"}, headers=HEADERS
    )

    assert response.status_code == 404
    assert response.json()["error"] == "PAGE_NOT_FOUND"


def test_thumbnail_is_small_and_color_is_full_size(with_page: Any) -> None:
    h, entry = with_page

    thumb = h.client.get(
        f"/v1/jobs/{entry.job_id}/pages/p1/image", params={"kind": "thumb"}, headers=HEADERS
    )
    color = h.client.get(f"/v1/jobs/{entry.job_id}/pages/p1/image", headers=HEADERS)

    assert max(Image.open(io.BytesIO(thumb.content)).size) <= h.container.settings.thumb_max_side
    assert Image.open(io.BytesIO(color.content)).size == (3000, 4000)


# --- results, export, errors ------------------------------------------------------------------


def test_results_are_the_rows_only(h: Harness) -> None:
    entry = seed_job(h)

    response = h.client.get(f"/v1/jobs/{entry.job_id}/results", headers=HEADERS)

    assert response.status_code == 200
    assert [r["row_id"] for r in response.json()] == [r.row_id for r in entry.record.rows]


def test_export_renders_on_demand_and_is_not_cacheable(h: Harness) -> None:
    entry = seed_job(h)

    response = h.client.get(
        f"/v1/jobs/{entry.job_id}/export", params={"format": "csv"}, headers=HEADERS
    )

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"


def test_export_of_an_unfinished_job_is_409(h: Harness) -> None:
    entry = seed_job(h, status=JobStatus.running, with_report=False)

    response = h.client.get(f"/v1/jobs/{entry.job_id}/export", headers=HEADERS)

    assert (response.status_code, response.json()["error"]) == (409, "JOB_NOT_READY")


def test_unknown_export_format_is_422(h: Harness) -> None:
    entry = seed_job(h)

    assert (
        h.client.get(
            f"/v1/jobs/{entry.job_id}/export", params={"format": "pdf"}, headers=HEADERS
        ).status_code
        == 422
    )


def test_unknown_job_is_404(h: Harness) -> None:
    response = h.client.get("/v1/jobs/does-not-exist", headers=HEADERS)

    assert (response.status_code, response.json()["error"]) == (404, "JOB_NOT_FOUND")


def test_metrics_count_jobs_without_values(h: Harness) -> None:
    seed_job(h)

    body = h.client.get("/v1/metrics", headers=HEADERS).json()

    assert body["jobs_total"] == 1
    assert "937309" not in json.dumps(body)


def test_eval_metrics_report_unavailable_without_a_run(h: Harness) -> None:
    body = h.client.get("/v1/metrics/eval", headers=HEADERS).json()

    assert "available" in body


def test_stress_without_an_augmenter_is_501(h: Harness) -> None:
    entry = seed_job(h)

    response = h.client.post(
        f"/v1/jobs/{entry.job_id}/stress", json={"ops": ["blur"]}, headers=HEADERS
    )

    assert (response.status_code, response.json()["error"]) == (501, "STRESS_UNAVAILABLE")


def test_stress_rejects_unknown_ops(h: Harness) -> None:
    entry = seed_job(h)
    h.app.state.stress_augmenter = lambda data, op, seed: data

    response = h.client.post(
        f"/v1/jobs/{entry.job_id}/stress", json={"ops": ["explode"]}, headers=HEADERS
    )

    assert (response.status_code, response.json()["error"]) == (422, "BAD_STRESS_OP")


def test_stress_creates_a_derived_job_that_inherits_the_consent_gate(h: Harness) -> None:
    entry = seed_job(h, images={"p1": clean_page()})
    entry.raw_images["p1"] = entry.page_images["p1"]
    h.app.state.stress_augmenter = lambda data, op, seed: data

    response = h.client.post(
        f"/v1/jobs/{entry.job_id}/stress", json={"ops": ["blur", "fax"]}, headers=HEADERS
    )

    assert response.status_code == 201
    derived = h.store.lookup(response.json()["job_id"])
    assert derived.derived_from == entry.job_id
    assert derived.consent is False, "degrading a document does not make it a known sample"


# --- SSE --------------------------------------------------------------------------------------


def seeded_events() -> list[SseEvent]:
    page = SsePageData(page_id="p1", label=None, stage="S1", status="running", pct=5)
    done = SseDoneData(status=JobStatus.needs_review, flagged=1, duration_ms=10)
    return [SsePageEvent(event="page", data=page), SseDoneEvent(event="done", data=done)]


def test_sse_closes_on_done(h: Harness) -> None:
    entry = seed_job(h)
    for event in seeded_events():
        entry.publish(event)

    response = h.client.get(f"/v1/jobs/{entry.job_id}/events", headers=HEADERS)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.index("event: page") < response.text.index("event: done")


def test_late_subscriber_replays_every_event(h: Harness) -> None:
    entry = seed_job(h)
    for event in seeded_events():
        entry.publish(event)

    first = h.client.get(f"/v1/jobs/{entry.job_id}/events", headers=HEADERS).text
    second = h.client.get(f"/v1/jobs/{entry.job_id}/events", headers=HEADERS).text

    assert first == second


@pytest.fixture
def live_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """The real application served by uvicorn on a free port, for streaming tests."""
    import socket

    from backend.tests.fixtures.api import API_KEY as KEY

    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SSE_PING_SECONDS", "1")
    app = app_module.create_app(use_fakes=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the test server did not start"
    yield app, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def test_sse_emits_ping_comments(live_server: Any) -> None:
    app, base = live_server
    store = app.state.job_store
    entry = store.create([], idempotency_key=None, consent=False)
    entry.record = entry.record.model_copy(update={"status": JobStatus.running})

    lines: list[str] = []
    with httpx.stream(
        "GET", f"{base}/v1/jobs/{entry.job_id}/events", headers={"X-API-Key": API_KEY}, timeout=10
    ) as response:
        for line in response.iter_lines():
            lines.append(line)
            if line.startswith(": ping"):
                break

    assert any(line.startswith(": ping") for line in lines), "no ping comment within 10 s"
