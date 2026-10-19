"""Shared fixtures for the S0-S2 unit tests: real storage, real imaging, synthetic pages."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from backend.adapters.imaging.cv import CvImaging
from backend.adapters.imaging.pdf import DocumentReader
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.models import PageRaw
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
