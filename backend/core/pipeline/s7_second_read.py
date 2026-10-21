"""S7 - independent second local read of risky candidates (PLAN §4 S7, T12).

A candidate is re-read on its own padded, upscaled crop with a different page-segmentation mode
and a per-field character whitelist, so the second reading is genuinely independent of the first.
Everything here is local: no step is `cloud` and nothing leaves the machine.

Pure with respect to I/O: the OCR readers and the cropper are injected. The digit reader (TrOCR,
T13) is reached through the same `OcrPort`; this module never imports an adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import string
from typing import Literal

from pydantic import Field
import structlog

from backend.core.errors import SafeLineError
from backend.core.models import (
    Box,
    Candidate,
    FieldKey,
    ImageRef,
    OcrResult,
    ReaderName,
    ReadingTrailStep,
    SourceKind,
    Strict,
)
from backend.core.ports.clock import ClockPort
from backend.core.ports.ocr import OcrPort
from backend.core.validators import CONFUSABLE, FIT_NEUTRAL, pattern_fit

log = structlog.get_logger()

# Crop `box` (normalised, already padded and clamped) from the colour image and upscale it by the
# given factor with cubic interpolation. Implemented by an adapter; the core never touches pixels.
Cropper = Callable[[ImageRef, Box, float], ImageRef]

# Structural: a numeric field is read with digits only.
DIGITS = string.digits
# Structural: a commitment number may carry a letter (X132767), so a digits-only whitelist would
# destroy it. Names get no whitelist at all.
COMMITMENT_WHITELIST = string.digits + string.ascii_uppercase

_NUMERIC_FIELDS = frozenset({FieldKey.national_id, FieldKey.commitment_number})
_SECOND_READ_NOTE = "second_read"
_DIGIT_READER_NOTE = "digit_reader"
# Sources that are not a visual read: a decoded symbol is exact, so re-reading it adds nothing.
_NOT_VISUAL = frozenset({SourceKind.barcode})
_RISKY_SOURCES = frozenset({SourceKind.handwritten, SourceKind.stamp})


class SecondReadConfig(Strict):
    """S7 thresholds. `config/models.yaml: second_read` is authoritative; a test keeps these
    defaults equal to it."""

    risky_confidence: float = Field(default=0.75, ge=0.0, le=1.0)  # reader confidence below this
    pad_fraction: float = Field(default=0.02, ge=0.0, le=0.5)  # of the image diagonal
    upscale: float = Field(default=2.5, ge=1.0)  # cubic; <2 loses the gain, >3 only costs time
    psm: int = Field(default=7, ge=0, le=13)  # Tesseract single line, unlike the page read
    digit_candidates: int = Field(default=5, ge=1)  # most alternatives taken from the digit reader


class SecondRead(Strict):
    """Output of S7: the new readings and one trail step per input candidate."""

    reads: list[Candidate]
    trail: list[ReadingTrailStep]


def whitelist_for(field: FieldKey) -> str | None:
    """Tesseract whitelist for `field`: digits for an ID, digits + A-Z for a commitment number,
    None (all characters) for everything else."""
    if field is FieldKey.national_id:
        return DIGITS
    if field is FieldKey.commitment_number:
        return COMMITMENT_WHITELIST
    return None


def padded_box(box: Box, image: ImageRef, config: SecondReadConfig) -> Box:
    """`box` grown by `pad_fraction` of the image diagonal on every side, clamped to the page.

    Tesseract loses glyphs that touch the crop edge. Pixel padding is converted per axis, so the
    margin is the same size horizontally and vertically. An image with no size reported pads as
    a unit square. Never raises."""
    width = image.width if image.width > 0 else 1
    height = image.height if image.height > 0 else 1
    pad_px = config.pad_fraction * (width**2 + height**2) ** 0.5
    pad_y, pad_x = pad_px / height, pad_px / width
    return (
        max(0.0, box[0] - pad_y),
        max(0.0, box[1] - pad_x),
        min(1.0, box[2] + pad_y),
        min(1.0, box[3] + pad_x),
    )


def is_risky(candidate: Candidate, *, hints: Sequence[str], config: SecondReadConfig) -> bool:
    """True when the candidate deserves a second read: source handwritten/stamp, reader
    confidence below `risky_confidence`, a digit that handwriting confuses, or a miss against the
    sender's soft hints. A barcode payload is never risky. Never raises."""
    if candidate.source in _NOT_VISUAL:
        return False
    return (
        candidate.source in _RISKY_SOURCES
        or candidate.reader_confidence < config.risky_confidence
        or any(ch in CONFUSABLE for ch in candidate.value)
        or (bool(hints) and pattern_fit(candidate.value, hints) < FIT_NEUTRAL)
    )


