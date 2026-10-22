"""The queue's pipeline: take a queued job, run the orchestrator, publish its progress.

`make_pipeline` returns the callable `JobQueue` runs for every job id. It never raises for a
pipeline problem: a failure marks the job `failed`, tells the stream and moves on, so one bad job
cannot stop a worker (PLAN §3.5). CPU work happens inside `run_job`, in a worker thread.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
import json
from pathlib import Path

import structlog

from backend.api.schemas import SseDoneData, SseDoneEvent, SseErrorData, SseErrorEvent
from backend.core.errors import JobNotFound, SafeLineError
from backend.core.models import JobStatus, PageRaw
from backend.core.pipeline.orchestrator import PipelineServices, SseEvent, run_job
from backend.core.pipeline.outcome import PageOutcome
from backend.jobs import JobEntry, JobStore

log = structlog.get_logger()

DEFAULT_SAMPLE_HASHES = Path(__file__).resolve().parents[1] / "config" / "sample_hashes.json"
_FAILED_CODE = "INTERNAL"


def load_known_hashes(path: Path | None = None) -> frozenset[str]:
    """SHA-256 of the rendered sample pages (hashes only, PLAN §8.5). A missing or unreadable file
    means no page is a known sample, which is the safe default: nothing leaves the machine."""
    target = path or DEFAULT_SAMPLE_HASHES
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    hashes = data.get("sha256", []) if isinstance(data, dict) else data
    return frozenset(h for h in hashes if isinstance(h, str))


def _remember_pages(
    entry: JobEntry, raws: Sequence[PageRaw], outcomes: Sequence[PageOutcome]
) -> None:
    """Keep the image references the crop and image routes need, per page id."""
    entry.raw_images = {r.page_id: r.image for r in raws}
    entry.all_known_samples = bool(raws) and all(r.known_sample for r in raws)
    entry.page_images = {o.page_id: o.color_image for o in outcomes if o.color_image is not None}


def make_pipeline(
    store: JobStore,
    services: PipelineServices,
    *,
    known_hashes: frozenset[str],
) -> Callable[[str], Awaitable[None]]:
    """The pipeline callable for `JobQueue`. A purged or unknown job id is skipped quietly."""

    async def run(job_id: str) -> None:
        try:
            entry = store.live(job_id)
        except SafeLineError:
            return
        uploads, entry.uploads = entry.uploads, None  # released: nothing keeps the bytes
        if not uploads:
            return
        entry.record = entry.record.model_copy(update={"status": JobStatus.running})
        try:
            result = await run_job(
                entry.record,
                uploads,
                services,
                on_event=lambda event: _on_event(entry, event),
                cloud_consent=entry.consent,
                known_hashes=known_hashes,
                on_done=lambda raws, outcomes: _remember_pages(entry, raws, outcomes),
            )
        except Exception as exc:  # the worker must survive any one job
            log.error("job_failed", job_id=job_id, exc_type=type(exc).__name__)
            _fail(entry)
            return
        try:
            current = store.lookup(job_id)
        except JobNotFound:
            return
        if current.purged:  # purged while it ran: keep nothing
            return
        current.record = result
        store.record_finished(current)
        current.finished = True
        current.wake()

    return run


def _on_event(entry: JobEntry, event: SseEvent) -> None:
    """Publish an event and keep the job record's progress current for plain polling."""
    entry.publish(event)
    if event.event == "counters":
        entry.record = entry.record.model_copy(
            update={"pages_done": event.data.pages_done, "page_count": event.data.pages_total}
        )


def _fail(entry: JobEntry) -> None:
    """Mark an unexpectedly failed job and tell the stream, so a client never waits forever."""
    entry.record = entry.record.model_copy(
        update={"status": JobStatus.failed, "errors": [*entry.record.errors, _FAILED_CODE]}
    )
    entry.publish(
        SseErrorEvent(
            event="error", data=SseErrorData(code=_FAILED_CODE, page_id=None, recoverable=False)
        )
    )
    entry.publish(
        SseDoneEvent(
            event="done", data=SseDoneData(status=JobStatus.failed, flagged=0, duration_ms=0)
        )
    )
