"""Job queue — bounded asyncio queue with a configurable worker pool.

Designed to be testable in isolation: the pipeline callable is injected,
so tests can pass a fake pipeline without importing the real pipeline stages.

Queue is full (asyncio.QueueFull) → the caller must return 429 + Retry-After: 30.
One page in memory per worker — CPU-bound work runs in a thread pool (T22).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog

log = structlog.get_logger()

# Type alias for the injected pipeline function
PipelineCallable = Callable[[str], Awaitable[None]]

# Hard-coded structural constant: the spec says maxsize=50 throughout.
# Changed only if the spec changes (§9 config rule exempts structural constants).
QUEUE_MAXSIZE = 50


class JobQueue:
    """Bounded async job queue with a configurable worker pool.

    Usage:
        queue = JobQueue(max_concurrency=2, pipeline=run_pipeline)
        queue.start()   # call from asyncio event loop (e.g. lifespan)
        await queue.enqueue("job-123")
        queue.stop()    # call on shutdown
    """

    def __init__(
        self,
        max_concurrency: int,
        pipeline: PipelineCallable,
        maxsize: int = QUEUE_MAXSIZE,
    ) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self._pipeline = pipeline
        self._max_concurrency = max_concurrency
        self._tasks: list[asyncio.Task[None]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(self, job_id: str) -> None:
        """Add a job_id to the queue.

        Raises asyncio.QueueFull immediately when the queue is at capacity.
        The caller must catch QueueFull and return HTTP 429 + Retry-After: 30.
        """
        self._queue.put_nowait(job_id)
        log.debug("job_queued", job_id=job_id, qsize=self._queue.qsize())

    def start(self) -> None:
        """Start background worker coroutines.

        Must be called inside a running asyncio event loop (e.g. FastAPI lifespan).
        """
        self._tasks = [
            asyncio.create_task(self._worker(i), name=f"queue-worker-{i}")
            for i in range(self._max_concurrency)
        ]
        log.info("queue_workers_started", count=self._max_concurrency, maxsize=self._queue.maxsize)

    def stop(self) -> None:
        """Cancel all worker tasks gracefully."""
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        log.info("queue_workers_stopped")

    @property
    def qsize(self) -> int:
        """Current number of pending jobs."""
        return self._queue.qsize()

    @property
    def maxsize(self) -> int:
        """Maximum queue capacity."""
        return self._queue.maxsize

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _worker(self, worker_id: int) -> None:
        """Run pipeline for each job_id taken from the queue.

        A failed pipeline call is logged but never propagates — the batch
        must continue (§3.5 error policy).
        """
        while True:
            job_id = await self._queue.get()
            log.info("worker_job_start", worker_id=worker_id, job_id=job_id)
            try:
                await self._pipeline(job_id)
                log.info("worker_job_done", worker_id=worker_id, job_id=job_id)
            except asyncio.CancelledError:
                self._queue.task_done()
                raise  # propagate cancellation so stop() works
            except Exception as exc:
                log.error(
                    "worker_job_failed",
                    worker_id=worker_id,
                    job_id=job_id,
                    exc_type=type(exc).__name__,
                )
            finally:
                self._queue.task_done()
