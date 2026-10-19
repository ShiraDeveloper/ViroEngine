"""Safe Line domain models — frozen data contracts.

CONTRACT STUB: This file was created as a dependency for Task 3 (Ahuba).
Task 2 (Shira) must verify every definition here against §3.3 and the
fixture sample_job.json, and owns this file going forward.
Any change to this file requires regenerating fixtures/sample_job.json
and frontend/src/api/types.ts (see §0.5 rule 6).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Foundational types
# ---------------------------------------------------------------------------

Box = tuple[float, float, float, float]
"""Normalised bounding box: (y_min, x_min, y_max, x_max), origin top-left, range 0.0-1.0."""


class Strict(BaseModel):
    """Base class for all boundary models.

    extra='forbid' + frozen=True: validated at the boundary, trusted inside the core.
    protected_namespaces=(): suppress Pydantic's model_ prefix warning on field names
    like model_id which are legitimate domain fields.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class FieldKey(StrEnum):
    first_name = "first_name"
    last_name = "last_name"
    national_id = "national_id"
    central_number = "central_number"
    receipt_date = "receipt_date"
    sender = "sender"
    commitment_number = "commitment_number"


class SourceKind(StrEnum):
    barcode = "barcode"
    printed = "printed"
    handwritten = "handwritten"
    stamp = "stamp"
    screen = "screen"
    unknown = "unknown"


class ReaderName(StrEnum):
    zxing = "zxing"
    tesseract = "tesseract"
    trocr = "trocr"
    gemini = "gemini"
    openrouter = "openrouter"
    groq = "groq"
    human = "human"


class ConfidenceLevel(StrEnum):
    A = "A"  # barcode + printed agree, validations pass
    B = "B"  # two independent readers agree, validations pass
    C = "C"  # single reader, validations pass, printed source
    D = "D"  # disagreement / failed validation / unverified handwriting / missing


class RegionKind(StrEnum):
    central_sticker = "central_sticker"
    receipt_stamp = "receipt_stamp"
    patient_box = "patient_box"
    commitment_label = "commitment_label"
    table = "table"
    letterhead = "letterhead"
    clinical = "clinical"  # NEVER sent to a cloud provider


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    needs_review = "needs_review"
    confirmed = "confirmed"
    failed = "failed"
    purged = "purged"


# ---------------------------------------------------------------------------
# Page-level models
# ---------------------------------------------------------------------------


class ImageRef(Strict):
    """Pointer to a stored image; carries no pixel data."""

    job_id: str
    page_id: str
    kind: Literal["color", "gray_bin", "thumb", "crop"]
    path: str
    width: int
    height: int
    sha256: str


class PageRaw(Strict):
    """Output of S0 (intake) — one rendered page before any correction."""

    page_id: str
    label: str | None = None
    source_file: str
    page_index: int
    sha256: str
    image: ImageRef


class PageMeasurement(Strict):
    """Output of S1. Measurement only; no modification.

    rotation_deg: degrees clockwise to make the page upright.
    blur_score: Laplacian variance — higher is sharper.
    illumination_cv: background coefficient of variation.
    noise_score: isolated-foreground ratio after morphological opening, 0-1.
    Never raises; an uncomputable measurement reports its neutral value.
    """

    rotation_deg: Literal[0, 90, 180, 270]
    rotation_votes: dict[str, int]
    skew_deg: float
    blur_score: float
    illumination_cv: float
    noise_score: float
    is_photo: bool
    needs: list[str]  # e.g. ["rotate", "deskew", "clahe"]


class NormalizedPage(Strict):
    """Output of S2 — page after targeted correction."""

    raw: PageRaw
    measurement: PageMeasurement
    corrections_applied: list[str]
    color: ImageRef
    gray_bin: ImageRef
    thumb: ImageRef


# ---------------------------------------------------------------------------
# OCR + barcode models
# ---------------------------------------------------------------------------


class OcrWord(Strict):
    text: str
    box: Box
    confidence: float  # 0.0-1.0 as reported by the reader


class OcrResult(Strict):
    """Output of OcrPort.read_page / read_crop."""

    reader: ReaderName
    words: list[OcrWord]
    text: str  # reading-order concatenation
    candidates: list[str] = Field(default_factory=list)


class BarcodeHit(Strict):
    """One decoded barcode.

    payload: any symbology, any length — NEVER filtered by length (§2.1).
    """

    payload: str
    symbology: str
    box: Box
    orientation_deg: int  # working orientation from the sweep
    nearby_text: list[str]  # OCR words within 0.06 x page diagonal


# ---------------------------------------------------------------------------
# Region models
# ---------------------------------------------------------------------------


class Region(Strict):
    """A labelled bounding box on the page, output of S4."""

    kind: RegionKind
    box: Box
    detector: str  # "rule-based" | "yolov8n-onnx" | "learned"
    score: float
    label_text: str | None = None


# ---------------------------------------------------------------------------
# Extraction models
# ---------------------------------------------------------------------------


class Candidate(Strict):
    """One candidate value for a field, from one reader."""

    value: str
    reader: ReaderName
    source: SourceKind
    box: Box | None
    reader_confidence: float  # as reported — NOT the final combined score
    label_distance: float | None
    region: RegionKind | None
    notes: list[str] = Field(default_factory=list)


