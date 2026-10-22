"""Vision-first engine (docs/decisions/ADR-017): read whole pages with a vision model, re-read
every value blind from a zoomed crop, and let code decide the confidence.

Per batch of a few pages:
1. Barcode sweep per page (local, ~1 s): decoded payloads are an independent reader for the
   numbers that carry a barcode.
2. Page read: the batch's page images go to the model in ONE request, with a strict schema whose
   sender is an enumeration of the closed list. The answer is validated before any value is used.
3. Blind re-read: a zoomed crop around each value that no barcode confirmed is read again in ONE
   request, by a model that is NOT shown the first answer. Two readings that agree are two readers.
4. S8 scores every field exactly as in the cascade: the Israeli ID check digit and the calendar
   are hard gates, barcode + print agreement is level A, two agreeing readers level B, and a lone
   or disputed reading is level D with its reason - shown, never silently accepted.

Batching exists because the free tier allows about 20 requests per model per day: two requests
per batch of three pages reads 30 forms in 20 requests instead of 60. The model never sets a
confidence. A page the provider chain cannot read comes back as None and the orchestrator runs the
local cascade on it instead, so a run never stops on the cloud side.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import time
from typing import Final, cast, get_args

import structlog

from backend.core.errors import ProviderExhausted, SafeLineError
from backend.core.learning import with_learned
from backend.core.models import (
    REASON_TEXT,
    VISION_TEXT_MAX,
    BarcodeHit,
    Box,
    Candidate,
    ConfidenceLevel,
    CropPayload,
    FieldKey,
    FieldStat,
    PageAnswer,
    PageRaw,
    ReaderName,
    ReadingTrailStep,
    RegionKind,
    SourceKind,
    VisionRequest,
    VisionResponse,
)
from backend.core.normalize import normalize_central, normalize_commitment, normalize_name
from backend.core.pipeline.outcome import PageOutcome
from backend.core.pipeline.s5_classify import PageRole
from backend.core.pipeline.s8_score import FieldEvaluation, evaluate_field
from backend.core.pipeline.s9_escalate import EscalationStats, flag_for_review
from backend.core.ports.barcode import BarcodePort
from backend.core.ports.clock import ClockPort
from backend.core.ports.imaging import ImagingPort
from backend.core.ports.learning import LearningStorePort
from backend.core.ports.ocr import OcrPort
from backend.core.ports.senders import SenderIndexPort
from backend.core.ports.storage import StoragePort
from backend.core.ports.vision_llm import VisionLLMPort
from backend.core.scoring import (
    CROSS_FIELD_NOTE,
    SECOND_READ_NOTE,
    THIRD_READ_NOTE,
    ScoringConfig,
    ScoringWeights,
)
from backend.core.validators import (
    date_not_future,
    israeli_id_ok,
    pad_id,
    parse_il_date,
    strip_surplus_leading_zeros,
)
from backend.core.vision_schema import VERIFY_SCHEMA, name_label_clear, page_schema
from backend.settings import Settings

log = structlog.get_logger()

# Structural: the region each field is read from, recorded on its candidates and crops.
_REGION: Final[dict[FieldKey, RegionKind]] = {
    FieldKey.first_name: RegionKind.patient_box,
    FieldKey.last_name: RegionKind.patient_box,
    FieldKey.national_id: RegionKind.patient_box,
    FieldKey.central_number: RegionKind.central_sticker,
    FieldKey.receipt_date: RegionKind.receipt_stamp,
    FieldKey.commitment_number: RegionKind.commitment_label,
    FieldKey.sender: RegionKind.letterhead,
}
# Structural: a candidate note carrying the printed label the value was read beside, shown on the
# review screen as "found beside ...".
LABEL_NOTE_PREFIX: Final = "label:"
# Structural: the only model page roles that may attach a page to the form before it.
_JOINING_ROLES: Final = frozenset({"continuation", "blank"})
# Structural: fields whose disputed reading is shown (flagged) instead of an empty cell.
_SHOWN_WHEN_DISPUTED: Final = (FieldKey.first_name, FieldKey.last_name)
# Structural: an ID read once is shown (level C) only with at least this many significant digits:
# a short reading left-padded with zeros passes the check digit one time in ten.
_MIN_ID_DIGITS: Final = 7
# Structural: shorter numbers (a 3-digit code) legitimately repeat across fields.
_MIN_SHARED_DIGITS: Final = 5
# Structural: fields whose value a decoded barcode can confirm.
_BARCODE_FIELDS: Final = (FieldKey.central_number, FieldKey.commitment_number, FieldKey.national_id)
# Structural: a cloud reading reports no confidence; S8 weights this neutral constant low.
_CLOUD_CONFIDENCE: Final = 0.5
_BARCODE_CONFIDENCE: Final = 0.95
# Structural: the smallest margin around a verification crop, as a share of the page height and
# width. Kept tight vertically so the line above a name (another label) stays out of the crop.
_MIN_PAD_Y: Final = 0.004
_MIN_PAD_X: Final = 0.012
# Structural: crops wider than this many pixels are downscaled before they are sent.
_CROP_MAX_SIDE: Final = 1600
# Structural: barcode payloads named to the model per batch (they only help it read stickers).
_MAX_BARCODES: Final = 30
_MS: Final = 1000.0

Progress = Callable[[PageRaw, str], None]


@dataclass(frozen=True)
class VisionServices:
    """The ports and knobs the vision-first engine reads through."""

    settings: Settings
    vision: VisionLLMPort
    verify: VisionLLMPort
    barcode: BarcodePort
    imaging: ImagingPort
    storage: StoragePort
    senders: SenderIndexPort
    clock: ClockPort
    weights: ScoringWeights
    scoring: ScoringConfig
    prompt_version: str
    usd_per_million: tuple[float, float] = (0.30, 2.50)
    # Local orientation detection (Tesseract OSD, ~1.5 s): a page is turned upright before the
    # model sees it - rotated SAP screenshots read far worse sideways. None skips it.
    ocr: OcrPort | None = None
    # What review taught (counts, labels, shapes - never values). None: nothing is learned.
    learning: LearningStorePort | None = None


@dataclass
class _Page:
    """One page of a batch while it is being read."""

    raw: PageRaw
    pixels: bytes
    hits: list[BarcodeHit]
    image: bytes
    answer: PageAnswer | None = None
    reads: dict[FieldKey, list[Candidate]] = field(default_factory=dict)
    trails: dict[FieldKey, list[ReadingTrailStep]] = field(default_factory=dict)
    to_verify: dict[FieldKey, Box] = field(default_factory=dict)
    crops_sent: int = 0
    started: float = 0.0


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _same_number(key: FieldKey, a: str, b: str) -> bool:
    """True when a barcode payload and a read value are the same number for `key`."""
    if key is FieldKey.national_id:
        da, db = _digits(a), _digits(b)
        return bool(da) and (pad_id(da) or strip_surplus_leading_zeros(da)) == (
            pad_id(db) or strip_surplus_leading_zeros(db)
        )
    norm = normalize_central if key is FieldKey.central_number else normalize_commitment
    na, nb = norm(a), norm(b)
    return na is not None and na == nb


def _source(model_source: SourceKind) -> SourceKind:
    """The model's view of how a value is written. Digits it read under a barcode are print: only
    a decoded symbol (zxing) is a barcode reading."""
    return SourceKind.printed if model_source is SourceKind.barcode else model_source


def _canonical(senders: SenderIndexPort, value: str) -> str | None:
    """The closed-list name for a model's sender answer, or None. Never invents one."""
    if senders.get(value) is not None:
        return value
    match = senders.match(value)
    return match.canonical if match is not None else None


