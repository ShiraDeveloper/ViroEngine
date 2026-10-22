"""S9 - resolve low-score fields by sending crops to a vision model (PLAN §4 S9, §6, T18).

One request per page carries one crop per unresolved field, the code's own candidates (so the
model chooses rather than invents) and, only when a field genuinely needs page context, a
redacted 512 px thumbnail. The validated answer is merged as `Candidate`s and S8 runs again: the
model never assigns its own confidence, and a lone cloud read is never better than level D.

Privacy gates, in order: `LOCAL_ONLY` (no vision port is ever touched); a non-sample document
without consent (PLAN §8.5: fields that would have escalated are flagged `providers_exhausted`
and nothing is sent); a `clinical` crop is a bug and raises AssertionError. A provider failure is
never fatal: `ProviderExhausted` flags the affected fields and the run continues.

Pure with respect to I/O: pixels, the provider and the clock are reached through ports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import Field
import structlog

from backend.core.errors import ProviderExhausted, SafeLineError
from backend.core.geometry import area, page_size
from backend.core.models import (
    Box,
    Candidate,
    ConfidenceLevel,
    CropPayload,
    FieldKey,
    FieldResult,
    NormalizedPage,
    PageMeasurement,
    ReaderName,
    ReadingTrailStep,
    Region,
    RegionKind,
    SenderSpec,
    SourceKind,
    Strict,
    VisionRequest,
    VisionResponse,
)
from backend.core.pipeline.s8_score import FieldEvaluation, evaluate_field
from backend.core.ports.clock import ClockPort
from backend.core.ports.imaging import ImagingPort
from backend.core.ports.storage import StoragePort
from backend.core.ports.vision_llm import VisionLLMPort
from backend.core.scoring import ScoringConfig, ScoringWeights, reason_text
from backend.core.vision_schema import EXTRACTION_SCHEMA
from backend.settings import Settings

log = structlog.get_logger()

# Structural: the redaction fill. Mid grey cannot be mistaken for ink or paper, and the prompt
# tells the model that grey blocks are removed regions (PLAN Appendix A).
REDACTION_GRAY: Final = 128

# Structural: the region kind each field is read from when no candidate located it (PLAN §4 S4).
_REGION_FOR_FIELD: Final[dict[FieldKey, RegionKind]] = {
    FieldKey.central_number: RegionKind.central_sticker,
    FieldKey.receipt_date: RegionKind.receipt_stamp,
    FieldKey.national_id: RegionKind.patient_box,
    FieldKey.first_name: RegionKind.patient_box,
    FieldKey.last_name: RegionKind.patient_box,
    FieldKey.commitment_number: RegionKind.commitment_label,
}

# Structural: S5 owns the sender (closed list, letterhead crop); S9 never re-asks for it.
_NOT_ESCALATED: Final = frozenset({FieldKey.sender})


class EscalationConfig(Strict):
    """S9 thresholds. `config/models.yaml: escalation` is authoritative; a test keeps these
    defaults equal to it."""

    # Margin around a crop, as a share of the page diagonal (Tesseract-style edge loss applies
    # to a model too: a digit touching the crop edge is read as a different digit).
    crop_pad_fraction: float = Field(default=0.02, ge=0.0, le=0.5)
    # Distinct code candidates passed to the model per field.
    max_candidates_per_field: int = Field(default=5, ge=0, le=10)
    # reader_confidence given to every cloud candidate. The model reports none (by design) and S8
    # weights this signal low; a neutral constant keeps it from deciding anything.
    cloud_reader_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # The page-context thumbnail is sent only when one of these fields is unresolved on one of
    # these page roles (PLAN §6.4: central_number on a sap_screen).
    context_fields: list[FieldKey] = Field(default_factory=lambda: [FieldKey.central_number])
    context_page_roles: list[str] = Field(default_factory=lambda: ["sap_screen"])
    # Planning prices for the cost counter (PLAN §6.5, ASSUMPTION: Gemini Flash list price).
    usd_per_million_input_tokens: float = Field(default=0.30, ge=0.0)
    usd_per_million_output_tokens: float = Field(default=2.50, ge=0.0)


class EscalationStats(Strict):
    """Counters of one page's S9 (no value, no PII). Feeds the RunReport and the SSE counters."""

    fields_sent: int = 0  # fields that were put in a request
    crops_sent: int = 0  # crops that left the machine (0 on a cache hit)
    calls: int = 0  # provider requests made (0 on a cache hit)
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_usd: float = 0.0
    failovers: list[str] = Field(default_factory=list)
    clinical_excluded: int = 0  # fields not sent because their crop touched a clinical region
    thumbnail_sent: bool = False
    exhausted: bool = False  # ProviderExhausted: every credential and provider failed
    gated: bool = False  # PLAN §8.5: unknown document without consent, nothing sent


