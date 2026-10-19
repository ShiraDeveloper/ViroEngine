"""API request/response models (PLAN §3.4, Appendix C). Frozen with the domain contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from backend.core.models import (
    CommitmentRow,
    ConfidenceLevel,
    FieldKey,
    JobStatus,
    RunReport,
    Strict,
)


class UploadResponse(Strict):
    job_id: str
    status: JobStatus
    page_count: int
    idempotent_replay: bool = False


class FieldEditRequest(Strict):
    """Exactly one of `value` (None clears the field) or `pick_candidate_index`."""

    value: str | None = None
    pick_candidate_index: int | None = Field(default=None, ge=0)
    actor: str = "technician"

    @model_validator(mode="after")
    def _exactly_one(self) -> FieldEditRequest:
        has_value = "value" in self.model_fields_set
        has_pick = self.pick_candidate_index is not None
        if has_value == has_pick:
            raise ValueError("provide exactly one of value or pick_candidate_index")
        return self


class JobView(Strict):
    """The job as the UI sees it: no file paths, no internal refs."""

    job_id: str
    status: JobStatus
    expires_at: datetime
    page_count: int
    pages_done: int
    rows: list[CommitmentRow]
    orphan_page_ids: list[str]
    report: RunReport | None


class ProviderStatus(Strict):
    """Provider readiness by count and state, never by key."""

    credentials: int
    state: str


class Health(Strict):
    status: Literal["ok", "unready"]
    tesseract: Literal["ready", "unavailable"]
    tesseract_langs: list[str]
    models: dict[str, str]
    providers: dict[str, ProviderStatus]


# --- SSE events (PLAN §3.4) -------------------------------------------------


class SsePageData(Strict):
    page_id: str
    label: str | None
    stage: str
    status: Literal["queued", "running", "done", "failed"]
    pct: int = Field(ge=0, le=100)


class SseCountersData(Strict):
    pages_done: int
    pages_total: int
    fields_closed_local: int
    crops_sent: int


class SseRowData(Strict):
    row_id: str
    row_level: ConfidenceLevel
    flagged_fields: list[FieldKey]


class SseDoneData(Strict):
    status: JobStatus
    flagged: int
    duration_ms: int


class SseErrorData(Strict):
    code: str
    page_id: str | None
    recoverable: bool


class SsePageEvent(Strict):
    event: Literal["page"]
    data: SsePageData


class SseCountersEvent(Strict):
    event: Literal["counters"]
    data: SseCountersData


class SseRowEvent(Strict):
    event: Literal["row"]
    data: SseRowData


class SseDoneEvent(Strict):
    event: Literal["done"]
    data: SseDoneData


class SseErrorEvent(Strict):
    event: Literal["error"]
    data: SseErrorData


# Members of the TypeScript `SseEvent` union, emitted by scripts/export_types.py.
SSE_EVENT_TYPES: tuple[type[Strict], ...] = (
    SsePageEvent,
    SseCountersEvent,
    SseRowEvent,
    SseDoneEvent,
    SseErrorEvent,
)
