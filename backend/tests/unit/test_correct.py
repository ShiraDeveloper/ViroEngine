"""Tests for S2 targeted correction (T9)."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
from typing import Literal

import cv2
import numpy as np
import pytest

from backend.adapters.fakes import FakeBarcode, FakeOcr
from backend.adapters.imaging.cv import CvImaging
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.errors import RenderFailed
from backend.core.models import ImageRef, NormalizedPage, PageMeasurement, PageRaw
from backend.core.pipeline.s1_measure import measure
from backend.core.pipeline.s2_correct import correct
from backend.settings import Settings
from backend.tests.fixtures.synthetic import pages
from backend.tests.fixtures.synthetic.doubles import FailingImaging
from backend.tests.fixtures.synthetic.pages import Pixels

MakePage = Callable[[Pixels], PageRaw]


def _measurement(
    *needs: str, rotation: Literal[0, 90, 180, 270] = 0, skew: float = 0.0
) -> PageMeasurement:
    return PageMeasurement(
        rotation_deg=rotation,
        rotation_votes={"0": 0, "90": 0, "180": 0, "270": 0},
        skew_deg=skew,
        blur_score=500.0,
        illumination_cv=0.0,
        noise_score=0.0,
        is_photo=False,
        needs=list(needs),
    )


def _load(storage: TmpdirStorage, ref: ImageRef) -> Pixels:
    data = storage.get(ref)
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    return np.asarray(image, dtype=np.uint8)


def _correct(
    page: PageRaw,
    measurement: PageMeasurement,
    settings: Settings,
    storage: TmpdirStorage,
    imaging: CvImaging,
) -> NormalizedPage:
    return correct(page, measurement, settings=settings, imaging=imaging, storage=storage)


def test_only_flagged_operations_applied(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    page = make_page(pages.clean_page())

    untouched = _correct(page, _measurement(), settings, storage, imaging)
    assert untouched.corrections_applied == []
    assert untouched.color == page.image  # nothing flagged, so the raw image is reused

    flagged = _correct(page, _measurement("sharpen"), settings, storage, imaging)
    assert flagged.corrections_applied == ["sharpen"]
    assert flagged.color.sha256 != page.image.sha256


def test_operations_run_in_the_fixed_order_whatever_the_needs_order(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    page = make_page(pages.clean_page())
    result = _correct(
        page, _measurement("sharpen", "clahe", "deskew", skew=1.0), settings, storage, imaging
    )
    assert result.corrections_applied == ["deskew", "clahe", "sharpen"]


def test_unknown_need_is_ignored(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    page = make_page(pages.clean_page())
    result = _correct(page, _measurement("teleport"), settings, storage, imaging)
    assert result.corrections_applied == []


def test_color_variant_is_never_binarized(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    """Blue ink and a pale stamp must survive every correction on the color image."""
    page = make_page(pages.clean_page())
    result = _correct(
        page,
        _measurement("deskew", "clahe", "sharpen", skew=1.0),
        settings,
        storage,
        imaging,
    )

    color = _load(storage, result.color)
    grey = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    assert color.ndim == 3
    assert len(np.unique(grey)) > 16  # a two-valued image would have at most 2 levels
    blue, red = color[:, :, 0].astype(int), color[:, :, 2].astype(int)
    assert int(((blue - red) > 80).sum()) > 100  # blue ink still blue

    binary = _load(storage, result.gray_bin)
    assert binary.ndim == 2
    assert set(np.unique(binary).tolist()) <= {0, 255}


def test_rotate_makes_the_page_upright(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    scanned = pages.rotated(pages.clean_page(), 90)  # needs a 270 degree clockwise turn
    page = make_page(scanned)
    result = _correct(page, _measurement("rotate", rotation=270), settings, storage, imaging)

    upright = _load(storage, result.color)
    assert upright.shape[:2] == (pages.PAGE_HEIGHT, pages.PAGE_WIDTH)
    assert pages.marker_corner_rotation(upright) == 0


def test_deskew_removes_the_measured_skew(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    page = make_page(pages.skewed(pages.clean_page(), 2.0))
    before = measure(
        page,
        settings=settings,
        ocr=FakeOcr(),
        barcode=FakeBarcode(),
        imaging=imaging,
        storage=storage,
    )
    assert "deskew" in before.needs

    fixed = _correct(page, before, settings, storage, imaging)
    after_page = make_page(_load(storage, fixed.color))
    after = measure(
        after_page,
        settings=settings,
        ocr=FakeOcr(),
        barcode=FakeBarcode(),
        imaging=imaging,
        storage=storage,
    )
    assert abs(after.skew_deg) < settings.skew_threshold_deg
    assert "deskew" not in after.needs


def test_perspective_and_clahe_correct_a_photograph(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    page = make_page(pages.photographed(pages.clean_page()))
    before = measure(
        page,
        settings=settings,
        ocr=FakeOcr(),
        barcode=FakeBarcode(),
        imaging=imaging,
        storage=storage,
    )
    assert "perspective" in before.needs

    fixed = _correct(page, before, settings, storage, imaging)
    assert fixed.corrections_applied[0] == "perspective"
    after_page = make_page(_load(storage, fixed.color))
    after = measure(
        after_page,
        settings=settings,
        ocr=FakeOcr(),
        barcode=FakeBarcode(),
        imaging=imaging,
        storage=storage,
    )
    assert after.is_photo is False


def test_failed_correction_keeps_original(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage
) -> None:
    failing = FailingImaging(fail=frozenset({"clahe"}), settings=settings)
    page = make_page(pages.clean_page())

    result = _correct(page, _measurement("clahe"), settings, storage, failing)

    assert result.corrections_applied == ["clahe_failed"]
    assert result.color == page.image  # the uncorrected image is kept
    assert hashlib.sha256(storage.get(result.color)).hexdigest() == page.sha256


def test_a_failure_in_the_middle_does_not_stop_later_operations(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage
) -> None:
    failing = FailingImaging(fail=frozenset({"deskew"}), settings=settings)
    page = make_page(pages.clean_page())
    result = _correct(
        page,
        _measurement("deskew", "sharpen", skew=1.0),
        settings,
        storage,
        failing,
    )
    assert result.corrections_applied == ["deskew_failed", "sharpen"]


def test_failed_binarization_falls_back_to_the_color_image(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage
) -> None:
    failing = FailingImaging(fail=frozenset({"binarize"}), settings=settings)
    page = make_page(pages.clean_page())
    result = _correct(page, _measurement(), settings, storage, failing)
    assert result.corrections_applied == ["binarize_failed"]
    assert result.gray_bin == result.color


def test_missing_raw_image_raises_render_failed(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    page = make_page(pages.clean_page())
    storage.purge_job(page.image.job_id)
    with pytest.raises(RenderFailed):
        _correct(page, _measurement(), settings, storage, imaging)


def test_thumbnail_is_bounded_and_never_upscaled(
    make_page: MakePage, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    big = _correct(make_page(pages.clean_page()), _measurement(), settings, storage, imaging)
    assert max(big.thumb.width, big.thumb.height) == settings.thumb_max_side

    tiny = _correct(
        make_page(pages.clean_page(width=200, height=280)),
        _measurement(),
        settings,
        storage,
        imaging,
    )
    assert (tiny.thumb.width, tiny.thumb.height) == (200, 280)