class Escalation(Strict):
    """Output of S9: the page's evaluations (changed fields replaced) and its counters."""

    evaluations: dict[FieldKey, FieldEvaluation]
    stats: EscalationStats


@dataclass(frozen=True)
class Planned:
    """A field that will be sent, with where its crop comes from."""

    key: FieldKey
    box: Box  # padded
    region_kind: RegionKind


def needs_escalation(evaluation: FieldEvaluation, *, threshold: float) -> bool:
    """True when the combined score is below `threshold` or a hard rule failed. Reader
    self-confidence is never a trigger (project rule §5). Never raises."""
    result = evaluation.result
    hard_failed = any(v.kind == "hard" and not v.passed for v in result.validations)
    return result.score < threshold or hard_failed


def flag_for_review(evaluation: FieldEvaluation, code: str) -> FieldEvaluation:
    """The same reading forced to level D with a closed-set reason. Evidence is dropped so
    calibration treats the field as flagged whatever its score. Raises KeyError for a code
    outside the closed set."""
    flagged = evaluation.result.model_copy(
        update={"level": ConfidenceLevel.D, "reason": reason_text(code)}
    )
    return FieldEvaluation(result=flagged, signals=evaluation.signals, evidence=None)


def _overlaps(a: Box, b: Box) -> bool:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))) > 0.0


def _padded(box: Box, page: NormalizedPage, fraction: float) -> Box:
    """`box` grown by `fraction` of the page diagonal on every side, limited to the page."""
    width, height = page_size(page)
    pad_px = fraction * (width**2 + height**2) ** 0.5
    pad_y, pad_x = pad_px / height, pad_px / width
    return (
        max(0.0, box[0] - pad_y),
        max(0.0, box[1] - pad_x),
        min(1.0, box[2] + pad_y),
        min(1.0, box[3] + pad_x),
    )


def _locate(
    key: FieldKey, result: FieldResult, regions: Sequence[Region]
) -> tuple[Box, RegionKind] | None:
    """Where to cut the crop for `key`: the winning candidate's box, else any candidate's box,
    else the best region of the kind the field lives in. None when the field has no location:
    a field is never sent without knowing where it is."""
    fallback = _REGION_FOR_FIELD.get(key)
    if fallback is None:
        return None
    boxed = [c for c in result.candidates if c.box is not None]
    if boxed:  # the winning reading's box first, any other located reading otherwise
        located = min(boxed, key=lambda c: c.box != result.box)
        assert located.box is not None
        return located.box, located.region or fallback
    kind_regions = [r for r in regions if r.kind is fallback]
    if not kind_regions:
        return None
    best = max(kind_regions, key=lambda r: r.score)
    return best.box, best.kind


def _plan(
    evaluations: Mapping[FieldKey, FieldEvaluation],
    *,
    regions: Sequence[Region],
    page: NormalizedPage,
    threshold: float,
    config: EscalationConfig,
) -> tuple[list[Planned], int]:
    """(fields to send with their padded crop boxes, fields excluded for touching a clinical
    region). The clinical test uses the padded box, which is what would actually be sent."""
    clinical = [r.box for r in regions if r.kind is RegionKind.clinical]
    planned: list[Planned] = []
    excluded = 0
    ordered = sorted(evaluations.items(), key=lambda kv: (kv[1].result.score, kv[0].value))
    for key, evaluation in ordered:
        if key in _NOT_ESCALATED or not needs_escalation(evaluation, threshold=threshold):
            continue
        located = _locate(key, evaluation.result, regions)
        if located is None:
            continue
        box, kind = located
        padded = _padded(box, page, config.crop_pad_fraction)
        if any(_overlaps(padded, c) for c in clinical):
            excluded += 1
            continue
        planned.append(Planned(key=key, box=padded, region_kind=kind))
    return planned, excluded


def _needs_context(
    planned: Sequence[Planned], *, page_role: str | None, config: EscalationConfig
) -> bool:
    return page_role in config.context_page_roles and any(
        p.key in config.context_fields for p in planned
    )


def _redacted_thumbnail(
    page: NormalizedPage,
    regions: Sequence[Region],
    *,
    imaging: ImagingPort,
    storage: StoragePort,
    max_side: int,
) -> bytes | None:
    """512 px layout thumbnail with every clinical box and the patient box filled BEFORE the
    downscale, so no glyph survives the resample. None when the page cannot be read or encoded
    (the request then goes without context)."""
    data = storage.get(page.color)
    if not data:
        return None
    hidden = [r.box for r in regions if r.kind in (RegionKind.clinical, RegionKind.patient_box)]
    try:
        masked = imaging.fill_boxes(data, hidden, gray_level=REDACTION_GRAY)
        return imaging.thumbnail(masked.data, max_side=max_side).data
    except SafeLineError as exc:
        log.warning("thumbnail_failed", page_id=page.raw.page_id, code=exc.code)
        return None