def _step(response: VisionResponse, value: str | None, *, page_sent: bool) -> ReadingTrailStep:
    return ReadingTrailStep(
        stage="S9",
        reader=response.provider,
        outcome="value" if value else "no_value",
        value=value,
        reader_confidence=None,
        duration_ms=response.duration_ms,
        cloud=True,
        tier=2 if page_sent else 1,
        page_sent=page_sent,
    )


def _padded(box: Box, margin: float) -> Box:
    """`box` grown by `margin` of its own size (at least the minimum pad), limited to the page."""
    pad_y = max((box[2] - box[0]) * margin, _MIN_PAD_Y)
    pad_x = max((box[3] - box[1]) * margin, _MIN_PAD_X)
    return (
        max(0.0, box[0] - pad_y),
        max(0.0, box[1] - pad_x),
        min(1.0, box[2] + pad_y),
        min(1.0, box[3] + pad_x),
    )


def _cloud_candidates(
    key: FieldKey,
    values: Sequence[str],
    response: VisionResponse,
    *,
    source: SourceKind,
    box: Box | None,
    second: bool,
    senders: SenderIndexPort,
) -> list[Candidate]:
    out: list[Candidate] = []
    if key is FieldKey.commitment_number and len(values) > 1:
        # Several commitments share one union box; row assembly would merge candidates at one
        # place into one commitment, so each keeps no box and becomes its own row.
        box = None
    for value in values:
        text = _canonical(senders, value) if key is FieldKey.sender else value
        if not text:
            continue
        out.append(
            Candidate(
                value=text,
                reader=response.provider,
                source=source,
                box=box,
                reader_confidence=_CLOUD_CONFIDENCE,
                label_distance=None,
                region=_REGION[key],
                notes=["cloud_read", SECOND_READ_NOTE] if second else ["cloud_read"],
            )
        )
    return out


def _upright(raw: PageRaw, services: VisionServices) -> PageRaw:
    """The page turned upright when local OSD is sure it is not (the stored colour image is
    replaced, so every box, crop and evidence view refers to the upright page). Unchanged when
    OSD abstains or the rotation fails - the model can still read a sideways page, only worse."""
    if services.ocr is None:
        return raw
    rotation = services.ocr.detect_orientation(raw.image)
    if rotation not in (90, 180, 270):
        return raw
    try:
        turned = services.imaging.apply_operation(
            services.storage.get(raw.image), "rotate", angle_deg=float(rotation)
        )
    except SafeLineError as exc:
        log.warning("upright_failed", page_id=raw.page_id, code=exc.code)
        return raw
    image = services.storage.put_page_image(raw.image.job_id, raw.page_id, turned.data, "color")
    log.info("page_turned_upright", page_id=raw.page_id, rotation=rotation)
    return raw.model_copy(update={"image": image})


