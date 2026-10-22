"""A document that is not a known sample is processed locally unless the job has consent
(PLAN §8.5): its crops never reach a provider, not even S5's letterhead question."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from backend.adapters.imaging.cv import CvImaging
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.models import REASON_TEXT, ConfidenceLevel, FieldKey, PageRaw
from backend.core.pipeline.orchestrator import process_page
from backend.settings import Settings
from backend.tests.fixtures.synthetic.pages import Pixels, clean_page
from backend.tests.fixtures.vision import ScriptedVision
from backend.tests.fixtures.vision_scenarios import pipeline_services


def make_raw(make_page: Callable[[Pixels], PageRaw], *, known: bool) -> PageRaw:
    raw = make_page(clean_page(width=900, height=1280))
    return raw.model_copy(update={"known_sample": known})


def settings_for(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=str(tmp_path))  # type: ignore[call-arg]


def test_unknown_document_does_not_call_provider(
    tmp_path: Path,
    make_page: Callable[[Pixels], PageRaw],
    storage: TmpdirStorage,
    imaging: CvImaging,
) -> None:
    vision = ScriptedVision()
    raw = make_raw(make_page, known=False)
    services = pipeline_services(settings_for(tmp_path), storage, imaging, vision, raw.page_id)

    outcome = process_page(raw, services=services)

    assert vision.requests == []
    assert outcome.escalation.gated is True
    patient = outcome.evaluations[FieldKey.first_name].result
    assert patient.level is ConfidenceLevel.D
    assert patient.reason == REASON_TEXT["providers_exhausted"]


def test_unknown_document_with_consent_reaches_the_provider(
    tmp_path: Path,
    make_page: Callable[[Pixels], PageRaw],
    storage: TmpdirStorage,
    imaging: CvImaging,
) -> None:
    vision = ScriptedVision()
    raw = make_raw(make_page, known=False)
    services = pipeline_services(settings_for(tmp_path), storage, imaging, vision, raw.page_id)

    outcome = process_page(raw, services=services, cloud_consent=True)

    assert vision.requests, "consent lets the crops go"
    assert outcome.escalation.gated is False


def test_known_sample_reaches_the_provider_without_consent(
    tmp_path: Path,
    make_page: Callable[[Pixels], PageRaw],
    storage: TmpdirStorage,
    imaging: CvImaging,
) -> None:
    vision = ScriptedVision()
    raw = make_raw(make_page, known=True)
    services = pipeline_services(settings_for(tmp_path), storage, imaging, vision, raw.page_id)

    process_page(raw, services=services)

    assert vision.requests