def _candidate_strings(result: FieldResult, limit: int, *, barcode: bool) -> list[str]:
    """Distinct values the code already found for the field; barcode-decoded ones or the rest."""
    seen: list[str] = []
    for c in result.candidates:
        if (c.source is SourceKind.barcode) is not barcode:
            continue
        value = " ".join(c.value.split())
        if value and value not in seen:
            seen.append(value)
    return seen[:limit]


def build_request(
    evaluations: Mapping[FieldKey, FieldEvaluation],
    planned: Sequence[Planned],
    *,
    page: NormalizedPage,
    regions: Sequence[Region],
    page_role: str | None,
    spec: SenderSpec | None,
    imaging: ImagingPort,
    storage: StoragePort,
    settings: Settings,
    config: EscalationConfig,
    prompt_version: str,
) -> VisionRequest | None:
    """The one request for the page: a crop per planned field, the code's candidates, the
    sender's label synonyms and, when needed and allowed, the redacted thumbnail.

    Raises AssertionError if any crop is `clinical` (a bug, never a runtime condition). Returns
    None when no crop could be cut."""
    source = storage.get(page.color)
    crops: list[CropPayload] = []
    cut: dict[Box, bytes] = {}
    for plan in planned:
        if plan.region_kind is RegionKind.clinical:
            raise AssertionError("a clinical region must never be sent to a provider")
        try:
            if plan.box not in cut:
                cut[plan.box] = imaging.crop(source, plan.box).data
        except SafeLineError as exc:
            log.warning(
                "crop_failed", page_id=page.raw.page_id, field=plan.key.value, code=exc.code
            )
            continue
        crops.append(
            CropPayload(
                field=plan.key, png=cut[plan.box], region_kind=plan.region_kind, box=plan.box
            )
        )
    if not crops:
        return None
    sent = [c.field for c in crops]
    thumb = None
    if settings.escalation_send_thumbnail and _needs_context(
        [p for p in planned if p.key in sent], page_role=page_role, config=config
    ):
        thumb = _redacted_thumbnail(
            page, regions, imaging=imaging, storage=storage, max_side=settings.thumb_max_side
        )
    limit = config.max_candidates_per_field
    results = {k: evaluations[k].result for k in sent}
    return VisionRequest(
        job_id=page.raw.image.job_id,
        page_id=page.raw.page_id,
        task="fields",
        page_thumb=thumb,
        crops=crops,
        sender=spec.canonical if spec is not None else None,
        label_synonyms=list(spec.commitment_labels)
        if spec is not None and FieldKey.commitment_number in sent
        else [],
        candidates={k: _candidate_strings(r, limit, barcode=False) for k, r in results.items()},
        barcode_candidates=[
            v for r in results.values() for v in _candidate_strings(r, limit, barcode=True)
        ],
        prompt_version=prompt_version,
        response_schema=EXTRACTION_SCHEMA,
    )


def _cloud_candidates(
    key: FieldKey,
    response: VisionResponse,
    request: VisionRequest,
    prior: FieldResult,
    config: EscalationConfig,
) -> list[Candidate]:
    """The model's values for `key` as candidates. The source is what the code knew about the
    field (never the model's claim about it) and the confidence is a neutral constant."""
    answer = response.answers.get(key)
    crop = next(c for c in request.crops if c.field is key)
    if answer is None:
        return []
    source = prior.source if prior.candidates else SourceKind.unknown
    return [
        Candidate(
            value=value,
            reader=response.provider,
            source=source,
            box=crop.box,
            reader_confidence=config.cloud_reader_confidence,
            label_distance=None,
            region=crop.region_kind,
            notes=["cloud_read", *(f"alt:{a}" for a in answer.alts)],
        )
        for value in answer.values
    ]


def _merge(
    request: VisionRequest,
    response: VisionResponse,
    evaluations: Mapping[FieldKey, FieldEvaluation],
    *,
    spec: SenderSpec | None,
    clock: ClockPort,
    weights: ScoringWeights,
    scoring: ScoringConfig,
    measurement: PageMeasurement,
    config: EscalationConfig,
) -> dict[FieldKey, FieldEvaluation]:
    """Add the cloud reads of every requested field and run S8 again on the combined list."""
    merged: dict[FieldKey, FieldEvaluation] = {}
    for crop in request.crops:
        key = crop.field
        prior = evaluations[key].result
        new = _cloud_candidates(key, response, request, prior, config)
        step = ReadingTrailStep(
            stage="S9",
            reader=response.provider,
            outcome="value" if new else "no_value",
            value=new[0].value if new else None,
            reader_confidence=None,
            duration_ms=response.duration_ms,
            cloud=True,
            tier=1,
            page_sent=False,
        )
        merged[key] = evaluate_field(
            key,
            [*prior.candidates, *new],
            spec=spec,
            clock=clock,
            weights=weights,
            config=scoring,
            measurement=measurement,
            trail=[*prior.trail, step],
        )
    return merged