def _enhanced(raw: PageRaw, pixels: bytes, services: VisionServices) -> bytes:
    """The page with only the corrections its measurement calls for, for the model's eyes: a
    blurred fax is sharpened, uneven phone-photo light is evened out (CLAHE), fax grain is
    removed. A clean page is untouched; a failed operation keeps the previous image. Colour is
    kept - never binarised, the blue ink and faint stamps would be lost (PLAN §4 S2)."""
    settings = services.settings
    measured = services.imaging.measure_pixels(pixels)
    if not measured.measured:
        return pixels
    ops = []
    if measured.noise_score > settings.noise_threshold:
        ops.append("denoise")
    if measured.illumination_cv > settings.illumination_cv_threshold:
        ops.append("clahe")
    if measured.blur_score < settings.blur_threshold:
        ops.append("sharpen")
    for op in ops:
        try:
            pixels = services.imaging.apply_operation(pixels, op).data
        except SafeLineError:
            continue
    if ops:
        log.info("page_enhanced", page_id=raw.page_id, ops=ops)
    return pixels


def _prepare(raw: PageRaw, services: VisionServices) -> _Page:
    """Barcodes and the JPEG page image of one page. Raises SafeLineError for an unreadable
    image (the orchestrator then fails just this page)."""
    started = time.perf_counter()
    settings = services.settings
    raw = _upright(raw, services)
    pixels = services.storage.get(raw.image)
    hits = services.barcode.sweep(raw.image)
    pixels = _enhanced(raw, pixels, services)
    image = services.imaging.thumbnail(
        pixels, max_side=settings.vision_page_max_side, jpeg_quality=settings.vision_jpeg_quality
    ).data
    return _Page(raw=raw, pixels=pixels, hits=hits, image=image, started=started)


def _self_validated(key: FieldKey, values: Sequence[str], services: VisionServices) -> bool:
    """True when code alone can vouch for the reading, so no second request is spent on it: an ID
    whose check digit holds (a misread passes it 1 time in 10, and the confusable-repair rule
    still runs), a real date that is not in the future, a sender the closed list knows. Names and
    numbers without a barcode always get the blind second read."""
    if len(values) != 1:
        return False
    value = values[0]
    if key is FieldKey.national_id:
        padded = pad_id(value)
        return padded is not None and israeli_id_ok(padded)
    if key is FieldKey.receipt_date:
        parsed = parse_il_date(value)
        return parsed is not None and date_not_future(parsed, now=services.clock.now().date())
    return False  # names, numbers without a barcode and the sender always get a second read


def _from_address(name: str, address: str) -> bool:
    """True when every word of a "name" also appears in the patient's address line - the model
    read the street or the city as the name (A1: the address "שלמציון המלכה 241 אלעזר" was read
    as the names "שלמוביץ" and "אלעזר"). Words match up to a misread letter or two. Such a value
    is dropped, never shown as a name."""
    words = (normalize_name(name) or "").split()
    place = (normalize_name(address) or "").split()

    def near(word: str) -> bool:  # a misread of an address word is still the address
        slack = 1 if len(word) <= 4 else 2
        return any(_edit_distance(word, p) <= slack for p in place)

    return bool(words) and bool(place) and all(near(w) for w in words)


def _names_from_address(answer: PageAnswer) -> bool:
    """True when either name of a reading lies in the address line: the reader was looking at
    the address, so BOTH of its names are dropped (A1: first name "אלעזר" is the address's city,
    and the "last name" came from the street on the same line)."""
    names = (answer.answers.get(FieldKey.first_name), answer.answers.get(FieldKey.last_name))
    return any(_from_address(v, answer.address) for a in names if a for v in a.values)


