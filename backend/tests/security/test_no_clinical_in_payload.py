"""What leaves the machine in an S9 request (PLAN §6.4, project rule §5): crops, never a page, and
never a clinical region. The redacted thumbnail is redacted BEFORE it is downscaled."""

from __future__ import annotations

from collections.abc import Callable
import shutil

import cv2
import numpy as np
import pytest

from backend.adapters.imaging.cv import CvImaging
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.models import Box, FieldKey, NormalizedPage, RegionKind
from backend.core.pipeline.orchestrator import PipelineConfig
from backend.core.pipeline.s9_escalate import REDACTION_GRAY, Planned, build_request
from backend.settings import Settings
from backend.tests.fixtures.synthetic.pages import Pixels, clean_page
from backend.tests.fixtures.vision import ScriptedVision
from backend.tests.fixtures.vision_scenarios import (
    CLINICAL,
    ID_BOX,
    NAME_BOX,
    PATIENT,
    STICKER,
    missing,
    run_s9,
    standard_regions,
    weak,
)

WIDTH, HEIGHT = 1200, 1700  # large enough that the 512 px thumbnail is a real downscale


def inked_page() -> Pixels:
    """A clean page with solid black 'ink' blocks inside the clinical and patient regions."""
    image = clean_page(width=WIDTH, height=HEIGHT).copy()
    for box in (CLINICAL, PATIENT):
        y0, x0, y1, x1 = (
            int(v) for v in (box[0] * HEIGHT, box[1] * WIDTH, box[2] * HEIGHT, box[3] * WIDTH)
        )
        image[y0 + 5 : y1 - 5, x0 + 5 : x1 - 5] = 0
    return image


def decode(data: bytes) -> np.ndarray:
    pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert pixels is not None
    return np.asarray(pixels)


def interior(box: Box, width: int, height: int, margin: float = 0.08) -> tuple[slice, slice]:
    """Pixel slices of `box` shrunk by `margin` of its size (away from resample edges)."""
    dy, dx = (box[2] - box[0]) * margin, (box[3] - box[1]) * margin
    return (
        slice(int((box[0] + dy) * height), int((box[2] - dy) * height)),
        slice(int((box[1] + dx) * width), int((box[3] - dx) * width)),
    )


@pytest.fixture
def sap_page(make_normalized: Callable[[Pixels], NormalizedPage]) -> NormalizedPage:
    return make_normalized(inked_page())


def central_missing() -> dict[FieldKey, object]:
    return {FieldKey.central_number: missing(FieldKey.central_number)}


def sent_request(vision: ScriptedVision):  # type: ignore[no-untyped-def]
    assert len(vision.requests) == 1
    return vision.requests[0]