class ReadingTrailStep(Strict):
    """Audit record for one step in the reading pipeline."""

    stage: str
    reader: ReaderName
    outcome: Literal["value", "no_value", "rejected", "skipped"]
    value: str | None
    reader_confidence: float | None
    duration_ms: int
    cloud: bool  # True when any data left the machine


class ValidationOutcome(Strict):
    name: str
    kind: Literal["hard", "soft"]
    passed: bool
    detail: str | None = None
    repaired_from: str | None = None


class FieldResult(Strict):
    """Complete extraction result for one field on one page.

    value=None means 'not found', never 'error'.
    level=D with a reason from the closed set in §5.2 when the value is uncertain.
    """

    field: FieldKey
    value: str | None  # None = not found. Never a guess.
    display_value: str | None
    level: ConfidenceLevel
    score: float
    source: SourceKind
    box: Box | None
    candidates: list[Candidate]
    validations: list[ValidationOutcome]
    trail: list[ReadingTrailStep]
    reason: str | None  # Hebrew text when level == D; from the closed set in §5.2
    escalated: bool


# ---------------------------------------------------------------------------
# Row + job models
# ---------------------------------------------------------------------------


class CommitmentRow(Strict):
    """One commitment extracted from a group of pages."""

    row_id: str
    group_id: str
    page_ids: list[str]
    fields: dict[str, FieldResult]  # FieldKey → FieldResult
    central_numbers: list[str]  # joined with "; " on export
    row_level: ConfidenceLevel  # worst level among critical fields
    status: Literal["auto", "confirmed", "edited"]


class PageGroup(Strict):
    group_id: str
    page_ids: list[str]
    sender: str | None
    family: str | None
    is_orphan: bool
    grouped_by: list[str]  # e.g. ["sequence", "central_number", "stamp_date"]


class ReviewEdit(Strict):
    edit_id: str
    job_id: str
    row_id: str
    field: FieldKey
    old_value: str | None
    new_value: str | None
    actor: str
    action: Literal["edit", "pick_candidate", "clear", "confirm", "undo"]
    at: datetime


class CostCounters(Strict):
    cloud_calls: int = 0
    crops_sent: int = 0
    tokens_used: int = 0
    estimated_cost_usd: float = 0.0


class RunReport(Strict):
    """run_report.json — counters only, no values, no PII."""

    job_id: str
    started_at: datetime
    finished_at: datetime | None
    page_count: int
    row_count: int
    fields_total: int
    fields_closed_local: int
    fields_escalated: int
    fields_flagged: int
    local_close_rate: float
    level_histogram: dict[str, int]
    stage_durations_ms: dict[str, int]
    corrections_histogram: dict[str, int]
    cost: CostCounters
    provider_failovers: list[str]
    engine_version: str
    prompt_version: str


class JobRecord(Strict):
    job_id: str
    created_at: datetime
    expires_at: datetime
    status: JobStatus
    idempotency_key: str | None
    page_count: int
    pages_done: int
    rows: list[CommitmentRow]
    groups: list[PageGroup]
    orphan_page_ids: list[str]
    report: RunReport | None
    errors: list[str]


# ---------------------------------------------------------------------------
# Sender spec
# ---------------------------------------------------------------------------


class SenderSpec(Strict):
    canonical: str
    family: Literal["hmo", "hospital", "private_lab", "military", "state_institute"]
    aliases: list[str]
    keywords: list[str]
    commitment_labels: list[str]
    commitment_deny: list[str] = Field(default_factory=list)
    pattern_hints: list[str] = Field(default_factory=list)  # hints only — never a hard gate
    barcode_is_commitment: bool | None = None  # None = unknown
    notes: str | None = None


# ---------------------------------------------------------------------------
# Vision LLM models
# ---------------------------------------------------------------------------


class VisionRequest(Strict):
    """Crop + context sent to a cloud vision provider.

    Only crops leave the machine, never a full page (§5).
    clinical regions must never appear here — enforced by assertion in S9.
    """

    job_id: str
    page_id: str
    field: FieldKey
    crop: ImageRef
    context_text: str | None = None
    sender: str | None = None
    prompt_version: str


class VisionResponse(Strict):
    """Schema-validated response from a cloud vision provider.

    The model never assigns its own confidence — S8 does.
    """

    raw_text: str
    candidates: list[str]
    provider: ReaderName
    model_id: str
    prompt_version: str
    duration_ms: int


class ProviderHealth(Strict):
    name: str
    credentials: int  # count, never the key values
    state: Literal["ok", "open", "half_open", "exhausted"]


# ---------------------------------------------------------------------------
# Learning store models
# ---------------------------------------------------------------------------


class LayoutObservation(Strict):
    """Observation recorded by the learning store.

    Holds locations and abstracted shapes only.
    Names, IDs, dates, form values and images are rejected by _assert_pii_free.
    """

    sender: str
    field: FieldKey
    region_kind: RegionKind | None
    box: Box
    source: SourceKind


class LayoutHint(Strict):
    """Approximate region location returned by the learning store."""

    region_kind: RegionKind
    box: Box
    confidence: float


class PatternHint(Strict):
    """Regex hint returned by the learning store — never a hard gate (§2.1)."""

    pattern: str
    confidence: float