def _first_reads(page: _Page, response: VisionResponse, services: VisionServices) -> None:
    """Candidates from the page read and the barcodes that confirm them; marks what to re-read."""
    answer = page.answer
    assert answer is not None
    for key in FieldKey:
        found = answer.answers.get(key)
        values = found.values if found is not None else []
        source = _source(found.source) if found is not None else SourceKind.unknown
        box = found.box if found is not None else None
        if key in _SHOWN_WHEN_DISPUTED and _names_from_address(answer):
            values = []
        cands = _cloud_candidates(
            key, values, response, source=source, box=box, second=False, senders=services.senders
        )
        if found is not None and found.note:
            label_note = f"{LABEL_NOTE_PREFIX}{found.note}"
            cands = [c.model_copy(update={"notes": [*c.notes, label_note]}) for c in cands]
        confirmed = 0
        if key in _BARCODE_FIELDS:
            for value in values:
                hit = next((h for h in page.hits if _same_number(key, h.payload, value)), None)
                if hit is None:
                    continue
                confirmed += 1
                cands.append(
                    Candidate(
                        value=hit.payload,
                        reader=ReaderName.zxing,
                        source=SourceKind.barcode,
                        box=hit.box,
                        reader_confidence=_BARCODE_CONFIDENCE,
                        label_distance=None,
                        region=_REGION[key],
                        notes=["barcode_confirms_read"],
                    )
                )
        page.reads[key] = cands
        page.trails[key] = [_step(response, values[0] if values else None, page_sent=True)]
        if cands and confirmed < len(values) and box is not None:
            # The page-mode second read sees the whole page anyway: checking every field against
            # it costs no request. A plausible date can still be misread (B5: 18-08 read as
            # 18-06, level C, unflagged), so nothing is waved through on plausibility alone.
            whole_page = services.settings.verify_mode == "page"
            if whole_page or not _self_validated(key, values, services):
                page.to_verify[key] = box


def _keep_verified(cands: Sequence[Candidate], seen: Sequence[str]) -> list[Candidate]:
    """Drop page-read commitments that the blind re-read of the same crop did not see, while it
    did see others: the model listed a header form id or print serial beside the real commitment
    (A1's 0046025783 next to 730578626). A barcode never rescues such a value - it proves digits,
    not that they are a commitment. Several commitments both readers saw all stay."""
    kept = {normalize_commitment(v) for v in seen}
    firsts = [c for c in cands if SECOND_READ_NOTE not in c.notes]
    if not any(normalize_commitment(c.value) in kept for c in firsts):
        return list(cands)  # the readers disagree on the value itself: S8 flags that, keep both
    return [
        c for c in cands if SECOND_READ_NOTE in c.notes or normalize_commitment(c.value) in kept
    ]


def _verify_request(
    pages: Sequence[_Page], services: VisionServices
) -> tuple[VisionRequest, list[tuple[_Page, FieldKey]]] | None:
    """One request with a crop per value still unconfirmed, across the batch, cut from the
    full-resolution pages; and which page and field each crop belongs to. None when there is
    nothing to re-read. The request carries no candidate: the second reader is blind."""
    crops: list[CropPayload] = []
    owners: list[tuple[_Page, FieldKey]] = []
    for page in pages:
        for key, box in page.to_verify.items():
            padded = _padded(box, services.settings.verify_crop_margin)
            try:
                piece = services.imaging.crop(page.pixels, padded)
                png = piece.data
                if max(piece.width, piece.height) > _CROP_MAX_SIDE:
                    png = services.imaging.thumbnail(png, max_side=_CROP_MAX_SIDE).data
            except SafeLineError as exc:
                log.warning(
                    "verify_crop_failed", page_id=page.raw.page_id, field=key.value, code=exc.code
                )
                continue
            crops.append(CropPayload(field=key, png=png, region_kind=_REGION[key], box=padded))
            owners.append((page, key))
    if not crops:
        return None
    first = pages[0].raw
    request = VisionRequest(
        job_id=first.image.job_id,
        page_id=first.page_id,
        task="verify",
        crops=crops,
        sender_options=[s.canonical for s in services.senders.specs],
        prompt_version=services.prompt_version,
        response_schema=VERIFY_SCHEMA,
    )
    return request, owners


def _stats(
    responses: Sequence[VisionResponse], fields: int, crops: int, usd: tuple[float, float]
) -> EscalationStats:
    """Counters of the requests a page took part in (no value, no PII). A batch's requests are
    booked on its first page so the run totals count each request once."""
    real = [r for r in responses if not r.cache_hit]
    cost = sum(
        (r.input_tokens * usd[0] + r.output_tokens * usd[1]) / 1_000_000
        for r in real
        if r.provider is ReaderName.gemini
    )
    return EscalationStats(
        fields_sent=fields,
        crops_sent=crops,
        calls=len(real),
        cache_hits=len(responses) - len(real),
        input_tokens=sum(r.input_tokens for r in responses),
        output_tokens=sum(r.output_tokens for r in responses),
        estimated_usd=cost,
        failovers=[f for r in responses for f in r.failovers],
        thumbnail_sent=False,
    )


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance; the strings here are a few characters long."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, start=1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _as_level_c(evaluation: FieldEvaluation, value: str) -> FieldEvaluation:
    result = evaluation.result.model_copy(
        update={"value": value, "display_value": value, "level": ConfidenceLevel.C, "reason": None}
    )
    return FieldEvaluation(result=result, signals=evaluation.signals, evidence=evaluation.evidence)


