"""run_job assembles rows with S10 and finalizes the job from them (PLAN §13 T20)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.adapters.imaging.cv import CvImaging
from backend.adapters.imaging.pdf import DocumentReader
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.api.schemas import SseDoneEvent, SseRowEvent
from backend.core.models import JobRecord, JobStatus
from backend.core.pipeline.orchestrator import SseEvent, run_job
from backend.core.pipeline.s0_intake import UploadedBlob
from backend.core.pipeline.s10_rows import pages_accounted_for
from backend.core.pipeline.s11_finalize import flagged_field_count
from backend.settings import Settings
from backend.tests.fixtures.synthetic.files import pdf_of_images
from backend.tests.fixtures.synthetic.pages import clean_page
from backend.tests.fixtures.vision_scenarios import pipeline_services

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


def new_job() -> JobRecord:
    return JobRecord(
        job_id="job-int",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
        status=JobStatus.running,
        idempotency_key=None,
        page_count=0,
        pages_done=0,
        rows=[],
        groups=[],
        orphan_page_ids=[],
        report=None,
        errors=[],
    )


def test_run_job_uses_s10_and_finalizes_from_the_rows(
    tmp_path: Path, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    settings = Settings(_env_file=None, data_dir=str(tmp_path), local_only=True)  # type: ignore[call-arg]
    services = pipeline_services(
        settings,
        storage,
        imaging,
        None,
        "unused",
        documents=DocumentReader(settings=settings),
        any_page=True,
    )
    upload = UploadedBlob(
        filename="forms.pdf", data=pdf_of_images([clean_page(), clean_page(seed=3)])
    )
    events: list[SseEvent] = []

    job = asyncio.run(run_job(new_job(), [upload], services, on_event=events.append))  # type: ignore[arg-type]

    assert job.rows, "S10 produced rows"
    assert pages_accounted_for(job.page_count, job.groups, job.orphan_page_ids)
    assert job.report is not None and job.report.row_count == len(job.rows)
    assert {r.group_id for r in job.rows} <= {g.group_id for g in job.groups}
    row_events = [e for e in events if isinstance(e, SseRowEvent)]
    assert [e.data.row_id for e in row_events] == [r.row_id for r in job.rows]
    done = [e for e in events if isinstance(e, SseDoneEvent)]
    assert len(done) == 1
    assert done[0].data.flagged == flagged_field_count(job.rows)
    assert job.status in (JobStatus.needs_review, JobStatus.confirmed)
    assert "PAGE_ACCOUNTING" not in job.errors
