"""The job registry: what the API knows about every job, for as long as the job lives.

A job is its record, the uploaded bytes (until processing starts), the progress events for
late SSE subscribers, the append-only audit log and the per-row undo history. Nothing is written
to disk here: page images live in storage (purged with the job) and this registry lives in
memory, so a restart loses every job together with its artefacts, which is the intended
lifetime (PLAN §3.2, §8). The registry is touched only from the event loop.

A job is purged at `created_at + JOB_TTL_MINUTES` by its own timer, and by `sweep()` for the
timers a restart would have lost. For a further while it answers with status `purged`, so the UI
shows a message instead of a 404; after that it is unknown. A job that is still running is never
purged under its feet.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import time
from typing import Any, Final
import uuid

from sse_starlette.sse import ServerSentEvent
import structlog

from backend.api.schemas import JobView
from backend.core.errors import JobNotFound, JobPurged
from backend.core.models import (
    CommitmentRow,
    ImageRef,
    JobRecord,
    JobStatus,
    ReviewEdit,
)
from backend.core.pipeline.orchestrator import SseEvent
from backend.core.pipeline.outcome import PageOutcome
from backend.core.pipeline.s0_intake import UploadedBlob
from backend.core.pipeline.s11_finalize import PurgeSchedule, purge_schedule
from backend.core.ports.clock import ClockPort
from backend.core.ports.storage import StoragePort
from backend.settings import Settings

log = structlog.get_logger()

# Structural: statuses in which a job is still being worked on and must not be purged.
_ACTIVE: Final = frozenset({JobStatus.queued, JobStatus.running})
_SECONDS_PER_MINUTE: Final = 60.0


@dataclass
class JobEntry:
    """Everything the API keeps for one job."""

    record: JobRecord
    uploads: list[UploadedBlob] | None
    consent: bool = False
    derived_from: str | None = None
    outcomes: list[PageOutcome] = field(default_factory=list)  # what each page produced
    all_known_samples: bool = False  # every page matched the sample hashes (PLAN §8.5)
    page_images: dict[str, ImageRef] = field(default_factory=dict)  # corrected page, per page id
    raw_images: dict[str, ImageRef] = field(default_factory=dict)  # as scanned, per page id
    events: list[SseEvent] = field(default_factory=list)
    audit: list[ReviewEdit] = field(default_factory=list)
    undo: dict[str, list[tuple[CommitmentRow, ReviewEdit]]] = field(default_factory=dict)
    finished: bool = False
    visible_until: datetime | None = None  # set when purged
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    timer: asyncio.TimerHandle | None = None

    @property
    def job_id(self) -> str:
        return self.record.job_id

    @property
    def purged(self) -> bool:
        return self.record.status is JobStatus.purged

    def wake(self) -> None:
        """Tell every waiting SSE generator that something changed."""
        self.changed.set()
        self.changed = asyncio.Event()

    def publish(self, event: SseEvent) -> None:
        """Record an event for late subscribers and wake the live ones. `done` ends the stream."""
        self.events.append(event)
        if event.event == "done":
            self.finished = True
        self.wake()

    def view(self) -> JobView:
        """The job as the UI sees it (no paths, no internal references)."""
        r = self.record
        return JobView(
            job_id=r.job_id,
            status=r.status,
            expires_at=r.expires_at,
            page_count=r.page_count,
            pages_done=r.pages_done,
            rows=r.rows,
            orphan_page_ids=r.orphan_page_ids,
            report=r.report,
            notices=[e for e in r.errors if e in _UI_NOTICES],
        )


# Job error codes the UI explains to the technician (the rest stay in logs and the report).
_UI_NOTICES: Final = frozenset({"AI_UNAVAILABLE"})


@dataclass
class ProcessMetrics:
    """Process-level aggregates for `/v1/metrics`. Counters only, never a value."""

    jobs_total: int = 0
    fields_total: int = 0
    fields_closed_local: int = 0
    fields_escalated: int = 0
    fields_flagged: int = 0
    cloud_calls: int = 0


class JobStore:
    """In-memory registry of jobs with idempotency, event replay and TTL purging."""

    def __init__(self, *, settings: Settings, clock: ClockPort, storage: StoragePort) -> None:
        self._settings = settings
        self._clock = clock
        self._storage = storage
        self._jobs: dict[str, JobEntry] = {}
        self._keys: dict[str, str] = {}
        self.metrics = ProcessMetrics()

    # ------------------------------------------------------------------ creation

    def find_by_key(self, key: str) -> JobEntry | None:
        """The live job created with this idempotency key, if any."""
        job_id = self._keys.get(key)
        if job_id is None:
            return None
        try:
            return self.lookup(job_id)
        except JobNotFound:
            self._keys.pop(key, None)
            return None

    def create(
        self,
        uploads: list[UploadedBlob],
        *,
        idempotency_key: str | None,
        consent: bool,
        derived_from: str | None = None,
    ) -> JobEntry:
        """Register a queued job and start its purge timer."""
        now = self._clock.now()
        schedule = self._schedule(now)
        job_id = str(uuid.uuid4())
        record = JobRecord(
            job_id=job_id,
            created_at=now,
            expires_at=schedule.expires_at,
            status=JobStatus.queued,
            idempotency_key=idempotency_key,
            page_count=0,
            pages_done=0,
            rows=[],
            groups=[],
            orphan_page_ids=[],
            report=None,
            errors=[],
        )
        entry = JobEntry(record=record, uploads=uploads, consent=consent, derived_from=derived_from)
        try:
            loop = asyncio.get_running_loop()
            ttl = self._settings.job_ttl_minutes * _SECONDS_PER_MINUTE
            entry.timer = loop.call_later(ttl, self.purge, job_id)
        except RuntimeError:  # no running loop (a synchronous caller): the sweeper covers it
            entry.timer = None
        self._jobs[job_id] = entry
        if idempotency_key:
            self._keys[idempotency_key] = job_id
        self.metrics.jobs_total += 1
        return entry

    def discard(self, job_id: str) -> None:
        """Forget a job that never started (e.g. the queue refused it)."""
        entry = self._jobs.pop(job_id, None)
        if entry is not None:
            if entry.timer is not None:
                entry.timer.cancel()
            if entry.record.idempotency_key:
                self._keys.pop(entry.record.idempotency_key, None)

    # ------------------------------------------------------------------ lookup

    def lookup(self, job_id: str) -> JobEntry:
        """The job, purged tombstone included. Applies the TTL lazily, so a job past its time is
        never served as live even if no timer has fired. Raises JobNotFound."""
        entry = self._jobs.get(job_id)
        if entry is None:
            raise JobNotFound("unknown job")
        now = self._clock.now()
        if entry.purged:
            if entry.visible_until is not None and now >= entry.visible_until:
                self._forget(entry)
                raise JobNotFound("unknown job")
            return entry
        if now >= entry.record.expires_at and entry.record.status not in _ACTIVE:
            self.purge(job_id)
        return entry

    def live(self, job_id: str) -> JobEntry:
        """The job if it still has its data. Raises JobNotFound or JobPurged (both 404)."""
        entry = self.lookup(job_id)
        if entry.purged:
            raise JobPurged("job was purged")
        return entry

    def all_entries(self) -> list[JobEntry]:
        return list(self._jobs.values())

    # ------------------------------------------------------------------ purge

    def _schedule(self, created: datetime) -> PurgeSchedule:
        return purge_schedule(
            created,
            ttl_minutes=self._settings.job_ttl_minutes,
            purged_visible_minutes=self._settings.purged_visible_minutes,
            sweep_minutes=self._settings.purge_sweep_minutes,
        )

    def purge(self, job_id: str) -> int:
        """Delete the job's artefacts and personal data now; keep a `purged` tombstone for the
        configured while. Idempotent. Returns the number of files removed."""
        entry = self._jobs.get(job_id)
        if entry is None:
            return 0
        removed = self._storage.purge_job(job_id)
        if entry.timer is not None:
            entry.timer.cancel()
            entry.timer = None
        if not entry.purged:
            now = self._clock.now()
            entry.record = entry.record.model_copy(
                update={
                    "status": JobStatus.purged,
                    "rows": [],
                    "groups": [],
                    "orphan_page_ids": [],
                    "report": None,
                    "idempotency_key": None,
                }
            )
            entry.visible_until = now + timedelta(minutes=self._settings.purged_visible_minutes)
        entry.uploads = None
        entry.events.clear()
        entry.audit.clear()
        entry.undo.clear()
        entry.page_images.clear()
        entry.outcomes.clear()
        entry.raw_images.clear()
        entry.finished = True
        entry.wake()
        for key, owner in list(self._keys.items()):
            if owner == job_id:
                del self._keys[key]
        log.info("job_purged", job_id=job_id, files=removed)
        return removed

    def sweep(self) -> int:
        """Purge every job past its time (whose timer was lost) and forget the tombstones that
        have been visible long enough. Returns the number of jobs purged."""
        now = self._clock.now()
        purged = 0
        for entry in self.all_entries():
            if entry.purged:
                if entry.visible_until is not None and now >= entry.visible_until:
                    self._forget(entry)
            elif now >= entry.record.expires_at and entry.record.status not in _ACTIVE:
                self.purge(entry.job_id)
                purged += 1
        return purged

    def _forget(self, entry: JobEntry) -> None:
        self._jobs.pop(entry.job_id, None)

    def shutdown(self) -> None:
        """Cancel every timer (application stop)."""
        for entry in self._jobs.values():
            if entry.timer is not None:
                entry.timer.cancel()
                entry.timer = None

    # ------------------------------------------------------------------ metrics

    def record_finished(self, entry: JobEntry) -> None:
        """Add a finished job's counters to the process aggregates."""
        report = entry.record.report
        if report is None:
            return
        m = self.metrics
        m.fields_total += report.fields_total
        m.fields_closed_local += report.fields_closed_local
        m.fields_escalated += report.fields_escalated
        m.fields_flagged += report.fields_flagged
        m.cloud_calls += report.cost.provider_calls

    def active_count(self) -> int:
        return sum(1 for e in self._jobs.values() if e.record.status in _ACTIVE)


async def event_stream(
    entry: JobEntry, *, wait_seconds: float, max_seconds: float
) -> AsyncIterator[ServerSentEvent]:
    """Replay the job's events from the start, then follow it until `done`, until the job is
    purged or until `max_seconds` have passed. An idle wait costs nothing: it wakes on a new event
    and re-checks the ceiling every `wait_seconds`. The transport (sse-starlette) sends the
    `: ping` comments and stops this generator when the client disconnects."""
    started = time.monotonic()
    index = 0
    while True:
        while index < len(entry.events):
            event = entry.events[index]
            index += 1
            payload: dict[str, Any] = json.loads(event.data.model_dump_json())
            yield ServerSentEvent(data=json.dumps(payload, ensure_ascii=False), event=event.event)
        if entry.finished or time.monotonic() - started >= max_seconds:
            return
        try:
            await asyncio.wait_for(entry.changed.wait(), timeout=wait_seconds)
        except TimeoutError:
            continue
