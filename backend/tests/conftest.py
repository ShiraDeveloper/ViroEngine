"""Shared fixtures for the S0-S2 unit tests: real storage, real imaging, synthetic pages."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from backend.adapters.imaging.cv import CvImaging
from backend.adapters.imaging.pdf import DocumentReader
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.models import NormalizedPage, PageMeasurement, PageRaw
from backend.core.pipeline.s2_correct import correct
from backend.settings import Settings
from backend.tests.fixtures.synthetic.files import png_bytes
from backend.tests.fixtures.synthetic.pages import Pixels

JOB_ID = "job-test"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Defaults only (no .env), with job data under pytest's temp directory."""
    return Settings(_env_file=None, data_dir=str(tmp_path))  # type: ignore[call-arg]


@pytest.fixture
def storage(settings: Settings) -> TmpdirStorage:
    return TmpdirStorage(settings=settings)


@pytest.fixture
def documents(settings: Settings) -> DocumentReader:
    return DocumentReader(settings=settings)


@pytest.fixture
def imaging(settings: Settings) -> CvImaging:
    return CvImaging(settings=settings)


@pytest.fixture
def make_page(storage: TmpdirStorage) -> Callable[[Pixels], PageRaw]:
    """Store a synthetic image as a raw page, as S0 would."""

    def _make(image: Pixels, page_id: str = "page#1") -> PageRaw:
        data = png_bytes(image)
        ref = storage.put_page_image(JOB_ID, page_id, data, "color")
        return PageRaw(
            page_id=page_id,
            source_file="page.png",
            page_index=0,
            sha256=ref.sha256,
            image=ref,
        )

    return _make


@pytest.fixture
def make_normalized(
    make_page: Callable[[Pixels], PageRaw],
    settings: Settings,
    storage: TmpdirStorage,
    imaging: CvImaging,
) -> Callable[[Pixels], NormalizedPage]:
    """Store a synthetic image and run it through S2 untouched, as a page that needs nothing."""

    def _make(image: Pixels) -> NormalizedPage:
        measurement = PageMeasurement(
            rotation_deg=0,
            rotation_votes={"0": 0, "90": 0, "180": 0, "270": 0},
            skew_deg=0.0,
            blur_score=1000.0,
            illumination_cv=0.0,
            noise_score=0.0,
            is_photo=False,
            needs=[],
        )
        return correct(
            make_page(image), measurement, settings=settings, imaging=imaging, storage=storage
        )

    return _make


@pytest.fixture(autouse=True)
def _fresh_sse_state() -> None:
    """sse-starlette keeps one process-wide exit event bound to the first event loop that served a
    stream. Every TestClient runs its own loop, so the event is forgotten before each test."""
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
