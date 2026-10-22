"""Frozen domain contracts (PLAN §3.3). Any change: regenerate the fixture and the TS types."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Normalized y_min, x_min, y_max, x_max, origin top-left, 0.0-1.0.
Box = tuple[float, float, float, float]

# Structural: the response schema's maxLength for a value or alternative (PLAN Appendix B).
VISION_TEXT_MAX = 120


class Strict(BaseModel):
    """Boundary model base: unknown keys are rejected and instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())


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
    clinical = "clinical"  # never sent to a provider


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    needs_review = "needs_review"
    confirmed = "confirmed"
    failed = "failed"
    purged = "purged"


# Closed set of level-D reasons (PLAN §5.2): code -> Hebrew UI text.
# A reason is never free text and is never built at a call site.
REASON_TEXT: dict[str, str] = {
    "not_found": "לא נמצא בטופס",
    "readers_disagree": "שני הקוראים לא הסכימו",
    "failed_id_check": 'ספרת הביקורת של ת"ז לא תקינה',
    "date_impossible": "תאריך לא קיים בלוח השנה",
    "date_future": "התאריך עתידי",
    "ambiguous_repair": "יש יותר מתיקון אפשרי אחד",
    "handwriting_unverified": "כתב יד שלא אומת",
    "sender_not_in_list": "הגורם לא ברשימה הסגורה",
    "stamp_and_handwritten_differ": "החותמת והתאריך בכתב יד שונים",
    "low_quality_region": "איכות התמונה באזור נמוכה",
    "providers_exhausted": "לא ניתן לאמת — נדרשת בדיקה",
    "multiple_candidates": "נמצאו כמה אפשרויות",
}


class ImageRef(Strict):
    job_id: str
    page_id: str
    kind: Literal["color", "gray_bin", "thumb", "crop"]
    path: str
    width: int
    height: int
    sha256: str


class PageRaw(Strict):
    page_id: str
    label: str | None = None
    source_file: str
    page_index: int
    sha256: str
    image: ImageRef
    # sha256 is in config/sample_hashes.json (§8.5); gates tier-2 escalation.
    known_sample: bool = False
    # sha256 of the origin page for Stress Mode / augmented variants; such a page
    # inherits known_sample from its origin.
    derived_from: str | None = None


class PageMeasurement(Strict):
    """Output of S1. Measurement only."""

    rotation_deg: Literal[0, 90, 180, 270]
    rotation_votes: dict[str, int]
    skew_deg: float  # degrees, -15..15
    blur_score: float  # Laplacian variance, higher = sharper
    illumination_cv: float  # background coefficient of variation
    noise_score: float  # 0..1
    is_photo: bool
    needs: list[str]  # ["rotate", "deskew", "clahe", ...]


class NormalizedPage(Strict):
    raw: PageRaw
    measurement: PageMeasurement
    corrections_applied: list[str]
    color: ImageRef
    gray_bin: ImageRef
    thumb: ImageRef


class OcrWord(Strict):
    text: str
    box: Box
    confidence: float


class OcrResult(Strict):
    reader: ReaderName
    words: list[OcrWord]
    text: str
    candidates: list[str] = Field(default_factory=list)


class BarcodeHit(Strict):
    payload: str  # any length, never filtered
    symbology: str
    box: Box
    orientation_deg: int
    nearby_text: list[str]


class Region(Strict):
    kind: RegionKind
    box: Box
    detector: str
    score: float
    label_text: str | None = None


class Candidate(Strict):
    value: str
    reader: ReaderName
    source: SourceKind
    box: Box | None
    reader_confidence: float  # as reported - NOT the final score
    label_distance: float | None
    region: RegionKind | None
    notes: list[str] = Field(default_factory=list)


class ReadingTrailStep(Strict):
    stage: str
    reader: ReaderName
    outcome: Literal["value", "no_value", "rejected", "skipped"]
    value: str | None
    reader_confidence: float | None
    duration_ms: int
    cloud: bool  # True when data left the machine
    tier: Literal[1, 2] | None = None  # escalation tier; None for local steps
    page_sent: bool = False  # True only for a tier-2 (redacted full page) call


