"""S1 Measure — report a page's condition and flag the corrections it needs (PLAN §4 S1).

Measurement only: nothing here changes the image. That is what makes processing dynamic —
a clean page yields ``needs == []`` and S2 then does nothing.

Orientation is the one measurement that needs judgement. Three independent signals each
vote for the clockwise rotation that would make the page upright, or abstain:

  (a) barcode sweep — the angle at which most barcodes decode;
  (b) Tesseract orientation/script detection (OSD);
  (c) Hebrew word count of a low-resolution OCR pass at 0/90/180/270.

No single signal is reliable (SAP screens carry no barcode, OSD is weak on sparse pages, a
sticker can be applied sideways on an upright page), so a rotation is applied only when
``rotation_min_votes`` signals agree. A tie goes to the barcode, otherwise to "no rotation".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import time
from typing import Final, Literal

import structlog

from backend.core.errors import RenderFailed
from backend.core.models import PageMeasurement, PageRaw
from backend.core.ports.barcode import BarcodePort
from backend.core.ports.imaging import CORRECTION_OPS, ImagingPort, PixelMeasurements
from backend.core.ports.ocr import OcrPort
from backend.core.ports.storage import StoragePort
from backend.settings import Settings

log = structlog.get_logger()

Rotation = Literal[0, 90, 180, 270]

# The four page orientations, in the order they are probed and reported.
ROTATIONS: Final[tuple[Rotation, ...]] = (0, 90, 180, 270)
_ROTATION_BY_VALUE: Final[dict[int, Rotation]] = {0: 0, 90: 90, 180: 180, 270: 270}

# Hebrew letters run from alef to tav. A token counts as a Hebrew word when it holds at
# least two of them and they make up most of it; low-resolution garbage rarely does both.
_ALEF: Final = "\u05d0"
_TAV: Final = "\u05ea"
_MIN_HEBREW_LETTERS: Final = 2
_MIN_HEBREW_SHARE: Final = 0.75

# The orientation probe reads Hebrew only; English test names would only add noise.
_PROBE_LANGS: Final = "heb"


@dataclass(frozen=True)
class _Votes:
    """The ballots cast for page orientation; each signal casts at most one."""

    barcode: Rotation | None
    osd: Rotation | None
    text: Rotation | None

    def counts(self) -> dict[Rotation, int]:
        """Votes per orientation, with all four orientations present."""
        tally: dict[Rotation, int] = dict.fromkeys(ROTATIONS, 0)
        for ballot in (self.barcode, self.osd, self.text):
            if ballot is not None:
                tally[ballot] += 1
        return tally


def measure(
    page: PageRaw,
    *,
    settings: Settings,
    ocr: OcrPort,
    barcode: BarcodePort,
    imaging: ImagingPort,
    storage: StoragePort,
) -> PageMeasurement:
    """Measure orientation (degrees clockwise to apply), skew (degrees), blur (Laplacian
    variance), illumination (CV), noise (0..1) and perspective, and list the corrections
    they call for. Never raises; an uncomputable measurement reports its neutral value."""
    started = time.perf_counter()
    image = storage.get(page.image)

    pixels = imaging.measure_pixels(image) if image else PixelMeasurements.unavailable()
    if not pixels.measured:
        log.warning("measure_unavailable", job_id=page.image.job_id, page_id=page.page_id)

    votes = _orientation_votes(page, image, settings, ocr, barcode, imaging, storage)
    tally = votes.counts()
    rotation = _decide_rotation(votes, settings.rotation_min_votes)

    skew = max(-settings.skew_max_deg, min(settings.skew_max_deg, pixels.skew_deg))
    # An unmeasured blur reports the threshold itself: neutral, it never triggers sharpen.
    blur = pixels.blur_score if pixels.measured else settings.blur_threshold
    is_photo = pixels.perspective_deviation > settings.perspective_threshold

    flagged = {
        "rotate": rotation != 0,
        "perspective": is_photo,
        "deskew": abs(skew) > settings.skew_threshold_deg,
        "clahe": pixels.illumination_cv > settings.illumination_cv_threshold,
        "denoise": pixels.noise_score > settings.noise_threshold,
        "sharpen": blur < settings.blur_threshold,
    }
    measurement = PageMeasurement(
        rotation_deg=rotation,
        rotation_votes={str(angle): tally[angle] for angle in ROTATIONS},
        skew_deg=skew,
        blur_score=blur,
        illumination_cv=pixels.illumination_cv,
        noise_score=pixels.noise_score,
        is_photo=is_photo,
        needs=[op for op in CORRECTION_OPS if flagged[op]],
    )
    log.info(
        "stage_done",
        job_id=page.image.job_id,
        page_id=page.page_id,
        stage="S1",
        needs=measurement.needs,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return measurement


# ---------------------------------------------------------------------------
# Orientation voting
# ---------------------------------------------------------------------------


def _orientation_votes(
    page: PageRaw,
    image: bytes,
    settings: Settings,
    ocr: OcrPort,
    barcode: BarcodePort,
    imaging: ImagingPort,
    storage: StoragePort,
) -> _Votes:
    """Collect the three ballots. Signal (c) is the costly one (four OCR passes), so it runs
    only when (a) and (b) have not already agreed with each other."""
    barcode_vote = _barcode_vote(barcode, page)
    osd_vote = _as_rotation(ocr.detect_orientation(page.image))
    already_agree = barcode_vote is not None and barcode_vote == osd_vote
    text_vote = (
        None
        if already_agree or not image
        else _text_vote(page, image, settings, ocr, imaging, storage)
    )
    return _Votes(barcode=barcode_vote, osd=osd_vote, text=text_vote)


def _decide_rotation(votes: _Votes, min_votes: int) -> Rotation:
    """The orientation with the most votes if it has at least `min_votes`; a tie between
    leaders goes to the barcode's choice, otherwise to no rotation."""
    tally = votes.counts()
    top = max(tally.values())
    if top < min_votes:
        return 0
    leaders = [angle for angle in ROTATIONS if tally[angle] == top]
    if len(leaders) == 1:
        return leaders[0]
    if votes.barcode is not None and votes.barcode in leaders:
        return votes.barcode
    return 0


