"""S9 inside the real per-page pipeline: an outage never stops a run, a re-run costs nothing."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from backend.adapters.imaging.cv import CvImaging
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.adapters.vision.chain import VisionChainAdapter
from backend.core.models import REASON_TEXT, ConfidenceLevel, FieldKey, PageRaw
from backend.core.pipeline.orchestrator import iter_outcomes, process_page
from backend.settings import Settings
from backend.tests.fixtures.synthetic.pages import Pixels, clean_page
from backend.tests.fixtures.vision import (
    GEMINI_HOST,
    FakeClock,
    FakeTransport,
    all_down,
    answer_json,
    gemini_body,
    ok,
)
from backend.tests.fixtures.vision_scenarios import pipeline_services

PATIENT_FIELDS = (FieldKey.first_name, FieldKey.last_name, FieldKey.national_id)
ALL_FIELDS = dict.fromkeys(
    ("first_name", "last_name", "national_id", "receipt_date"), "x"
) | {"central_number": ["4417528"], "commitment_number": ["X132767"]}


def chain_for(settings: Settings, transport: FakeTransport) -> VisionChainAdapter:
    clock = FakeClock()
    return VisionChainAdapter(
        settings, transport=transport, clock=clock, sleep=clock.sleep, rand=lambda: 0.5
    )


def known_page(make_page: Callable[..., PageRaw]) -> PageRaw:
    raw: PageRaw = make_page(clean_page(width=900, height=1280))
    return raw.model_copy(update={"known_sample": True})


def provider_settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=str(tmp_path), gemini_api_keys="TESTKEY_ALPHA_0001")  # type: ignore[call-arg]


def test_all_providers_down_flags_fields_and_run_completes(
    tmp_path: Path,
    make_page: Callable[[Pixels], PageRaw],
    storage: TmpdirStorage,
    imaging: CvImaging,
) -> None:
    settings = provider_settings(tmp_path)
    transport = all_down()
    raw = known_page(make_page)  # type: ignore[arg-type]
    services = pipeline_services(settings, storage, imaging, chain_for(settings, transport), raw.page_id)

    outcomes = list(iter_outcomes([raw, raw.model_copy(update={"page_id": "p2"})], services=services))

    assert transport.total > 0, "the chain was never asked"
    first = outcomes[0]
    assert first.status == "done"
    assert first.escalation.exhausted is True
    for key in PATIENT_FIELDS:
        result = first.evaluations[key].result
        assert result.level is ConfidenceLevel.D
        assert result.reason == REASON_TEXT["providers_exhausted"]
    assert len(outcomes) == 2, "an outage on one page must not stop the batch"


def test_rerun_of_the_same_batch_makes_zero_provider_calls(
    tmp_path: Path,
    make_page: Callable[[Pixels], PageRaw],
    storage: TmpdirStorage,
    imaging: CvImaging,
) -> None:
    settings = provider_settings(tmp_path)
    # S5 asks the letterhead question first (no text or logo match), then S9 asks for the fields.
    sender = ok(body=gemini_body('{"sender": "לא מוכר"}'))
    fields = ok(body=gemini_body(answer_json(**ALL_FIELDS)))  # type: ignore[arg-type]
    transport = FakeTransport({GEMINI_HOST: [sender, fields]})
    raw = known_page(make_page)  # type: ignore[arg-type]
    services = pipeline_services(settings, storage, imaging, chain_for(settings, transport), raw.page_id)

    first = process_page(raw, services=services)
    calls_after_first = transport.total
    second = process_page(raw, services=services)

    assert calls_after_first == 2, "one letterhead question and one request for the page fields"
    assert transport.total == calls_after_first, "the re-run must come from the cache"
    assert first.escalation.calls == 1 and first.escalation.crops_sent > 0  # S9 itself: one call
    assert second.escalation.calls == 0
    assert second.escalation.cache_hits == 1
    assert second.escalation.crops_sent == 0