class ValidationOutcome(Strict):
    name: str
    kind: Literal["hard", "soft"]
    passed: bool
    detail: str | None = None
    repaired_from: str | None = None


class FieldResult(Strict):
    field: FieldKey
    value: str | None  # None = not found. Never a guess
    display_value: str | None
    level: ConfidenceLevel
    score: float
    source: SourceKind
    box: Box | None
    candidates: list[Candidate]
    validations: list[ValidationOutcome]
    trail: list[ReadingTrailStep]
    reason: str | None  # Hebrew text from REASON_TEXT when level == D
    escalated: bool

    @model_validator(mode="after")
    def _level_d_has_closed_set_reason(self) -> FieldResult:
        """A level-D field must carry a reason from the closed set (§2, §5.2)."""
        if self.level is ConfidenceLevel.D and self.reason not in REASON_TEXT.values():
            raise ValueError("level D requires a reason from the closed set REASON_TEXT")
        return self


class CommitmentRow(Strict):
    row_id: str
    group_id: str
    page_ids: list[str]
    # "x-key-type" lets the TS generator emit Partial<Record<FieldKey, FieldResult>>.
    fields: dict[FieldKey, FieldResult] = Field(json_schema_extra={"x-key-type": "FieldKey"})
    central_numbers: list[str]  # joined with "; " on export
    row_level: ConfidenceLevel  # worst among critical fields
    status: Literal["auto", "confirmed", "edited"]
    # S10 (T20): row_id of an earlier row this one repeats (same page content, same critical
    # values). A suggestion only: nothing is merged or removed automatically.
    merge_suggestion: str | None = None


class PageGroup(Strict):
    group_id: str
    page_ids: list[str]
    sender: str | None
    family: str | None
    is_orphan: bool
    grouped_by: list[str]  # ["sequence", "central_number", "stamp_date"]


class CostCounters(Strict):
    """Counters only. The PLAN names this type but does not define it (see T2 notes)."""

    provider_calls: int
    cache_hits: int
    input_tokens: int
    output_tokens: int
    estimated_usd: float


class RunReport(Strict):
    """run_report.json - counters only, no values, no PII."""

    job_id: str
    started_at: datetime
    finished_at: datetime | None
    page_count: int
    row_count: int
    fields_total: int
    fields_closed_local: int
    fields_escalated: int
    fields_flagged: int
    tier1_calls: int  # provider requests per tier (§6.4, §6.6)
    tier2_calls: int
    local_close_rate: float
    level_histogram: dict[str, int]
    stage_durations_ms: dict[str, int]
    corrections_histogram: dict[str, int]
    cost: CostCounters
    provider_failovers: list[str]
    engine_version: str
    prompt_version: str
    # PLAN §8.5: the job's consent to send crops of documents that are not known samples.
    cloud_consent: bool = False


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


class SenderSpec(Strict):
    canonical: str
    family: Literal["hmo", "hospital", "private_lab", "military", "state_institute"]
    aliases: list[str]
    keywords: list[str]
    commitment_labels: list[str]
    commitment_deny: list[str] = Field(default_factory=list)
    pattern_hints: list[str] = Field(default_factory=list)  # hints only
    barcode_is_commitment: bool | None = None  # None = unknown
    notes: str | None = None


# ---------------------------------------------------------------------------
# Port-facing stubs from Task 3 (Ahuba). Kept verbatim from main so the ports and
# fakes keep working. T18 (providers) and T24 (learning) own their final shapes.
# ---------------------------------------------------------------------------


class CropPayload(Strict):
    """One crop that leaves the machine (PLAN §13 T18). `png` holds the pixels of `box` only."""

    field: FieldKey
    png: bytes
    region_kind: RegionKind  # asserted never `clinical` where the request is built and sent
    box: Box


