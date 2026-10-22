"""LOCAL_ONLY=true produces zero network calls and never touches the vision port (project rule §5)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import socket

import pytest

from backend.adapters.imaging.cv import CvImaging
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.adapters.vision.chain import VisionChainAdapter
from backend.container import build_container
from backend.core.errors import ProviderExhausted
from backend.core.models import FieldKey, PageRaw
from backend.core.pipeline.orchestrator import process_page
from backend.settings import Settings
from backend.tests.fixtures.synthetic.pages import Pixels, clean_page
from backend.tests.fixtures.synthetic.pipeline_doubles import RecordingVision
from backend.tests.fixtures.vision import FakeTransport, fields_request
from backend.tests.fixtures.vision_scenarios import pipeline_services


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any attempt to open a connection fails the test."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a network connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def local_settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        data_dir=str(tmp_path),
        local_only=True,
        gemini_api_keys="TESTKEY_ALPHA_0001",
        openrouter_api_key="TESTKEY_ROUTER_0003",
        openrouter_vision_model="vendor/vision:free",
    )


def test_no_network_calls_when_local_only(tmp_path: Path, no_network: None) -> None:
    transport = FakeTransport()
    chain = VisionChainAdapter(local_settings(tmp_path), transport=transport)

    with pytest.raises(ProviderExhausted):
        chain.extract(fields_request(FieldKey.first_name))

    assert transport.total == 0


def test_container_builds_no_vision_port_when_local_only(tmp_path: Path) -> None:
    container = build_container(local_settings(tmp_path), use_fakes=True)

    assert container.vision is None


def test_pipeline_never_touches_the_vision_port_when_local_only(
    tmp_path: Path,
    make_page: Callable[[Pixels], PageRaw],
    storage: TmpdirStorage,
    imaging: CvImaging,
    no_network: None,
) -> None:
    settings = local_settings(tmp_path)
    vision = RecordingVision()  # raises if it is ever called
    raw = make_page(clean_page(width=900, height=1280)).model_copy(update={"known_sample": True})
    services = pipeline_services(settings, storage, imaging, vision, raw.page_id)

    outcome = process_page(raw, services=services, cloud_consent=True)

    assert outcome.status == "done"
    assert vision.calls == 0
    assert outcome.escalation.calls == 0