def _barcode_vote(barcode: BarcodePort, page: PageRaw) -> Rotation | None:
    """The angle at which the most barcodes decode; abstains with no hit or a tie."""
    angles = Counter(
        rotation
        for hit in barcode.sweep(page.image, ROTATIONS)
        if (rotation := _as_rotation(hit.orientation_deg)) is not None
    )
    ranked = angles.most_common(2)
    if not ranked or (len(ranked) == 2 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def _text_vote(
    page: PageRaw,
    image: bytes,
    settings: Settings,
    ocr: OcrPort,
    imaging: ImagingPort,
    storage: StoragePort,
) -> Rotation | None:
    """Count Hebrew-looking words on a low-resolution copy rotated to each orientation and
    vote for the clearly best one; abstain when the evidence is thin or ambiguous."""
    try:
        probe = imaging.thumbnail(image, max_side=settings.ocr_probe_max_side)
        counts: dict[Rotation, int] = {}
        for angle in ROTATIONS:
            rotated = (
                probe
                if angle == 0
                else imaging.apply_operation(probe.data, "rotate", angle_deg=float(angle))
            )
            ref = storage.put_page_image(page.image.job_id, page.page_id, rotated.data, "thumb")
            result = ocr.read_page(ref, _PROBE_LANGS)
            counts[angle] = sum(
                1
                for word in result.words
                if word.confidence >= settings.ocr_min_word_confidence and _looks_hebrew(word.text)
            )
    except RenderFailed:
        return None
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    (best_angle, best), (_, runner_up) = ranked[0], ranked[1]
    if best < settings.ocr_probe_min_words or best < settings.ocr_probe_min_ratio * runner_up:
        return None
    return best_angle


def _looks_hebrew(token: str) -> bool:
    """True when `token` is mostly Hebrew letters (at least two): a word, not noise."""
    letters = sum(1 for ch in token if _ALEF <= ch <= _TAV)
    return letters >= _MIN_HEBREW_LETTERS and letters / len(token) >= _MIN_HEBREW_SHARE


def _as_rotation(angle: int | None) -> Rotation | None:
    """Normalise a reported angle to one of the four orientations, or None."""
    return None if angle is None else _ROTATION_BY_VALUE.get(angle % 360)
