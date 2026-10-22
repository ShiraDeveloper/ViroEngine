"""PageOutcome builders for the S10 tests. Fields are decided by the real S8, so a test starts from
the same results the pipeline produces; nothing is read from `reference/`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from backend.adapters.fakes import FakeSenders, FixedClock
from backend.core.models import (
    Box,
    Candidate,
    FieldKey,
    ReaderName,
    RegionKind,
    SourceKind,
)
from backend.core.pipeline.outcome import PageOutcome
from backend.core.pipeline.s5_classify import PageRole
from backend.core.pipeline.s8_score import FieldEvaluation, evaluate_field
from backend.core.pipeline.s10_rows import RowContext
from backend.core.scoring import DEFAULT_WEIGHTS, SECOND_READ_NOTE

CLOCK = FixedClock()  # 2026-10-18
VALID_ID = "011223344"
SENDER = "בית חולים הדסה"


def ctx(**kw: object) -> RowContext:
    return RowContext(clock=CLOCK, senders=FakeSenders(), **kw)  # type: ignore[arg-type]


def box_at(index: int) -> Box:
    """A distinct location per index, top to bottom."""
    y = 0.10 + 0.07 * index
    return (y, 0.50, y + 0.03, 0.80)


def _cand(
    value: str,
    reader: ReaderName,
    source: SourceKind,
    box: Box,
    *,
    note: bool = False,
    conf: float = 0.9,
    label_distance: float | None = 0.05,
) -> Candidate:
    return Candidate(
        value=value,
        reader=reader,
        source=source,
        box=box,
        reader_confidence=conf,
        label_distance=label_distance,
        region=RegionKind.patient_box,
        notes=[SECOND_READ_NOTE] if note else [],
    )


def agreed(value: str, box: Box, *, source: SourceKind = SourceKind.printed) -> list[Candidate]:
    """One value read twice by independent readers at one place: closes at level B (or better)."""
    return [
        _cand(value, ReaderName.tesseract, source, box),
        _cand(value, ReaderName.tesseract, source, box, note=True),
    ]


def barcoded(value: str, box: Box) -> list[Candidate]:
    """A barcode and its printed digits agreeing."""
    return [
        _cand(value, ReaderName.zxing, SourceKind.barcode, box),
        _cand(value, ReaderName.tesseract, SourceKind.printed, box),
    ]


def noise(value: str, box: Box) -> list[Candidate]:
    """A stray token read once, with no label nearby: must never survive on its own."""
    return [
        _cand(value, ReaderName.tesseract, SourceKind.printed, box, conf=0.3, label_distance=None)
    ]


def evaluation(key: FieldKey, cands: Sequence[Candidate]) -> FieldEvaluation:
    return evaluate_field(key, cands, spec=None, clock=CLOCK, weights=DEFAULT_WEIGHTS)


def page(
    page_id: str,
    role: PageRole | None = "primary_form",
    *,
    centrals: Sequence[str] = (),
    stray: Sequence[str] = (),
    date: str | None = None,
    commitments: Sequence[str] = (),
    first_name: str | None = None,
    last_name: str | None = None,
    national_id: str | None = VALID_ID,
    sequence: tuple[int, int] | None = None,
    sha: str | None = None,
    sender: str | None = SENDER,
    status: Literal["done", "failed"] = "done",
) -> PageOutcome:
    """A processed page whose fields were decided by S8 from the candidates implied here."""
    if status == "failed":
        return PageOutcome(
            page_id=page_id, label=None, source_file="f.pdf", page_index=0, status="failed"
        )
    cands: dict[FieldKey, list[Candidate]] = {k: [] for k in FieldKey}
    for i, value in enumerate(centrals):
        cands[FieldKey.central_number] += barcoded(
            value, (0.02, 0.10 + 0.2 * i, 0.06, 0.25 + 0.2 * i)
        )
    for i, value in enumerate(stray):
        cands[FieldKey.central_number] += noise(value, (0.90, 0.10 + 0.1 * i, 0.92, 0.15 + 0.1 * i))
    if date is not None:
        cands[FieldKey.receipt_date] = agreed(
            date, (0.05, 0.60, 0.09, 0.90), source=SourceKind.stamp
        )
    for i, value in enumerate(commitments):
        cands[FieldKey.commitment_number] += agreed(value, box_at(i))
    if first_name is not None:
        cands[FieldKey.first_name] = agreed(first_name, (0.30, 0.50, 0.33, 0.80))
    if last_name is not None:
        cands[FieldKey.last_name] = agreed(last_name, (0.34, 0.50, 0.37, 0.80))
    if national_id is not None:
        cands[FieldKey.national_id] = agreed(national_id, (0.40, 0.50, 0.43, 0.80))
    if sender is not None:
        cands[FieldKey.sender] = agreed(sender, (0.01, 0.30, 0.04, 0.70))
    return PageOutcome(
        page_id=page_id,
        label=page_id,
        source_file="f.pdf",
        page_index=0,
        status="done",
        page_role=role,
        sender=sender,
        sha256=sha,
        page_number=sequence[0] if sequence else None,
        page_total=sequence[1] if sequence else None,
        evaluations={k: evaluation(k, cs) for k, cs in cands.items()},
    )