def _settle(key: FieldKey, evaluation: FieldEvaluation) -> FieldEvaluation:
    """Vision-first leniency, applied after S8 (ADR-017):

    - A value read once, by a strong model, that passed every hard rule (ID check digit, a real
      date, a sender from the closed list) is level C - shown, amber, not a "check me". S8's
      cascade rule (a lone handwritten read is D) was written for Tesseract, which invents
      handwriting; a vision model that read a check-digit-valid ID did not.
    - Two readings of a NAME that differ by one letter (a vowel letter, a final form) are one name: the
      page reading is kept at level C.
    Never touched: an empty field, a failed hard rule, two numbers or dates that disagree."""
    result = evaluation.result
    if result.level is not ConfidenceLevel.D:
        return evaluation
    if key is FieldKey.national_id and result.reason == REASON_TEXT["failed_id_check"]:
        # Both readers saw the same digits and they fail the check digit: the form itself is
        # wrong. Stay orange, but show what is written so the technician corrects it in place.
        firsts = {_digits(c.value) for c in result.candidates if SECOND_READ_NOTE not in c.notes}
        if len(firsts) == 1 and (value := next(iter(firsts))):
            shown = result.model_copy(update={"value": value, "display_value": value})
            return FieldEvaluation(result=shown, signals=evaluation.signals, evidence=None)
        return evaluation
    if any(v.kind == "hard" and not v.passed for v in result.validations):
        return evaluation
    if result.reason in (REASON_TEXT["handwriting_unverified"], REASON_TEXT["low_quality_region"]):
        short_id = len(_digits(result.value or "").lstrip("0")) < _MIN_ID_DIGITS
        if key is FieldKey.national_id and short_id:
            return evaluation  # a 6-digit reading padded with zeros passes the check too easily
        if result.value:
            return _as_level_c(evaluation, result.value)
        return evaluation
    if key in _SHOWN_WHEN_DISPUTED and result.reason == REASON_TEXT["readers_disagree"]:
        first = [c.value for c in result.candidates if SECOND_READ_NOTE not in c.notes]
        second = [c.value for c in result.candidates if SECOND_READ_NOTE in c.notes]
        if first and second:
            a, b = normalize_name(first[0]) or "", normalize_name(second[0]) or ""
            if a and b and _edit_distance(a, b) <= 1:
                return _as_level_c(evaluation, first[0])
    return evaluation


def _number_key(value: str) -> str:
    """Digits with leading zeros dropped: the same number written in two fields compares equal."""
    return _digits(value).lstrip("0")


def _flag_shared_numbers(evaluations: dict[FieldKey, FieldEvaluation]) -> None:
    """A number decided for one of the ID / central / commitment fields that another of them also
    decided is a mix-up (a commitment barcode taken for the central sticker, an ID read as a
    commitment): both fields go to level D `multiple_candidates`, in place. A barcode agreeing with
    the reading cannot clear this - it only proves the digits, not which field they belong to."""
    keyed: dict[str, list[FieldKey]] = {}
    for key in _BARCODE_FIELDS:
        result = evaluations[key].result
        if result.value is None or result.level is ConfidenceLevel.D:
            continue
        for part in result.value.split(";"):
            number = _number_key(part)
            if len(number) >= _MIN_SHARED_DIGITS:
                keyed.setdefault(number, []).append(key)
    for keys in keyed.values():
        if len(set(keys)) < 2:
            continue
        for key in set(keys):
            flagged = flag_for_review(evaluations[key], "multiple_candidates")
            noted = [
                c.model_copy(update={"notes": [*c.notes, CROSS_FIELD_NOTE]})
                for c in flagged.result.candidates
            ]
            evaluations[key] = FieldEvaluation(
                result=flagged.result.model_copy(update={"candidates": noted}),
                signals=flagged.signals,
                evidence=flagged.evidence,
            )


def _flag_twin_names(evaluations: dict[FieldKey, FieldEvaluation]) -> None:
    """The same word as first AND family name means one name was read into both fields: both are
    flagged `multiple_candidates`, in place, still showing the reading for the technician."""
    first = evaluations[FieldKey.first_name].result
    last = evaluations[FieldKey.last_name].result
    if not first.value or not last.value:
        return
    if normalize_name(first.value) != normalize_name(last.value):
        return
    for key in (FieldKey.first_name, FieldKey.last_name):
        flagged = flag_for_review(evaluations[key], "multiple_candidates")
        shown = flagged.result.model_copy(
            update={
                "value": evaluations[key].result.value,
                "display_value": evaluations[key].result.value,
            }
        )
        evaluations[key] = FieldEvaluation(result=shown, signals=flagged.signals, evidence=None)


def _flag_unlabelled_names(evaluations: dict[FieldKey, FieldEvaluation], page: _Page) -> None:
    """A name the page reader found beside a label that is not a name label goes to level D
    `name_label_unclear`, still showing the reading, in place. Agreement of a second reader does
    not clear it: both may have taken the same wrong text."""
    for key in _SHOWN_WHEN_DISPUTED:
        found = page.answer.answers.get(key) if page.answer is not None else None
        if found is None or not found.values or name_label_clear(found.note or ""):
            continue
        result = evaluations[key].result
        flagged = flag_for_review(evaluations[key], "name_label_unclear")
        shown = flagged.result.model_copy(
            update={"value": result.value, "display_value": result.value}
        )
        evaluations[key] = FieldEvaluation(result=shown, signals=flagged.signals, evidence=None)