class VisionRequest(Strict):
    """What one page sends to a cloud vision provider: crops, never the page (PLAN §6.4).

    `page_thumb` is the redacted 512 px layout thumbnail, present only when a field needs page
    context. `candidates` is what the code already found, so the model chooses rather than
    invents. `task` selects the prompt: `fields` is the S9 extraction (`crops` hold one crop per
    unresolved field), `sender` is S5's letterhead question answered from `sender_options`.
    The plan's `schema` field is called `response_schema` because `schema` shadows a pydantic
    attribute.
    """

    job_id: str
    page_id: str
    task: Literal["fields", "sender"] = "fields"
    page_thumb: bytes | None = None
    crops: list[CropPayload]
    sender: str | None = None
    sender_options: list[str] = Field(default_factory=list)
    label_synonyms: list[str] = Field(default_factory=list)
    candidates: dict[FieldKey, list[str]] = Field(
        default_factory=dict, json_schema_extra={"x-key-type": "FieldKey"}
    )
    barcode_candidates: list[str] = Field(default_factory=list)  # decoded from the sent crops
    prompt_version: str
    response_schema: dict[str, Any] = Field(default_factory=dict)


class VisionAnswer(Strict):
    """The validated answer for one field. There is no confidence here by design: the model is
    never asked how sure it is, and S8 computes confidence from independent signals."""

    values: list[str] = Field(default_factory=list, max_length=10)
    source: SourceKind = SourceKind.unknown
    box: Box | None = None
    alts: list[str] = Field(default_factory=list, max_length=5)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _bounded_text(self) -> VisionAnswer:
        """The schema's maxLength: a pathological response cannot become a value."""
        if any(len(v) > VISION_TEXT_MAX for v in [*self.values, *self.alts]):
            raise ValueError(f"a value or alternative is longer than {VISION_TEXT_MAX} characters")
        return self


class VisionResponse(Strict):
    """Schema-validated provider response. The model never assigns its own confidence.

    `answers` carries the S9 extraction per requested field; `raw_text`/`candidates` carry the
    S5 sender answer. `failovers` lists "from->to" hops that happened before this answer;
    `cache_hit` is True when it came from the local cache and no provider was called."""

    raw_text: str
    candidates: list[str]
    provider: ReaderName
    model_id: str
    prompt_version: str
    duration_ms: int
    answers: dict[FieldKey, VisionAnswer] = Field(
        default_factory=dict, json_schema_extra={"x-key-type": "FieldKey"}
    )
    ambiguities: list[str] = Field(default_factory=list, max_length=10)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit: bool = False
    failovers: list[str] = Field(default_factory=list)


class ProviderHealth(Strict):
    name: str
    credentials: int  # count, never the key values
    state: Literal["ok", "open", "half_open", "exhausted"]
    labels: list[str] = Field(default_factory=list)  # credential labels such as "k1", never keys


class LayoutObservation(Strict):
    """Locations and abstracted shapes only. Deliberately has NO value field: adding one must
    break `core.learning.assert_pii_free` and the reflection test that guards it (PLAN §7.1)."""

    sender: str
    field: FieldKey
    region_kind: RegionKind | None
    box: Box
    source: SourceKind
    page_role: str = "primary_form"
    label_text: str | None = None  # static printed label text, never a form value
    shape: str | None = None  # '#######' / 'A######' from abstract_shape(), never the value


class RunMetric(Strict):
    """Counters of one finished job for the learning curve (PLAN §7.2 `run_metric`). No values."""

    at: datetime
    fields_total: int
    fields_closed_local: int
    fields_escalated: int
    fields_flagged: int
    local_close_rate: float
    provider_calls: int
    engine_version: str


class LayoutHint(Strict):
    """Approximate region location returned by the learning store."""

    region_kind: RegionKind
    box: Box
    confidence: float


class PatternHint(Strict):
    """Regex hint returned by the learning store - never a hard gate (§2.1)."""

    pattern: str
    confidence: float