def _cost(response: VisionResponse, config: EscalationConfig) -> float:
    """Planning cost of one real Gemini call; cached answers and free fallbacks cost nothing."""
    if response.cache_hit or response.provider is not ReaderName.gemini:
        return 0.0
    return (
        response.input_tokens * config.usd_per_million_input_tokens
        + response.output_tokens * config.usd_per_million_output_tokens
    ) / 1_000_000


def escalate(
    evaluations: Mapping[FieldKey, FieldEvaluation],
    *,
    page: NormalizedPage,
    regions: Sequence[Region],
    page_role: str | None,
    spec: SenderSpec | None,
    vision: VisionLLMPort | None,
    imaging: ImagingPort,
    storage: StoragePort,
    clock: ClockPort,
    settings: Settings,
    weights: ScoringWeights,
    scoring: ScoringConfig,
    measurement: PageMeasurement,
    config: EscalationConfig,
    prompt_version: str,
    cloud_allowed: bool,
) -> Escalation:
    """Resolve the page's low-score fields with ONE provider request.

    `cloud_allowed` is False for a document that is not a known sample and has no consent
    (PLAN §8.5): the fields that would have escalated become level D `providers_exhausted` and
    nothing is sent. `LOCAL_ONLY` or a missing `vision` port skips the stage with the
    evaluations untouched. A `ProviderExhausted` flags the requested fields the same way and
    returns normally. Raises AssertionError for a clinical crop. Other errors propagate to the
    orchestrator, which fails only this page."""
    unchanged = Escalation(evaluations=dict(evaluations), stats=EscalationStats())
    if settings.local_only or vision is None:
        return unchanged
    planned, excluded = _plan(
        evaluations,
        regions=regions,
        page=page,
        threshold=settings.escalation_score_threshold,
        config=config,
    )
    if not planned:
        return Escalation(
            evaluations=dict(evaluations), stats=EscalationStats(clinical_excluded=excluded)
        )
    if not cloud_allowed:
        flagged = {
            p.key: flag_for_review(evaluations[p.key], "providers_exhausted") for p in planned
        }
        log.info("escalation_gated", page_id=page.raw.page_id, stage="S9", fields=len(planned))
        return Escalation(
            evaluations={**evaluations, **flagged},
            stats=EscalationStats(clinical_excluded=excluded, gated=True),
        )
    request = build_request(
        evaluations,
        planned,
        page=page,
        regions=regions,
        page_role=page_role,
        spec=spec,
        imaging=imaging,
        storage=storage,
        settings=settings,
        config=config,
        prompt_version=prompt_version,
    )
    if request is None:
        return Escalation(
            evaluations=dict(evaluations), stats=EscalationStats(clinical_excluded=excluded)
        )
    requested = [c.field for c in request.crops]
    try:
        response = vision.extract(request)
    except ProviderExhausted as exc:
        flagged = {k: flag_for_review(evaluations[k], "providers_exhausted") for k in requested}
        log.warning(
            "providers_exhausted",
            job_id=request.job_id,
            page_id=request.page_id,
            stage="S9",
            fields=len(requested),
        )
        return Escalation(
            evaluations={**evaluations, **flagged},
            stats=EscalationStats(
                fields_sent=len(requested),
                failovers=list(exc.failovers),
                clinical_excluded=excluded,
                exhausted=True,
            ),
        )
    merged = _merge(
        request,
        response,
        evaluations,
        spec=spec,
        clock=clock,
        weights=weights,
        scoring=scoring,
        measurement=measurement,
        config=config,
    )
    real_call = not response.cache_hit
    log.info(
        "stage_done",
        job_id=request.job_id,
        page_id=request.page_id,
        stage="S9",
        fields=len(requested),
        crops=len(request.crops),
        cache_hit=response.cache_hit,
        provider=response.provider.value,
        duration_ms=response.duration_ms,
    )
    return Escalation(
        evaluations={**evaluations, **merged},
        stats=EscalationStats(
            fields_sent=len(requested),
            crops_sent=len(request.crops) if real_call else 0,
            calls=1 if real_call else 0,
            cache_hits=0 if real_call else 1,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_usd=_cost(response, config),
            failovers=list(response.failovers),
            clinical_excluded=excluded,
            thumbnail_sent=request.page_thumb is not None,
        ),
    )


__all__ = [
    "REDACTION_GRAY",
    "Escalation",
    "EscalationConfig",
    "EscalationStats",
    "build_request",
    "escalate",
    "flag_for_review",
    "needs_escalation",
]
