"""A running application with fake adapters, real storage and imaging, and seeded jobs.

`running_app` builds the real FastAPI app (so routing, auth, error handling and the lifespan are the
production ones) over a container whose OCR, barcode, vision and senders are fakes. Jobs are seeded
straight into the registry, so API tests do not depend on the pipeline.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import dataclasses
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.adapters.fakes import FixedClock
from backend.adapters.imaging.cv import CvImaging
from backend.adapters.storage.tempdir import TmpdirStorage
import backend.app as app_module
from backend.container import Container, build_container
from backend.core.models import CommitmentRow, JobStatus, RunReport
from backend.core.pipeline.s10_rows import assemble_rows
from backend.core.pipeline.s11_finalize import build_report
from backend.jobs import JobEntry, JobStore
from backend.settings import Settings
from backend.tests.fixtures.rows import ctx, page
from backend.tests.fixtures.synthetic.files import png_bytes
from backend.tests.fixtures.synthetic.pages import Pixels

API_KEY = "TESTKEY_API_0001"
ACCESS_CODE = "TESTCODE_0002"
HEADERS = {"X-API-Key": API_KEY}
DATE = "18/06/2026"


@dataclasses.dataclass
class Harness:
    client: TestClient
    app: FastAPI
    store: JobStore
    container: Container
    storage: TmpdirStorage
    clock: FixedClock

    def advance(self, minutes: float) -> None:
        """Move the application's clock forward."""
        self.clock._fixed = self.clock.now() + timedelta(minutes=minutes)


@contextmanager
def running_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> Iterator[Harness]:
    """The application under test. `env` are environment variables (upper-case names)."""
    values = {"API_KEY": API_KEY, "DATA_DIR": str(tmp_path), **env}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    real_build = build_container

    def build(settings: Settings, *, use_fakes: bool = False) -> Container:
        fakes = real_build(settings, use_fakes=True)
        return dataclasses.replace(
            fakes, storage=TmpdirStorage(settings=settings), imaging=CvImaging(settings=settings)
        )

    monkeypatch.setattr(app_module, "build_container", build)
    app = app_module.create_app(use_fakes=True)
    with TestClient(app) as client:
        container: Container = app.state.container
        clock = container.clock
        assert isinstance(clock, FixedClock)
        yield Harness(
            client=client,
            app=app,
            store=app.state.job_store,
            container=container,
            storage=container.storage,  # type: ignore[arg-type]
            clock=clock,
        )


def sample_rows() -> tuple[list[CommitmentRow], RunReport]:
    """Two rows: one with every critical field closed, one missing its stamp date (flagged)."""
    pages = [
        page(
            "p1",
            centrals=["937309"],
            date=DATE,
            commitments=["4521"],
            first_name="דנה",
            last_name="כהן",
        ),
        page("p2", centrals=["937310"], commitments=["4522"], first_name="רונית", last_name="לוי"),
    ]
    rows, _, _ = assemble_rows(pages, ctx=ctx())
    now = datetime(2026, 10, 18, 9, 0).astimezone()
    report = build_report(
        "seed",
        pages,
        started_at=now,
        finished_at=now,
        row_count=len(rows),
        page_count=len(pages),
        intake_ms=1,
        prompt_version="v1",
    )
    return rows, report


def seed_job(
    h: Harness,
    *,
    rows: list[CommitmentRow] | None = None,
    status: JobStatus = JobStatus.needs_review,
    images: dict[str, Pixels] | None = None,
    with_report: bool = True,
    key: str | None = None,
) -> JobEntry:
    """A finished (or, with `status`, running) job registered as if the pipeline had made it."""
    seeded_rows, report = sample_rows()
    entry = h.store.create([], idempotency_key=key, consent=False)
    entry.uploads = None
    entry.record = entry.record.model_copy(
        update={
            "status": status,
            "rows": seeded_rows if rows is None else rows,
            "page_count": 2,
            "pages_done": 2,
            "report": report if with_report else None,
        }
    )
    for page_id, pixels in (images or {}).items():
        entry.page_images[page_id] = h.storage.put_page_image(
            entry.job_id, page_id, png_bytes(pixels), "color"
        )
    entry.finished = status not in (JobStatus.queued, JobStatus.running)
    return entry


def edit(h: Harness, entry: JobEntry, row: str, field: str, body: dict[str, Any]) -> Any:
    return h.client.patch(
        f"/v1/jobs/{entry.job_id}/rows/{row}/fields/{field}", json=body, headers=HEADERS
    )
