"""A job's artefacts and data are deleted after JOB_TTL_MINUTES (project rule §7, PLAN §4 S11)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.core.models import JobStatus
from backend.tests.fixtures.api import HEADERS, Harness, running_app, seed_job
from backend.tests.fixtures.synthetic.pages import clean_page

TTL = 30  # minutes (the default)
VISIBLE = 10  # minutes a purged job still answers "purged"


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    with running_app(monkeypatch, tmp_path) as harness:
        yield harness


def job_files(h: Harness, job_id: str) -> list[Path]:
    root = Path(h.container.settings.data_dir) / "jobs" / job_id
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def get(h: Harness, job_id: str) -> Any:
    return h.client.get(f"/v1/jobs/{job_id}", headers=HEADERS)


def test_job_artifacts_deleted_after_ttl(h: Harness) -> None:
    entry = seed_job(h, images={"p1": clean_page()}, key="demo")
    assert job_files(h, entry.job_id), "the page image exists while the job lives"

    h.advance(TTL + 1)
    response = get(h, entry.job_id)  # TTL applies on access, with no timer or sweeper involved

    assert response.status_code == 200 and response.json()["status"] == "purged"
    assert job_files(h, entry.job_id) == [], "the job directory is empty after the TTL"
    assert not (Path(h.container.settings.data_dir) / "jobs" / entry.job_id).exists()
    assert response.json()["rows"] == []


def test_a_job_is_kept_until_its_ttl(h: Harness) -> None:
    entry = seed_job(h, images={"p1": clean_page()})

    h.advance(TTL - 1)

    assert get(h, entry.job_id).json()["status"] == "needs_review"
    assert job_files(h, entry.job_id)


def test_sweeper_catches_missed_purge(h: Harness) -> None:
    entry = seed_job(h, images={"p1": clean_page()})
    assert entry.timer is None, "this job's own timer was lost"
    h.advance(TTL + 1)

    purged = h.store.sweep()

    assert purged == 1
    assert job_files(h, entry.job_id) == []
    assert h.store.sweep() == 0, "sweeping again finds nothing new"


def test_purged_job_returns_clear_status_then_404(h: Harness) -> None:
    entry = seed_job(h)
    h.advance(TTL + 1)
    h.store.sweep()

    purged = get(h, entry.job_id)
    h.advance(VISIBLE + 1)
    gone = get(h, entry.job_id)

    assert (purged.status_code, purged.json()["status"]) == (200, "purged")
    assert (gone.status_code, gone.json()["error"]) == (404, "JOB_NOT_FOUND")


def test_a_purged_job_serves_nothing_else(h: Harness) -> None:
    entry = seed_job(h, images={"p1": clean_page()})
    h.store.purge(entry.job_id)
    base = f"/v1/jobs/{entry.job_id}"

    for path in ("/results", "/audit", "/export", "/pages/p1/image"):
        response = h.client.get(base + path, headers=HEADERS)
        assert response.status_code == 404, path
        assert response.json()["error"] == "JOB_PURGED", path


def test_purge_removes_personal_data_from_memory(h: Harness) -> None:
    entry = seed_job(h, images={"p1": clean_page()}, key="k1")
    h.client.patch(
        f"/v1/jobs/{entry.job_id}/rows/row-p1-1/fields/first_name",
        json={"value": "משה"},
        headers=HEADERS,
    )

    h.store.purge(entry.job_id)

    assert entry.audit == [] and entry.undo == {} and entry.events == []
    assert entry.record.rows == [] and entry.record.report is None
    assert entry.page_images == {} and entry.uploads is None
    assert h.store.find_by_key("k1") is None, "the idempotency key is forgotten"


def test_delete_purges_immediately_and_the_job_then_reports_purged(h: Harness) -> None:
    entry = seed_job(h, images={"p1": clean_page()})

    deleted = h.client.delete(f"/v1/jobs/{entry.job_id}", headers=HEADERS)

    assert deleted.status_code == 204
    assert job_files(h, entry.job_id) == []
    assert get(h, entry.job_id).json()["status"] == "purged"
    assert h.client.delete(f"/v1/jobs/{entry.job_id}", headers=HEADERS).status_code == 404


def test_a_running_job_is_not_purged_under_its_feet(h: Harness) -> None:
    entry = seed_job(h, status=JobStatus.running, with_report=False)
    h.advance(TTL + 5)

    assert get(h, entry.job_id).json()["status"] == "running"
    assert h.store.sweep() == 0


def test_the_purge_ends_open_streams(h: Harness) -> None:
    entry = seed_job(h, status=JobStatus.running, with_report=False)
    h.store.purge(entry.job_id)

    assert entry.finished is True


def test_purge_is_idempotent(h: Harness) -> None:
    entry = seed_job(h, images={"p1": clean_page()})

    first = h.store.purge(entry.job_id)
    second = h.store.purge(entry.job_id)

    assert first >= 1 and second == 0
    assert h.store.purge("never-existed") == 0