def _mean_confidence(result: OcrResult) -> float:
    words = result.words
    return sum(w.confidence for w in words) / len(words) if words else 0.0


def _duration_ms(clock: ClockPort, started: float) -> int:
    return max(0, int((clock.now().timestamp() - started) * 1000))


def _step(
    outcome: Literal["value", "no_value", "rejected", "skipped"],
    reader: ReaderName,
    value: str | None,
    confidence: float | None,
    duration_ms: int,
) -> ReadingTrailStep:
    return ReadingTrailStep(
        stage="S7",
        reader=reader,
        outcome=outcome,
        value=value,
        reader_confidence=confidence,
        duration_ms=duration_ms,
        cloud=False,
    )


def _read_values(result: OcrResult, limit: int) -> list[str]:
    """Distinct non-empty readings: the text first, then the reader's alternatives, up to
    `limit`. Whitespace is collapsed; characters are never altered."""
    seen: list[str] = []
    for raw in (result.text, *result.candidates):
        value = " ".join(raw.split())
        if value and value not in seen:
            seen.append(value)
    return seen[:limit]


def second_read(
    candidates: Sequence[Candidate],
    *,
    field: FieldKey,
    image: ImageRef,
    ocr: OcrPort,
    crop: Cropper,
    clock: ClockPort,
    hints: Sequence[str] = (),
    digit_ocr: OcrPort | None = None,
    config: SecondReadConfig | None = None,
) -> SecondRead:
    """Re-read each risky candidate; a candidate that is not risky, or has no box, is `skipped`.

    `image` is the COLOUR page; `crop` returns the padded, upscaled crop. Reader 2a is `ocr` at
    `config.psm` with `whitelist_for(field)`. Reader 2b, `digit_ocr`, runs only for a handwritten
    value of a numeric field and may add up to `digit_candidates` alternatives. A reading that
    produced nothing is a `no_value` step, never an empty candidate. A recoverable
    `SafeLineError` from the cropper or a reader skips that candidate; anything else propagates."""
    cfg = config or SecondReadConfig()
    reads: list[Candidate] = []
    trail: list[ReadingTrailStep] = []
    for cand in candidates:
        if cand.box is None or not is_risky(cand, hints=hints, config=cfg):
            trail.append(_step("skipped", ReaderName.tesseract, None, None, 0))
            continue
        started = clock.now().timestamp()
        try:
            crop_ref = crop(image, padded_box(cand.box, image, cfg), cfg.upscale)
            readers: list[tuple[OcrPort, bool]] = [(ocr, False)]
            if (
                digit_ocr is not None
                and cand.source is SourceKind.handwritten
                and field in _NUMERIC_FIELDS
            ):
                readers.append((digit_ocr, True))
            for reader, is_digit_reader in readers:
                result = reader.read_crop(crop_ref, whitelist=whitelist_for(field), psm=cfg.psm)
                values = _read_values(result, cfg.digit_candidates if is_digit_reader else 1)
                elapsed = _duration_ms(clock, started)
                if not values:
                    trail.append(_step("no_value", result.reader, None, None, elapsed))
                    continue
                confidence = _mean_confidence(result)
                trail.append(_step("value", result.reader, values[0], confidence, elapsed))
                for value in values:
                    reads.append(
                        Candidate(
                            value=value,
                            reader=result.reader,
                            source=cand.source,
                            box=cand.box,
                            reader_confidence=confidence,
                            label_distance=cand.label_distance,
                            region=cand.region,
                            notes=[_DIGIT_READER_NOTE if is_digit_reader else _SECOND_READ_NOTE],
                        )
                    )
        except SafeLineError as exc:
            if not exc.recoverable:
                raise
            log.warning("second_read_failed", field=field.value, code=exc.code)
            trail.append(_step("skipped", ReaderName.tesseract, None, None, 0))
    return SecondRead(reads=reads, trail=trail)