def _show_first_reading(evaluation: FieldEvaluation) -> FieldEvaluation:
    """A disputed name stays level D with its reason, but shows the page reading instead of an
    empty cell, so the technician confirms or corrects it rather than retyping it. Numbers and
    dates are never treated this way: for them an empty flagged cell beats a disputed value."""
    result = evaluation.result
    if result.value is not None or result.reason != REASON_TEXT["readers_disagree"]:
        return evaluation
    first = next((c for c in result.candidates if SECOND_READ_NOTE not in c.notes), None)
    if first is None:
        return evaluation
    shown = result.model_copy(update={"value": first.value, "display_value": first.value})
    return FieldEvaluation(result=shown, signals=evaluation.signals, evidence=evaluation.evidence)


# Structural: the level-D reasons a third, zoomed reading can settle - two readers that disagree,
# or digits that fail the ID check (one dropped or misread digit is the usual cause).
_ARBITRATED: Final = frozenset({"readers_disagree", "failed_id_check", "multiple_candidates"})
# Structural: generous margin around the disputed value for the arbiter's zoomed crop.
_ARBITER_MARGIN: Final = 0.6


def _disputed(evaluations: dict[FieldKey, FieldEvaluation]) -> list[FieldKey]:
    """Fields worth a third reading: level D for an arbitrable reason, with a located value."""
    reasons = {REASON_TEXT[code] for code in _ARBITRATED}
    return [
        key
        for key, ev in evaluations.items()
        if key is not FieldKey.sender
        and ev.result.level is ConfidenceLevel.D
        and ev.result.reason in reasons
        and any(c.box is not None for c in ev.result.candidates)
    ]


def _arbitrate(page: _Page, keys: Sequence[FieldKey], services: VisionServices) -> None:
    """One request with a zoomed crop per disputed field, read blind by the primary chain; the
    values join the field as a third reader. Two of three agreeing then settle the field in S8;
    no majority leaves it orange. A provider failure changes nothing."""
    crops: list[CropPayload] = []
    for key in keys:
        box = next(c.box for c in page.reads[key] if c.box is not None)
        padded = _padded(box, _ARBITER_MARGIN)
        try:
            piece = services.imaging.crop(page.pixels, padded)
            png = piece.data
            if max(piece.width, piece.height) > _CROP_MAX_SIDE:
                png = services.imaging.thumbnail(png, max_side=_CROP_MAX_SIDE).data
        except SafeLineError:
            continue
        crops.append(CropPayload(field=key, png=png, region_kind=_REGION[key], box=padded))
    if not crops:
        return
    request = VisionRequest(
        job_id=page.raw.image.job_id,
        page_id=page.raw.page_id,
        task="verify",
        crops=crops,
        sender_options=[s.canonical for s in services.senders.specs],
        prompt_version=services.prompt_version,
        response_schema=VERIFY_SCHEMA,
    )
    try:
        third = services.vision.extract(request)
    except ProviderExhausted:
        return
    for crop, values in zip(request.crops, third.crop_values, strict=False):
        key = crop.field
        prior = page.reads[key][0].source if page.reads.get(key) else SourceKind.unknown
        for cand in _cloud_candidates(
            key, values, third, source=prior, box=crop.box, second=True, senders=services.senders
        ):
            page.reads[key].append(
                cand.model_copy(update={"notes": ["cloud_read", THIRD_READ_NOTE]})
            )
        page.trails[key].append(_step(third, values[0] if values else None, page_sent=False))


def _evaluate_all(page: _Page, services: VisionServices) -> dict[FieldKey, FieldEvaluation]:
    sender_values = [c.value for c in page.reads.get(FieldKey.sender, [])]
    spec = services.senders.get(sender_values[0]) if sender_values else None
    if services.learning is not None:
        spec = with_learned(spec, services.learning)  # learned shapes make s_pattern informative
    return {
        key: evaluate_field(
            key,
            page.reads.get(key, []),
            spec=spec,
            clock=services.clock,
            weights=services.weights,
            config=services.scoring,
            measurement=None,
            trail=page.trails.get(key, []),
        )
        for key in FieldKey
    }