def test_clinical_region_never_sent(
    sap_page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision()
    near_clinical: Box = (CLINICAL[0] - 0.01, 0.3, CLINICAL[0] + 0.03, 0.6)  # overlaps it
    evals = {
        FieldKey.first_name: weak(FieldKey.first_name, near_clinical),
        FieldKey.last_name: weak(FieldKey.last_name, NAME_BOX),
    }

    out = run_s9(
        vision,
        evals,
        page=sap_page,
        regions=standard_regions(),
        settings=settings,
        imaging=imaging,
        storage=storage,
    )

    request = sent_request(vision)
    assert [c.field for c in request.crops] == [FieldKey.last_name]
    assert out.stats.clinical_excluded == 1
    assert all(c.region_kind is not RegionKind.clinical for c in request.crops)
    for crop in request.crops:
        assert not (crop.box[0] < CLINICAL[2] and crop.box[2] > CLINICAL[0])


def test_clinical_crop_in_a_plan_is_a_bug(
    sap_page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    evals = {FieldKey.first_name: weak(FieldKey.first_name, NAME_BOX)}
    planned = [Planned(key=FieldKey.first_name, box=CLINICAL, region_kind=RegionKind.clinical)]

    with pytest.raises(AssertionError):
        build_request(
            evals,
            planned,
            page=sap_page,
            regions=standard_regions(),
            page_role="primary",
            spec=None,
            imaging=imaging,
            storage=storage,
            settings=settings,
            config=PipelineConfig().escalation,
            prompt_version="v1",
        )


def test_payload_contains_only_crops_and_redacted_thumb(
    sap_page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision()
    evals = {
        FieldKey.first_name: weak(FieldKey.first_name, NAME_BOX),
        FieldKey.national_id: weak(FieldKey.national_id, ID_BOX, value="123"),
        **central_missing(),
    }

    run_s9(
        vision,
        evals,
        page=sap_page,
        regions=standard_regions(),
        settings=settings,
        imaging=imaging,
        storage=storage,
        page_role="sap_screen",
    )

    request = sent_request(vision)
    for crop in request.crops:
        pixels = decode(crop.png)
        assert pixels.shape[0] < HEIGHT * 0.5 and pixels.shape[1] < WIDTH
    assert request.page_thumb is not None
    assert max(decode(request.page_thumb).shape[:2]) <= settings.thumb_max_side
    # No field of the request can carry a full page: the only image-bearing fields are these two.
    image_fields = {n for n, v in request if isinstance(v, bytes) or n == "crops"}
    assert image_fields == {"page_thumb", "crops"}


def test_thumbnail_is_redacted_before_downscale(
    sap_page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision()

    run_s9(
        vision,
        central_missing(),
        page=sap_page,
        regions=standard_regions(),
        settings=settings,
        imaging=imaging,
        storage=storage,
        page_role="sap_screen",
    )

    thumb = decode(sent_request(vision).page_thumb or b"")
    height, width = thumb.shape[:2]
    assert max(height, width) == settings.thumb_max_side
    for box in (CLINICAL, PATIENT):
        block = thumb[interior(box, width, height)]
        assert block.size > 0
        assert np.all(block == REDACTION_GRAY), "residual ink survived the downscale"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_no_readable_clinical_text_in_thumbnail(
    make_normalized: Callable[[Pixels], NormalizedPage],
    settings: Settings,
    imaging: CvImaging,
    storage: TmpdirStorage,
) -> None:
    pytesseract = pytest.importorskip("pytesseract")
    image = clean_page(width=WIDTH, height=HEIGHT).copy()
    y0, x0 = int(CLINICAL[0] * HEIGHT) + 60, int(CLINICAL[1] * WIDTH) + 40
    cv2.putText(
        image, "CONFIDENTIAL DIAGNOSIS", (x0, y0), cv2.FONT_HERSHEY_SIMPLEX, 2.4, (0, 0, 0), 6
    )
    page = make_normalized(image)
    vision = ScriptedVision()

    run_s9(
        vision,
        central_missing(),
        page=page,
        regions=standard_regions(),
        settings=settings,
        imaging=imaging,
        storage=storage,
        page_role="sap_screen",
    )

    thumb = decode(sent_request(vision).page_thumb or b"")
    text = pytesseract.image_to_string(thumb, lang="eng").upper()
    assert "CONFIDENTIAL" not in text and "DIAGNOSIS" not in text


@pytest.mark.parametrize(
    ("key", "role"),
    [
        (FieldKey.first_name, "sap_screen"),  # not a context field
        (FieldKey.central_number, "primary"),  # not a context page role
        (FieldKey.central_number, "continuation"),
    ],
)
def test_thumbnail_omitted_unless_field_needs_page_context(
    sap_page: NormalizedPage,
    settings: Settings,
    imaging: CvImaging,
    storage: TmpdirStorage,
    key: FieldKey,
    role: str,
) -> None:
    vision = ScriptedVision()

    run_s9(
        vision,
        {key: missing(key)},
        page=sap_page,
        regions=standard_regions(),
        settings=settings,
        imaging=imaging,
        storage=storage,
        page_role=role,
    )

    assert sent_request(vision).page_thumb is None


def test_no_thumbnail_when_send_thumbnail_disabled(
    sap_page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision()
    no_thumb = settings.model_copy(update={"escalation_send_thumbnail": False})

    run_s9(
        vision,
        central_missing(),
        page=sap_page,
        regions=standard_regions(),
        settings=no_thumb,
        imaging=imaging,
        storage=storage,
        page_role="sap_screen",
    )

    request = sent_request(vision)
    assert request.page_thumb is None
    assert [c.field for c in request.crops] == [FieldKey.central_number]
    assert request.crops[0].box[0] <= STICKER[0]
