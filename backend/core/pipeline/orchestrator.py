"""Orchestrator - runs S0-S8 over a batch and reports on it (PLAN §13 T15).

`process_page` is the per-page pipeline: S1 measure -> S2 correct -> S3 read -> S4 regions ->
S5 classify -> S4 again with the sender's labels -> S6 candidates -> S7 second read -> S8 score ->
S9 escalate.
`iter_outcomes` runs it over many pages, one page in memory at a time, and isolates failures:
a page that raises becomes a `failed` outcome and the batch continues. `run_job` wraps both with
intake (S0), progress events, row assembly and the `RunReport`.

S9 (cloud escalation, T18) runs after S8 and re-scores what the model read. The vision port is
touched only when `LOCAL_ONLY` is off and the page is a known sample or the job has consent
(`cloud_consent`, PLAN §8.5); otherwise it is never called, even if one is present.
Everything here reaches the outside world only through ports, so the same code runs against the
fake adapters in CI and the real ones in production.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
import gc
import time
from typing import Any, Final, Protocol

from pydantic import Field
import structlog

from backend.api.schemas import (
    SseCountersData,
    SseCountersEvent,
    SseDoneData,
    SseDoneEvent,
    SseErrorData,
    SseErrorEvent,
    SsePageData,
    SsePageEvent,
    SseRowData,
    SseRowEvent,
)
from backend.core.errors import SafeLineError
from backend.core.geometry import page_size
from backend.core.models import (
    Box,
    Candidate,
    ConfidenceLevel,
    CostCounters,
    FieldKey,
    ImageRef,
    JobRecord,
    JobStatus,
    LayoutHint,
    LayoutObservation,
    NormalizedPage,
    PageMeasurement,
    PageRaw,
    PatternHint,
    ReaderName,
    ReadingTrailStep,
    RunReport,
    SenderSpec,
    SourceKind,
    Strict,
)
from backend.core.pipeline.interim_rows import one_row_per_page
from backend.core.pipeline.outcome import Assembly, PageOutcome
from backend.core.pipeline.s0_intake import UploadedBlob, intake
from backend.core.pipeline.s1_measure import measure
from backend.core.pipeline.s2_correct import correct
from backend.core.pipeline.s3_read_page import read_page
from backend.core.pipeline.s4_regions import detect_regions
from backend.core.pipeline.s5_classify import Classification, ClassifyConfig, classify
from backend.core.pipeline.s6_candidates import LabInputs, generate_candidates
from backend.core.pipeline.s6_candidates_identity import IdentityConfig
from backend.core.pipeline.s7_second_read import SecondReadConfig, second_read
from backend.core.pipeline.s8_score import FieldEvaluation, evaluate_field
from backend.core.pipeline.s9_escalate import (
    EscalationConfig,
    EscalationStats,
    escalate,
    flag_for_review,
)
from backend.core.ports.barcode import BarcodePort
from backend.core.ports.clock import ClockPort
from backend.core.ports.document import DocumentPort
from backend.core.ports.imaging import ImagingPort
from backend.core.ports.learning import LearningStorePort
from backend.core.ports.logo import LogoMatcherPort
from backend.core.ports.ocr import OcrPort
from backend.core.ports.region_detector import RegionDetectorPort
from backend.core.ports.senders import SenderIndexPort
from backend.core.ports.storage import StoragePort
from backend.core.ports.vision_llm import VisionLLMPort
from backend.core.scoring import (
    DEFAULT_WEIGHTS,
    ScoringConfig,
    ScoringWeights,
    scoring_from_mapping,
)
from backend.settings import Settings

log = structlog.get_logger()

SseEvent = SsePageEvent | SseCountersEvent | SseRowEvent | SseDoneEvent | SseErrorEvent

# Structural: shown in run_report.json so a result can be traced to the code that made it.
ENGINE_VERSION: Final = "0.1.0"

# Structural: the stages `process_page` runs, in order. S0 is the batch-level intake.
PAGE_STAGES: Final = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9")

# Structural: S5 reports how it found the sender; the model path is the only cloud one.
_SENDER_READER: Final = {"text": ReaderName.tesseract, "logo": ReaderName.tesseract}


class PipelineLayers(Strict):
    """Switches for the layers the ablation table (PLAN §10.5) turns off one at a time.
    `config/models.yaml: layers` is authoritative; turning a layer off must need no code edit."""

    second_read: bool = True  # S7 independent re-read
    digit_reader: bool = True  # S7 reader 2b (TrOCR); has no effect without second_read
    learning: bool = True  # learned layout hints in S4
    logo: bool = True  # ORB logo fallback in S5


class PipelineConfig(Strict):
    """Every threshold the pipeline reads, from `config/models.yaml` (project rule §9)."""

    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    weights: ScoringWeights = DEFAULT_WEIGHTS
    classify: ClassifyConfig = Field(default_factory=ClassifyConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    second_read: SecondReadConfig = Field(default_factory=SecondReadConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    layers: PipelineLayers = Field(default_factory=PipelineLayers)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> PipelineConfig:
        """Build from the parsed models.yaml. A missing section keeps its defaults. Raises
        pydantic.ValidationError on an unknown key or an out-of-range value."""
        scoring, weights = scoring_from_mapping(data.get("scoring", {}))
        return cls(
            scoring=scoring,
            weights=weights,
            classify=ClassifyConfig.model_validate(data.get("classify", {})),
            identity=IdentityConfig.model_validate(data.get("identity", {})),
            second_read=SecondReadConfig.model_validate(data.get("second_read", {})),
            escalation=EscalationConfig.model_validate(data.get("escalation", {})),
            layers=PipelineLayers.model_validate(data.get("layers", {})),
        )


class PipelineServices(Protocol):
    """What the pipeline needs from the composition root. `backend.container.Container`
    satisfies it structurally, so the core never imports the container or any adapter."""

    @property
    def settings(self) -> Settings: ...
    @property
    def ocr_printed(self) -> OcrPort: ...
    @property
    def ocr_digits(self) -> OcrPort | None: ...
    @property
    def barcode(self) -> BarcodePort: ...
    @property
    def regions(self) -> RegionDetectorPort: ...
    @property
    def vision(self) -> VisionLLMPort | None: ...
    @property
    def storage(self) -> StoragePort: ...
    @property
    def documents(self) -> DocumentPort: ...
    @property
    def imaging(self) -> ImagingPort: ...
    @property
    def learning(self) -> LearningStorePort: ...
    @property
    def clock(self) -> ClockPort: ...
    @property
    def senders(self) -> SenderIndexPort: ...
    @property
    def logo(self) -> LogoMatcherPort | None: ...
    @property
    def pipeline(self) -> PipelineConfig: ...


@dataclass
class _NoLearning:
    """LearningStorePort that knows nothing; used when the `learning` layer is switched off."""

    def record_confirmation(self, obs: LayoutObservation) -> None:
        """Discard the observation."""

    def layout_hints(self, sender: str, field: str) -> list[LayoutHint]:
        """No hints."""
        return []

    def pattern_hints(self, sender: str, field: str) -> list[PatternHint]:
        """No hints."""
        return []


@dataclass
class _Timer:
    """Accumulates wall-clock milliseconds per stage and announces each stage that starts."""

    progress: Callable[[str], None] | None
    totals: dict[str, float] = field(default_factory=dict)
    _name: str | None = None
    _started: float = 0.0

    def begin(self, name: str) -> None:
        """Close the running stage and start `name`; announce it the first time it starts."""
        self.end()
        if self.progress is not None and name not in self.totals:
            self.progress(name)
        self.totals.setdefault(name, 0.0)
        self._name, self._started = name, time.perf_counter()

    def end(self) -> None:
        """Close the running stage, if any."""
        if self._name is not None:
            self.totals[self._name] += (time.perf_counter() - self._started) * 1000.0
            self._name = None

    def millis(self) -> dict[str, int]:
        """Whole milliseconds per stage that ran."""
        self.end()
        return {k: int(v) for k, v in self.totals.items()}


def _cropper(services: PipelineServices) -> Callable[[ImageRef, Box], ImageRef]:
    """Cut `box` out of a stored image and store the piece. Raises RenderFailed (recoverable)."""

    def crop(image: ImageRef, box: Box) -> ImageRef:
        piece = services.imaging.crop(services.storage.get(image), box)
        return services.storage.put_page_image(image.job_id, image.page_id, piece.data, "crop")

    return crop


def _png_cropper(services: PipelineServices) -> Callable[[ImageRef, Box], bytes]:
    """Cut `box` out of a stored image and return the PNG bytes without storing them: the piece
    exists only to be put in a request. Raises RenderFailed (recoverable)."""

    def crop(image: ImageRef, box: Box) -> bytes:
        return services.imaging.crop(services.storage.get(image), box).data

    return crop


def _sender_candidates(cls: Classification) -> list[Candidate]:
    """S5's one sender candidate (PLAN §4 S6, `sender` row). Empty when unresolved: the sender is
    never invented. On a SAP screenshot the value was read from a screen, elsewhere from print."""
    if cls.sender is None:
        return []
    return [
        Candidate(
            value=cls.sender,
            reader=_SENDER_READER.get(cls.sender_method, ReaderName.gemini),
            source=SourceKind.screen if cls.page_role == "sap_screen" else SourceKind.printed,
            box=None,
            reader_confidence=min(1.0, max(0.0, cls.sender_score / 100.0)),
            label_distance=None,
            region=None,
        )
    ]


def _first_read_step(cands: Sequence[Candidate]) -> ReadingTrailStep:
    """The S6 step of the reading trail: what the page read produced for this field."""
    first = cands[0] if cands else None
    return ReadingTrailStep(
        stage="S6",
        reader=first.reader if first is not None else ReaderName.tesseract,
        outcome="value" if first is not None else "no_value",
        value=first.value if first is not None else None,
        reader_confidence=first.reader_confidence if first is not None else None,
        duration_ms=0,
        cloud=False,
    )


def _decide_fields(
    page: NormalizedPage,
    cls: Classification,
    spec: SenderSpec | None,
    candidates: Mapping[FieldKey, list[Candidate]],
    measurement: PageMeasurement,
    services: PipelineServices,
    timer: _Timer,
) -> dict[FieldKey, FieldEvaluation]:
    """S7 then S8 for every field. A field with no candidates is level D `not_found`."""
    cfg = services.pipeline
    crop = _cropper(services)

    def cropper(image: ImageRef, box: Box, scale: float) -> ImageRef:
        # ImagingPort has no resize, so S7's x2.5 upscale (`scale`) is not applied yet and the
        # crop is taken at page resolution. Recorded in docs/limitations.md.
        return crop(image, box)

    digit_ocr = services.ocr_digits if cfg.layers.digit_reader else None
    evaluations: dict[FieldKey, FieldEvaluation] = {}
    for key in FieldKey:
        cands = _sender_candidates(cls) if key is FieldKey.sender else candidates.get(key, [])
        reads: list[Candidate] = []
        trail = [_first_read_step(cands)]
        if key is not FieldKey.sender and cands and cfg.layers.second_read:
            timer.begin("S7")
            is_commitment = spec is not None and key is FieldKey.commitment_number
            try:
                second = second_read(
                    cands,
                    field=key,
                    image=page.color,
                    ocr=services.ocr_printed,
                    crop=cropper,
                    clock=services.clock,
                    hints=spec.pattern_hints if spec is not None and is_commitment else [],
                    digit_ocr=digit_ocr,
                    config=cfg.second_read,
                )
                reads, trail = second.reads, [*trail, *second.trail]
            except SafeLineError as exc:
                # The second read is an extra opinion; losing it leaves the first read standing.
                log.warning("second_read_failed", page_id=page.raw.page_id, code=exc.code)
        timer.begin("S8")
        evaluation = evaluate_field(
            key,
            [*cands, *reads],
            spec=spec,
            clock=services.clock,
            weights=cfg.weights,
            config=cfg.scoring,
            measurement=measurement,
            trail=trail,
        )
        if key is FieldKey.sender and "multiple_candidates" in cls.sender_flags:
            evaluation = flag_for_review(evaluation, "multiple_candidates")
        evaluations[key] = evaluation
    return evaluations


def process_page(
    raw: PageRaw,
    *,
    services: PipelineServices,
    progress: Callable[[str], None] | None = None,
    cloud_consent: bool = False,
) -> PageOutcome:
    """S1-S9 for one page. `progress` is told the name of each stage as it starts.
    `cloud_consent` is the per-job consent of PLAN §8.5: a page that is not a known sample is
    processed locally unless it is True. May raise: `iter_outcomes` is what isolates a failing
    page from its batch."""
    cfg = services.pipeline
    settings = services.settings
    timer = _Timer(progress)
    learning: LearningStorePort = services.learning if cfg.layers.learning else _NoLearning()
    # LOCAL_ONLY is enforced here as well as in the container: a vision port that slipped through
    # must still never be called (project rule §5). So is the §8.5 gate: an unknown document
    # without consent never reaches a provider, S5's letterhead question included.
    cloud_allowed = raw.known_sample or cloud_consent
    vision = None if settings.local_only or not cloud_allowed else services.vision
    logo = services.logo if cfg.layers.logo else None

    timer.begin("S1")
    measurement = measure(
        raw,
        settings=settings,
        ocr=services.ocr_printed,
        barcode=services.barcode,
        imaging=services.imaging,
        storage=services.storage,
    )
    timer.begin("S2")
    page = correct(
        raw, measurement, settings=settings, imaging=services.imaging, storage=services.storage
    )
    timer.begin("S3")
    ocr, barcodes = read_page(
        page,
        settings=settings,
        ocr=services.ocr_printed,
        barcode=services.barcode,
        imaging=services.imaging,
        storage=services.storage,
    )
    timer.begin("S4")
    regions = detect_regions(
        page,
        ocr,
        barcodes,
        settings=settings,
        sender=None,
        learning=learning,
        detector=services.regions,
    )
    timer.begin("S5")
    cls = classify(
        page,
        ocr,
        regions,
        services.senders,
        vision=vision,
        logo=logo,
        crop=_png_cropper(services) if vision is not None else None,
        barcodes=barcodes,
        config=cfg.classify,
    )
    spec = services.senders.get(cls.sender) if cls.sender is not None else None
    if spec is not None:
        # The sender is known only after S5 and decides which commitment labels and learned
        # hints apply, so the regions are found again with it.
        timer.begin("S4")
        regions = detect_regions(
            page,
            ocr,
            barcodes,
            settings=settings,
            sender=spec,
            learning=learning,
            detector=services.regions,
        )
    timer.begin("S6")
    candidates = generate_candidates(
        ocr,
        barcodes,
        regions,
        spec=spec,
        page_role=cls.page_role,
        identity=cfg.identity,
        lab=LabInputs(settings=settings, clock=services.clock, size=page_size(page)),
    )
    evaluations = _decide_fields(page, cls, spec, candidates, measurement, services, timer)
    escalation = EscalationStats()
    if not settings.local_only and services.vision is not None and cls.page_role != "blank":
        timer.begin("S9")
        escalated = escalate(
            evaluations,
            page=page,
            regions=regions,
            page_role=cls.page_role,
            spec=spec,
            vision=services.vision,
            imaging=services.imaging,
            storage=services.storage,
            clock=services.clock,
            settings=settings,
            weights=cfg.weights,
            scoring=cfg.scoring,
            measurement=measurement,
            config=cfg.escalation,
            prompt_version=cfg.classify.prompt_version,
            cloud_allowed=cloud_allowed,
        )
        evaluations, escalation = escalated.evaluations, escalated.stats
    return PageOutcome(
        page_id=raw.page_id,
        label=raw.label,
        source_file=raw.source_file,
        page_index=raw.page_index,
        status="done",
        page_role=cls.page_role,
        sender=cls.sender,
        corrections=list(page.corrections_applied),
        durations_ms=timer.millis(),
        evaluations=evaluations,
        escalation=escalation,
    )


def _failed(raw: PageRaw, code: str) -> PageOutcome:
    return PageOutcome(
        page_id=raw.page_id,
        label=raw.label,
        source_file=raw.source_file,
        page_index=raw.page_index,
        status="failed",
        error_code=code,
    )


def iter_outcomes(
    raws: Sequence[PageRaw],
    *,
    services: PipelineServices,
    progress: Callable[[PageRaw, str], None] | None = None,
    cloud_consent: bool = False,
) -> Iterator[PageOutcome]:
    """Process pages in order, yielding each outcome as soon as it exists so only one page is
    ever held. A page that raises becomes a `failed` outcome carrying a stable code (the
    exception's own for a SafeLineError, `INTERNAL` otherwise) and the batch continues.
    `cloud_consent` is the job's per-job consent to send crops of unknown documents (§8.5)."""
    interval = services.settings.gc_interval_pages
    for n, raw in enumerate(raws, start=1):

        def announce(stage: str, page: PageRaw = raw) -> None:
            if progress is not None:
                progress(page, stage)

        try:
            outcome = process_page(
                raw, services=services, progress=announce, cloud_consent=cloud_consent
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, SafeLineError) else "INTERNAL"
            log.warning(
                "page_failed",
                page_id=raw.page_id,
                stage="pipeline",
                code=code,
                exc_type=type(exc).__name__,
            )
            outcome = _failed(raw, code)
        yield outcome
        if n % interval == 0:
            gc.collect()


Assembler = Callable[[Sequence[PageOutcome]], Assembly]
Labeler = Callable[[PageRaw], str | None]


def apply_labels(raws: Sequence[PageRaw], label_for: Labeler | None) -> list[PageRaw]:
    """Pages carrying the label `label_for` gives them (e.g. A3 for page 3 of a sample PDF).
    A page keeps its own label when `label_for` returns None."""
    if label_for is None:
        return list(raws)
    return [
        r.model_copy(update={"label": label}) if (label := label_for(r)) is not None else r
        for r in raws
    ]


def build_report(
    job_id: str,
    outcomes: Sequence[PageOutcome],
    *,
    started_at: datetime,
    finished_at: datetime,
    row_count: int,
    page_count: int,
    intake_ms: int,
    cfg: PipelineConfig,
) -> RunReport:
    """Counters only: no value, name, ID or date ever enters the report (project rule §7).
    `fields_closed_local` counts fields that are not level D and did not leave the machine."""
    results = [r for o in outcomes for r in o.results.values()]
    levels = Counter(r.level.value for r in results)
    closed = sum(1 for r in results if r.level is not ConfidenceLevel.D and not r.escalated)
    durations: Counter[str] = Counter({"S0": intake_ms})
    for outcome in outcomes:
        durations.update(outcome.durations_ms)
    total = len(results)
    stats = [o.escalation for o in outcomes]
    failovers = list(dict.fromkeys(hop for s in stats for hop in s.failovers))
    return RunReport(
        job_id=job_id,
        started_at=started_at,
        finished_at=finished_at,
        page_count=page_count,
        row_count=row_count,
        fields_total=total,
        fields_closed_local=closed,
        fields_escalated=sum(1 for r in results if r.escalated),
        fields_flagged=levels.get(ConfidenceLevel.D.value, 0),
        tier1_calls=sum(s.calls for s in stats),
        tier2_calls=0,  # no full-page tier exists: only crops ever leave the machine
        local_close_rate=closed / total if total else 0.0,
        level_histogram={lv.value: levels.get(lv.value, 0) for lv in ConfidenceLevel},
        stage_durations_ms=dict(durations),
        corrections_histogram=dict(Counter(c for o in outcomes for c in o.corrections)),
        cost=CostCounters(
            provider_calls=sum(s.calls for s in stats),
            cache_hits=sum(s.cache_hits for s in stats),
            input_tokens=sum(s.input_tokens for s in stats),
            output_tokens=sum(s.output_tokens for s in stats),
            estimated_usd=round(sum(s.estimated_usd for s in stats), 6),
        ),
        provider_failovers=failovers,
        engine_version=ENGINE_VERSION,
        prompt_version=cfg.classify.prompt_version,
    )


def _error(code: str, page_id: str | None, *, recoverable: bool) -> SseErrorEvent:
    data = SseErrorData(code=code, page_id=page_id, recoverable=recoverable)
    return SseErrorEvent(event="error", data=data)


def _done(status: JobStatus, flagged: int, started: float) -> SseDoneEvent:
    ms = int((time.perf_counter() - started) * 1000)
    return SseDoneEvent(
        event="done", data=SseDoneData(status=status, flagged=flagged, duration_ms=ms)
    )


def final_status(
    report: RunReport, outcomes: Sequence[PageOutcome], errors: Sequence[str]
) -> JobStatus:
    """`failed` when no page was read; `needs_review` when any field is level D or any page
    failed; otherwise `confirmed` (PLAN §4 S11)."""
    if not any(o.status == "done" for o in outcomes):
        return JobStatus.failed
    return JobStatus.needs_review if report.fields_flagged or errors else JobStatus.confirmed


async def run_job(
    job: JobRecord,
    uploads: Sequence[UploadedBlob],
    c: PipelineServices,
    *,
    on_event: Callable[[SseEvent], None] | None = None,
    assemble: Assembler | None = None,
    label_for: Labeler | None = None,
    cloud_consent: bool = False,
) -> JobRecord:
    """S0..S9 for a batch, then rows, report and a final status.

    Emits `page` (each stage, then done or failed), `counters` (per page), `error` (per failed
    page), `row` (per row) and `done`, in that order. Never raises for a single-page failure:
    the page is recorded in `job.errors` and the batch continues. An upload that S0 refuses
    outright (unsupported, too large, encrypted) ends the job as `failed` with its code in
    `errors`. CPU-bound work runs in a worker thread, one page at a time, so the event loop
    stays free. `cloud_consent` lets crops of documents that are not known samples reach a
    provider (PLAN §8.5); it defaults to False. `assemble` groups pages into rows; the default is the interim one-row-per-page
    assembler until S10 (T20) exists. Artefacts are left in storage for review; the TTL purge
    (T22) removes them."""
    emit: Callable[[SseEvent], None] = on_event or (lambda event: None)
    started = time.perf_counter()
    started_at = c.clock.now()
    try:
        intaken = await asyncio.to_thread(
            intake,
            uploads,
            settings=c.settings,
            documents=c.documents,
            storage=c.storage,
            job_id=job.job_id,
        )
    except SafeLineError as exc:
        log.warning("job_refused", job_id=job.job_id, stage="S0", code=exc.code)
        emit(_error(exc.code, None, recoverable=False))
        emit(_done(JobStatus.failed, 0, started))
        return job.model_copy(
            update={"status": JobStatus.failed, "errors": [*job.errors, exc.code]}
        )
    intake_ms = int((time.perf_counter() - started) * 1000)

    raws = apply_labels(intaken.pages, label_for)
    errors = list(job.errors)
    for failure in intaken.failures:
        where = failure.source_file
        if failure.page_index is not None:
            where = f"{where}#{failure.page_index + 1}"
        errors.append(f"{failure.code}:{where}")
        emit(_error(failure.code, None, recoverable=failure.recoverable))

    total = len(raws) + len(intaken.failures)
    loop = asyncio.get_running_loop()
    progress = {"done": len(intaken.failures), "closed": 0, "crops": 0}

    def pct(stage: str) -> int:
        step = PAGE_STAGES.index(stage) if stage in PAGE_STAGES else 0
        return min(100, int(100 * (progress["done"] + step / len(PAGE_STAGES)) / max(1, total)))

    def on_stage(raw: PageRaw, stage: str) -> None:
        # Runs in the worker thread; call_soon_threadsafe keeps the events in order on the loop.
        data = SsePageData(
            page_id=raw.page_id, label=raw.label, stage=stage, status="running", pct=pct(stage)
        )
        loop.call_soon_threadsafe(emit, SsePageEvent(event="page", data=data))

    outcomes: list[PageOutcome] = []
    iterator = iter_outcomes(raws, services=c, progress=on_stage, cloud_consent=cloud_consent)
    labels = {r.page_id: r.label for r in raws}
    while (outcome := await asyncio.to_thread(next, iterator, None)) is not None:
        outcomes.append(outcome)
        progress["done"] += 1
        failed = outcome.status == "failed"
        progress["closed"] += sum(
            1 for r in outcome.results.values() if r.level is not ConfidenceLevel.D
        )
        progress["crops"] += outcome.escalation.crops_sent
        page_data = SsePageData(
            page_id=outcome.page_id,
            label=labels.get(outcome.page_id),
            stage=PAGE_STAGES[-1],
            status="failed" if failed else "done",
            pct=pct("S1"),
        )
        emit(SsePageEvent(event="page", data=page_data))
        if failed:
            errors.append(f"{outcome.error_code}:{outcome.page_id}")
            emit(_error(outcome.error_code or "INTERNAL", outcome.page_id, recoverable=True))
        counters = SseCountersData(
            pages_done=progress["done"],
            pages_total=total,
            fields_closed_local=progress["closed"],
            crops_sent=progress["crops"],
        )
        emit(SseCountersEvent(event="counters", data=counters))

    rows, groups, orphans = (assemble or one_row_per_page)(outcomes)
    for row in rows:
        flagged_fields = [k for k, f in row.fields.items() if f.level is ConfidenceLevel.D]
        row_data = SseRowData(
            row_id=row.row_id, row_level=row.row_level, flagged_fields=flagged_fields
        )
        emit(SseRowEvent(event="row", data=row_data))

    report = build_report(
        job.job_id,
        outcomes,
        started_at=started_at,
        finished_at=c.clock.now(),
        row_count=len(rows),
        page_count=total,
        intake_ms=intake_ms,
        cfg=c.pipeline,
    )
    status = final_status(report, outcomes, errors)
    emit(_done(status, report.fields_flagged, started))
    return job.model_copy(
        update={
            "status": status,
            "page_count": total,
            "pages_done": progress["done"],
            "rows": rows,
            "groups": groups,
            "orphan_page_ids": orphans,
            "report": report,
            "errors": errors,
        }
    )