def _outcome(
    page: _Page, services: VisionServices, stats: EscalationStats, cloud_ms: int
) -> PageOutcome:
    """S8 over everything read for the page, and the page's outcome."""
    t_score = time.perf_counter()
    sender_values = [c.value for c in page.reads.get(FieldKey.sender, [])]
    evaluations = _evaluate_all(page, services)
    disputed = _disputed(evaluations)
    if disputed:
        _arbitrate(page, disputed, services)
        evaluations = _evaluate_all(page, services)
    for key in FieldKey:
        evaluations[key] = _settle(key, evaluations[key])
    _flag_shared_numbers(evaluations)
    _flag_twin_names(evaluations)
    _flag_unlabelled_names(evaluations, page)
    _distrust(evaluations, evaluations[FieldKey.sender].result.value, services)
    for key in _SHOWN_WHEN_DISPUTED:
        evaluations[key] = _show_first_reading(evaluations[key])
    answer = page.answer
    role = answer.page_role if answer is not None else "primary_form"
    # A SAP screen or a lab report that the model read is a sample of its own (the 48 samples
    # bear this out): only an explicit continuation or a blank page may join the previous form.
    if role not in get_args(PageRole) or role not in _JOINING_ROLES:
        role = "primary_form"
    sender = evaluations[FieldKey.sender].result.value or (
        sender_values[0] if sender_values else None
    )
    durations = {
        "S3": int((t_score - page.started) * _MS) - cloud_ms,
        "S9": cloud_ms,
        "S8": int((time.perf_counter() - t_score) * _MS),
    }
    return PageOutcome(
        page_id=page.raw.page_id,
        label=page.raw.label,
        source_file=page.raw.source_file,
        page_index=page.raw.page_index,
        status="done",
        page_role=cast(PageRole, role),
        sender=sender,
        sha256=page.raw.sha256,
        color_image=page.raw.image,
        corrections=[],
        durations_ms={k: max(0, v) for k, v in durations.items()},
        evaluations=evaluations,
        escalation=stats,
    )


# Structural: review evidence needed before a statistic changes anything (one correction is an
# anecdote, three are a pattern - PLAN §7.3), and the correction rate from which a single reading
# of that field is no longer trusted.
_MIN_REVIEWED: Final = 3
_DISTRUST_RATE: Final = 0.34
_MAX_LEARNED_LINES: Final = 30


def _field_stats(services: VisionServices) -> dict[tuple[str, FieldKey], FieldStat]:
    if services.learning is None:
        return {}
    try:
        return {(s.sender, s.field): s for s in services.learning.field_stats()}
    except Exception:  # learning is an aid: a broken store never stops a read
        log.warning("learning_unavailable")
        return {}


def _learned_lines(services: VisionServices) -> list[str]:
    """What review taught, as short guidance lines for the model: where an institution puts a
    field, its usual shape, and which fields staff often had to correct. No value ever."""
    lines: list[str] = []
    for stat in _field_stats(services).values():
        if stat.total < _MIN_REVIEWED:
            continue
        parts = [f"{stat.sender} / {stat.field.value}: reviewed {stat.total}x"]
        if stat.label:
            parts.append(f'found beside "{stat.label}"')
        if stat.shape:
            parts.append(f"shape {stat.shape}")
        if stat.correction_rate >= _DISTRUST_RATE:
            parts.append(f"staff corrected it {stat.corrected}x - read it with extra care")
        lines.append(", ".join(parts)[:VISION_TEXT_MAX])
    return lines[:_MAX_LEARNED_LINES]


def _distrust(
    evaluations: dict[FieldKey, FieldEvaluation], sender: str | None, services: VisionServices
) -> None:
    """A single reading (level C) of a field that staff corrected often for this institution is
    not trusted: it goes to level D `often_corrected`, still showing the reading. Agreement of two
    readers or a barcode (A/B) is evidence of its own and is left alone. In place."""
    if not sender:
        return
    stats = _field_stats(services)
    for key, ev in evaluations.items():
        stat = stats.get((sender, key))
        if stat is None or stat.total < _MIN_REVIEWED or stat.correction_rate < _DISTRUST_RATE:
            continue
        if ev.result.level is not ConfidenceLevel.C:
            continue
        flagged = flag_for_review(ev, "often_corrected")
        shown = flagged.result.model_copy(
            update={"value": ev.result.value, "display_value": ev.result.value}
        )
        evaluations[key] = FieldEvaluation(result=shown, signals=flagged.signals, evidence=None)


def _read_pages(group: Sequence[_Page], services: VisionServices) -> VisionResponse | None:
    """One page-read request for `group`; sets each page's answer and first reads. None when no
    provider could answer (the pages keep no answer and fall back to the cascade)."""
    options = [s.canonical for s in services.senders.specs]
    payloads = [h.payload for p in group for h in p.hits]
    first = group[0].raw
    request = VisionRequest(
        job_id=first.image.job_id,
        page_id=first.page_id,
        task="page",
        page_images=[p.image for p in group],
        crops=[],
        sender_options=options,
        barcode_candidates=list(dict.fromkeys(payloads))[:_MAX_BARCODES],
        label_synonyms=_learned_lines(services),
        prompt_version=services.prompt_version,
        response_schema=page_schema(options),
    )
    try:
        response = services.vision.extract(request)
    except ProviderExhausted as exc:
        log.warning("page_read_exhausted", page_id=first.page_id, hops=len(exc.failovers))
        return None
    by_number = {a.page: a for a in response.pages}
    for number, page in enumerate(group, start=1):
        page.answer = by_number.get(number)
        if page.answer is not None:
            _first_reads(page, response, services)
    return response


