"""Review: edit a field, undo, confirm, and read the audit log (PLAN §3.4, T22).

Every change is appended to an append-only log: an edit records the old and the new value, an undo
is itself an entry. The values in the log are patient data, so the log lives and dies with the job
(it is cleared by the purge) and is never written to a log line. A confirmed or purged job takes no
edits (409).
"""

from __future__ import annotations

from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, Path

from backend.api.deps import get_container, get_store, require_api_key
from backend.api.errors import (
    FieldNotFound,
    InvalidEdit,
    JobNotReady,
    NothingToUndo,
    RowNotFound,
)
from backend.api.schemas import FieldEditRequest, JobView
from backend.container import Container
from backend.core.errors import EditConflict
from backend.core.models import (
    CommitmentRow,
    ConfidenceLevel,
    FieldKey,
    JobStatus,
    ReviewEdit,
)
from backend.core.review import EditInvalid, apply_edit, resolve_edit
from backend.jobs import JobEntry, JobStore

router = APIRouter(tags=["review"], dependencies=[Depends(require_api_key)])

# Structural: the actor recorded for a confirmation or an undo made without a named user.
DEFAULT_ACTOR = "technician"


def _editable(store: JobStore, job_id: str) -> JobEntry:
    """The job if it still takes edits. Raises JobNotFound (404) and EditConflict (409) for a
    purged or confirmed job."""
    entry = store.lookup(job_id)
    if entry.purged or entry.record.status is JobStatus.confirmed:
        raise EditConflict("the job no longer takes edits")
    return entry


def _row(entry: JobEntry, row_id: str) -> tuple[int, CommitmentRow]:
    for index, row in enumerate(entry.record.rows):
        if row.row_id == row_id:
            return index, row
    raise RowNotFound("unknown row")


def _replace(entry: JobEntry, index: int, row: CommitmentRow) -> None:
    rows = list(entry.record.rows)
    rows[index] = row
    entry.record = entry.record.model_copy(update={"rows": rows})


@router.patch("/jobs/{job_id}/rows/{row_id}/fields/{field}", response_model=CommitmentRow)
async def edit_field(
    job_id: str,
    row_id: str,
    field: Annotated[FieldKey, Path()],
    body: FieldEditRequest,
    store: JobStore = Depends(get_store),
    container: Container = Depends(get_container),
) -> CommitmentRow:
    """Set a field to a typed value (`null` clears it) or to one of its candidates, recompute the
    row level and append the change to the audit log. 422 for a value that breaks a hard rule,
    404 for an unknown row or field, 409 on a confirmed or purged job."""
    entry = _editable(store, job_id)
    index, row = _row(entry, row_id)
    if field not in row.fields:
        raise FieldNotFound("the row has no such field")
    try:
        edited, action = resolve_edit(
            row,
            field,
            value=body.value,
            pick_index=body.pick_candidate_index,
            clock=container.clock,
            senders=container.senders,
        )
    except EditInvalid as exc:
        raise InvalidEdit(str(exc)) from None
    record = ReviewEdit(
        edit_id=str(uuid.uuid4()),
        job_id=job_id,
        row_id=row_id,
        field=field,
        old_value=row.fields[field].value,
        new_value=edited.value,
        actor=body.actor,
        action=action,
        at=container.clock.now(),
    )
    updated = apply_edit(row, field, edited)
    _replace(entry, index, updated)
    entry.audit.append(record)
    entry.undo.setdefault(row_id, []).append((row, record))
    return updated


@router.post("/jobs/{job_id}/rows/{row_id}/undo", response_model=CommitmentRow)
async def undo_edit(
    job_id: str,
    row_id: str,
    store: JobStore = Depends(get_store),
    container: Container = Depends(get_container),
) -> CommitmentRow:
    """Revert the last edit of the row. The log stays append-only: the undo is an entry too."""
    entry = _editable(store, job_id)
    index, current = _row(entry, row_id)
    stack = entry.undo.get(row_id, [])
    if not stack:
        raise NothingToUndo("the row has no edit to undo")
    before, edit = stack.pop()
    _replace(entry, index, before)
    entry.audit.append(
        ReviewEdit(
            edit_id=str(uuid.uuid4()),
            job_id=job_id,
            row_id=row_id,
            field=edit.field,
            old_value=current.fields[edit.field].value,
            new_value=before.fields[edit.field].value,
            actor=DEFAULT_ACTOR,
            action="undo",
            at=container.clock.now(),
        )
    )
    return before


@router.post("/jobs/{job_id}/confirm", response_model=JobView)
async def confirm_job(
    job_id: str,
    store: JobStore = Depends(get_store),
    container: Container = Depends(get_container),
) -> JobView:
    """Mark the job reviewed. Rows keep `edited` where a person changed them and become
    `confirmed` otherwise; a field accepted while still flagged is recorded in the audit log.
    409 while the job is still running, and once it is confirmed."""
    entry = _editable(store, job_id)
    if entry.record.status not in (JobStatus.needs_review, JobStatus.confirmed):
        raise JobNotReady("the job has not finished")
    now = container.clock.now()
    rows = []
    for row in entry.record.rows:
        for key, result in row.fields.items():
            if result.level is ConfidenceLevel.D and row.status != "edited":
                entry.audit.append(
                    ReviewEdit(
                        edit_id=str(uuid.uuid4()),
                        job_id=job_id,
                        row_id=row.row_id,
                        field=key,
                        old_value=result.value,
                        new_value=result.value,
                        actor=DEFAULT_ACTOR,
                        action="confirm",
                        at=now,
                    )
                )
        rows.append(
            row if row.status == "edited" else row.model_copy(update={"status": "confirmed"})
        )
    entry.record = entry.record.model_copy(update={"rows": rows, "status": JobStatus.confirmed})
    entry.undo.clear()
    return entry.view()


@router.get("/jobs/{job_id}/audit", response_model=list[ReviewEdit])
async def get_audit(job_id: str, store: JobStore = Depends(get_store)) -> list[ReviewEdit]:
    """Every review action on the job, oldest first."""
    return list(store.live(job_id).audit)
