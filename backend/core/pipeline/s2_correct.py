"""S2 Targeted correction — apply only the operations S1 flagged (PLAN §4 S2).

Operations run in the fixed order of ``CORRECTION_OPS``: rotate, perspective, deskew, clahe,
denoise, sharpen. Each success is appended to ``corrections_applied``. A failing operation
keeps the previous image, appends ``<op>_failed`` and the page continues.

Three variants leave this stage:
  * ``color``    — goes to the vision model and the UI; it is NEVER binarized, because the
                   patient details are in blue ink and the receipt date in a pale stamp, and
                   thresholding destroys both;
  * ``gray_bin`` — Sauvola binarization, for local OCR and barcodes only;
  * ``thumb``    — a small copy for the UI and the context thumbnail.
"""

from __future__ import annotations

import time

import structlog

from backend.core.errors import RenderFailed
from backend.core.models import NormalizedPage, PageMeasurement, PageRaw
from backend.core.ports.imaging import CORRECTION_OPS, ImagingPort
from backend.core.ports.storage import StoragePort
from backend.settings import Settings

log = structlog.get_logger()


def correct(
    page: PageRaw,
    measurement: PageMeasurement,
    *,
    settings: Settings,
    imaging: ImagingPort,
    storage: StoragePort,
) -> NormalizedPage:
    """Apply the flagged corrections to `page` and store its color, gray_bin and thumb.

    Raises RenderFailed only when the raw page image is missing from storage (the page is
    then marked failed by the caller); a failing operation never raises.
    """
    started = time.perf_counter()
    raw_bytes = storage.get(page.image)
    if not raw_bytes:
        raise RenderFailed("page image is missing from storage")

    current = raw_bytes
    applied: list[str] = []
    for op in CORRECTION_OPS:
        if op not in measurement.needs:
            continue
        try:
            current = imaging.apply_operation(
                current, op, angle_deg=_angle_for(op, measurement)
            ).data
        except RenderFailed:
            applied.append(f"{op}_failed")
            log.warning("correction_failed", job_id=page.image.job_id, page_id=page.page_id, op=op)
            continue
        applied.append(op)

    # Untouched pages keep their raw reference; the image is not written a second time.
    color = (
        page.image
        if current is raw_bytes
        else storage.put_page_image(page.image.job_id, page.page_id, current, "color")
    )

    gray_bin = color
    try:
        binarized = imaging.binarize(current)
        gray_bin = storage.put_page_image(
            page.image.job_id, page.page_id, binarized.data, "gray_bin"
        )
    except RenderFailed:
        applied.append("binarize_failed")

    thumb = color
    try:
        small = imaging.thumbnail(current, max_side=settings.thumb_max_side)
        thumb = storage.put_page_image(page.image.job_id, page.page_id, small.data, "thumb")
    except RenderFailed:
        applied.append("thumbnail_failed")

    log.info(
        "stage_done",
        job_id=page.image.job_id,
        page_id=page.page_id,
        stage="S2",
        corrections=applied,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return NormalizedPage(
        raw=page,
        measurement=measurement,
        corrections_applied=applied,
        color=color,
        gray_bin=gray_bin,
        thumb=thumb,
    )


def _angle_for(op: str, measurement: PageMeasurement) -> float:
    """The angle an operation needs: the clockwise rotation, or the skew to remove."""
    if op == "rotate":
        return float(measurement.rotation_deg)
    if op == "deskew":
        return measurement.skew_deg
    return 0.0
