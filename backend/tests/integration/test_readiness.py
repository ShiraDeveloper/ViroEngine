"""The engine says when it cannot read (PLAN §3.5 `OCR_UNAVAILABLE`, T28): a missing Hebrew pack
must be a visible 503, not a silently empty result."""

from __future__ import annotations

import dataclasses
import io
from pathlib import Path
import shutil
from typing import Any

import pytest

from backend.adapters.fakes import FakeOcr
from backend.adapters.ocr.tesseract import TesseractAdapter
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.ports.ocr import OcrProbePort
from backend.readiness import ocr_readiness
from backend.settings import Settings
from backend.tests.fixtures.api import HEADERS, Harness, running_app


class Probe(FakeOcr):
    """A reader whose installed language packs the test decides."""

    def __init__(self, installed: list[str] | None) -> None:
        super().__init__(name="probe")
        self.installed = installed

    def languages(self) -> list[str] | None:
        return self.installed


def with_reader(h: Harness, reader: Any) -> None:
    h.app.state.container = dataclasses.replace(h.container, ocr_printed=reader)


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    with running_app(monkeypatch, tmp_path) as harness:
        yield harness


def test_a_reader_with_the_packs_is_ready(h: Harness) -> None:
    with_reader(h, Probe(["eng", "heb", "osd"]))

    response = h.client.get("/v1/health")

    body = response.json()
    assert response.status_code == 200 and body["status"] == "ok"
    assert body["ocr"] == "ready" and "heb" in body["ocr_langs"] and body["ocr_missing"] == []


def test_a_missing_hebrew_pack_makes_health_unready_with_503(h: Harness) -> None:
    with_reader(h, Probe(["eng", "osd"]))

    response = h.client.get("/v1/health")

    body = response.json()
    assert response.status_code == 503 and body["status"] == "unready"
    assert body["ocr"] == "unavailable" and body["ocr_missing"] == ["heb"]


def test_an_unreachable_engine_is_unready(h: Harness) -> None:
    with_reader(h, Probe(None))

    response = h.client.get("/v1/health")

    assert response.status_code == 503
    assert response.json()["ocr_missing"] == ["heb", "eng"]


def test_uploads_are_refused_while_unready(h: Harness) -> None:
    with_reader(h, Probe(["eng"]))

    response = h.client.post(
        "/v1/documents",
        files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 stub"), "application/pdf")},
        headers=HEADERS,
    )

    assert (response.status_code, response.json()["error"]) == (503, "OCR_UNAVAILABLE")
    assert h.store.metrics.jobs_total == 0, "nothing was accepted and wasted"


def test_reads_still_work_while_unready(h: Harness) -> None:
    with_reader(h, Probe(["eng"]))

    assert h.client.get("/v1/senders", headers=HEADERS).status_code == 200


def test_a_reader_without_a_probe_is_assumed_ready(h: Harness) -> None:
    class Plain:
        name = "plain"

    with_reader(h, Plain())

    assert ocr_readiness(h.app.state.container).ready is True


def test_the_probe_protocol_is_satisfied_by_the_real_adapter(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=str(tmp_path))  # type: ignore[call-arg]

    adapter = TesseractAdapter(settings=settings, storage=TmpdirStorage(settings=settings))

    assert isinstance(adapter, OcrProbePort)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_the_real_adapter_lists_installed_packs(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=str(tmp_path))  # type: ignore[call-arg]
    adapter = TesseractAdapter(settings=settings, storage=TmpdirStorage(settings=settings))

    langs = adapter.languages()

    assert langs is not None and "eng" in langs
    assert adapter.languages() == langs, "the answer is stable once known"


def test_the_real_adapter_reports_none_when_the_binary_is_missing(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, data_dir=str(tmp_path), tesseract_cmd=str(tmp_path / "no-such-tesseract")
    )
    adapter = TesseractAdapter(settings=settings, storage=TmpdirStorage(settings=settings))
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = str(tmp_path / "no-such-tesseract")

    assert adapter.languages() is None