def _second_page_read(page: _Page, services: VisionServices) -> VisionResponse | None:
    """The blind second read of a whole page by the verify chain (a different model), for the
    fields still unconfirmed. Whole-page context avoids the misplaced-crop misreads measured on
    the samples (a crop cut beside the value, read as another number). None when no provider
    answered: the fields keep their single reading."""
    options = [s.canonical for s in services.senders.specs]
    request = VisionRequest(
        job_id=page.raw.image.job_id,
        page_id=page.raw.page_id,
        task="page",
        page_images=[page.image],
        crops=[],
        sender_options=options,
        barcode_candidates=list(dict.fromkeys(h.payload for h in page.hits))[:_MAX_BARCODES],
        prompt_version=services.prompt_version,
        response_schema=page_schema(options),
    )
    try:
        second = services.verify.extract(request)
    except ProviderExhausted as exc:
        log.warning("verify_read_exhausted", page_id=page.raw.page_id, hops=len(exc.failovers))
        return None
    answer = next(iter(second.pages), None)
    if answer is None:
        return None
    for key in list(page.to_verify):
        found = answer.answers.get(key)
        values = found.values if found is not None else []
        if key in _SHOWN_WHEN_DISPUTED and _names_from_address(answer):
            values = []
        prior = page.reads[key][0].source if page.reads.get(key) else SourceKind.unknown
        page.reads[key] = page.reads.get(key, []) + _cloud_candidates(
            key,
            values,
            second,
            source=prior,
            box=found.box if found is not None else None,
            second=True,
            senders=services.senders,
        )
        page.trails[key].append(_step(second, values[0] if values else None, page_sent=True))
        page.crops_sent += 1
        if key is FieldKey.commitment_number and values:
            page.reads[key] = _keep_verified(page.reads[key], values)
    return second


def read_batch_vision(
    raws: Sequence[PageRaw],
    *,
    services: VisionServices,
    progress: Progress | None = None,
) -> list[PageOutcome | None]:
    """The vision-first reading of a few pages, in order. An entry is None when no provider
    could read that page (the caller then runs the local cascade on it). Provider problems never
    escape; an unreadable page image raises SafeLineError."""
    announce = progress or (lambda raw, stage: None)
    for raw in raws:
        announce(raw, "S3")
    pages = [_prepare(raw, services) for raw in raws]

    for raw in raws:
        announce(raw, "S9")
    t_cloud = time.perf_counter()
    size = max(1, services.settings.vision_pages_per_call)
    groups = [pages[i : i + size] for i in range(0, len(pages), size)]
    # Each page-read request runs on its own thread: they wait on the network side by side.
    with ThreadPoolExecutor(max_workers=len(groups), thread_name_prefix="read") as pool:
        answered = list(pool.map(lambda group: _read_pages(group, services), groups))
    responses = [r for r in answered if r is not None]
    if not responses:
        return [None] * len(raws)
    first = responses[0]
    read = [p for p in pages if p.answer is not None]

    if services.settings.verify_mode == "page":
        verifying = [p for p in read if p.to_verify]
        if verifying:
            with ThreadPoolExecutor(
                max_workers=len(verifying), thread_name_prefix="verify"
            ) as pool:
                seconds = list(pool.map(lambda page: _second_page_read(page, services), verifying))
            responses += [r for r in seconds if r is not None]
        built = None
    else:
        built = _verify_request(read, services)
    if built is not None:
        verify_request, owners = built
        try:
            second = services.verify.extract(verify_request)
        except ProviderExhausted as exc:
            log.warning("verify_read_exhausted", page_id=raws[0].page_id, hops=len(exc.failovers))
        else:
            responses.append(second)
            for (page, key), crop, values in zip(
                owners, verify_request.crops, second.crop_values or [[]] * len(owners), strict=False
            ):
                prior = page.reads[key][0].source if page.reads.get(key) else SourceKind.unknown
                page.reads[key] = page.reads.get(key, []) + _cloud_candidates(
                    key,
                    values,
                    second,
                    source=prior,
                    box=crop.box,
                    second=True,
                    senders=services.senders,
                )
                page.trails[key].append(
                    _step(second, values[0] if values else None, page_sent=False)
                )
                page.crops_sent += 1
                if key is FieldKey.commitment_number and values:
                    page.reads[key] = _keep_verified(page.reads[key], values)
    cloud_ms = int((time.perf_counter() - t_cloud) * _MS)

    outcomes: list[PageOutcome | None] = []
    booked = False
    for page in pages:
        if page.answer is None:
            outcomes.append(None)
            continue
        announce(page.raw, "S8")
        shared = responses if not booked else []
        booked = True
        stats = _stats(shared, len(page.to_verify), page.crops_sent, services.usd_per_million)
        outcomes.append(_outcome(page, services, stats, cloud_ms))
    log.info(
        "stage_done",
        job_id=raws[0].image.job_id,
        stage="vision_first",
        pages=len(raws),
        read=len(read),
        verified=sum(p.crops_sent for p in pages),
        provider=first.provider.value,
        model=first.model_id,
        requests=len(responses),
        duration_ms=cloud_ms,
    )
    return outcomes


__all__ = ["VisionServices", "read_batch_vision"]
