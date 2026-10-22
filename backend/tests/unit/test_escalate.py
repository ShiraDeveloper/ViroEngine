"""S9 escalation stage (PLAN §4 S9, §13 T18): what is sent, how the answer is re-scored, how
failure is handled. The provider is a scripted fake; nothing touches the network."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped,unused-ignore]

from backend.adapters.imaging.cv import CvImaging
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.adapters.vision.config import ProvidersConfig
from backend.core.models import (
    REASON_TEXT,
    ConfidenceLevel,
    FieldKey,
    NormalizedPage,
    ReaderName,
    RegionKind,
)
from backend.core.pipeline.s9_escalate import EscalationConfig, needs_escalation
from backend.settings import Settings
from backend.tests.fixtures.synthetic.pages import Pixels, clean_page
from backend.tests.fixtures.vision import ScriptedVision, answer
from backend.tests.fixtures.vision_scenarios import (
    ID_BOX,
    NAME_BOX,
    STICKER,
    missing,
    run_s9,
    standard_regions,
    weak,
)

MODELS_YAML = Path(__file__).resolve().parents[3] / "config" / "models.yaml"

Normalized = Callable[[Pixels], NormalizedPage]


@pytest.fixture
def page(make_normalized: Normalized) -> NormalizedPage:
    return make_normalized(clean_page())


def three_weak() -> dict[FieldKey, object]:
    return {
        FieldKey.first_name: weak(FieldKey.first_name, NAME_BOX),
        FieldKey.last_name: weak(FieldKey.last_name, NAME_BOX, value="כהן"),
        FieldKey.national_id: weak(FieldKey.national_id, ID_BOX, value="123"),
    }


def run(vision, evals, page, settings, imaging, storage, **kw):  # type: ignore[no-untyped-def]
    return run_s9(
        vision,
        evals,
        page=page,
        regions=kw.pop("regions", standard_regions()),
        settings=kw.pop("settings_override", settings),
        imaging=imaging,
        storage=storage,
        **kw,
    )


def test_unresolved_fields_go_in_one_request(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision(answers={FieldKey.first_name: answer("דנה")})

    out = run(vision, three_weak(), page, settings, imaging, storage)

    assert len(vision.requests) == 1
    assert {c.field for c in vision.requests[0].crops} == {
        FieldKey.first_name,
        FieldKey.last_name,
        FieldKey.national_id,
    }
    assert out.stats.calls == 1
    assert out.stats.crops_sent == 3
    assert out.stats.fields_sent == 3


def test_nothing_is_sent_when_every_field_clears_the_threshold(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision()
    lenient = settings.model_copy(update={"escalation_score_threshold": 0.0})
    evals = {FieldKey.first_name: weak(FieldKey.first_name, NAME_BOX)}

    out = run(vision, evals, page, lenient, imaging, storage)

    assert vision.requests == []
    assert out.stats.calls == 0


def test_failed_hard_rule_escalates_whatever_the_threshold(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision()
    lenient = settings.model_copy(update={"escalation_score_threshold": 0.0})
    evals = {FieldKey.national_id: weak(FieldKey.national_id, ID_BOX, value="123")}

    run(vision, evals, page, lenient, imaging, storage)

    assert len(vision.requests) == 1


def test_sender_is_never_escalated(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision()
    evals = {FieldKey.sender: missing(FieldKey.sender)}

    run(vision, evals, page, settings, imaging, storage)

    assert vision.requests == []


def test_field_with_no_known_location_is_not_sent(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision()
    evals = {FieldKey.first_name: missing(FieldKey.first_name)}

    run(vision, evals, page, settings, imaging, storage, regions=[])

    assert vision.requests == []


def test_field_without_candidates_is_cut_from_its_region(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision()
    evals = {FieldKey.central_number: missing(FieldKey.central_number)}

    run(vision, evals, page, settings, imaging, storage)

    (crop,) = vision.requests[0].crops
    assert crop.region_kind is RegionKind.central_sticker
    assert crop.box[0] <= STICKER[0]  # padded outward from the region


def test_cloud_answer_is_rescored_not_trusted(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision(answers={FieldKey.first_name: answer("דנה")})
    evals = {FieldKey.first_name: weak(FieldKey.first_name, NAME_BOX, value="דנא")}

    out = run(vision, evals, page, settings, imaging, storage)

    result = out.evaluations[FieldKey.first_name].result
    cloud = [c for c in result.candidates if c.reader is ReaderName.gemini]
    assert len(cloud) == 1
    assert cloud[0].reader_confidence == EscalationConfig().cloud_reader_confidence
    assert result.level not in (ConfidenceLevel.A, ConfidenceLevel.B)
    step = result.trail[-1]
    assert (step.stage, step.cloud, step.page_sent) == ("S9", True, False)


def test_lone_cloud_read_is_never_closed_as_confirmed(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision(answers={FieldKey.central_number: answer("4417528")})
    evals = {FieldKey.central_number: missing(FieldKey.central_number)}

    out = run(vision, evals, page, settings, imaging, storage)

    result = out.evaluations[FieldKey.central_number].result
    assert result.escalated is True
    assert result.level in (ConfidenceLevel.C, ConfidenceLevel.D)


def test_unanswered_field_stays_as_it_was(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision(answers={})
    evals = {FieldKey.first_name: weak(FieldKey.first_name, NAME_BOX)}

    out = run(vision, evals, page, settings, imaging, storage)

    after = out.evaluations[FieldKey.first_name].result
    assert after.value == evals[FieldKey.first_name].result.value
    assert after.trail[-1].outcome == "no_value"


def test_provider_exhausted_flags_fields_and_returns(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision(down=True)

    out = run(vision, three_weak(), page, settings, imaging, storage)

    for key in (FieldKey.first_name, FieldKey.last_name, FieldKey.national_id):
        result = out.evaluations[key].result
        assert result.level is ConfidenceLevel.D
        assert result.reason == REASON_TEXT["providers_exhausted"]
    assert out.stats.exhausted is True
    assert out.stats.failovers == ["gemini:unavailable"]
    assert out.stats.calls == 0


def test_local_only_skips_the_stage(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision()
    local = settings.model_copy(update={"local_only": True})
    evals = three_weak()

    out = run(vision, evals, page, local, imaging, storage)

    assert vision.requests == []
    assert out.evaluations == evals


def test_missing_vision_port_skips_the_stage(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    evals = three_weak()

    out = run(None, evals, page, settings, imaging, storage)

    assert out.evaluations == evals


def test_cached_answer_counts_a_hit_not_a_call(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision(answers={FieldKey.first_name: answer("דנה")}, cached=True)
    evals = {FieldKey.first_name: weak(FieldKey.first_name, NAME_BOX)}

    out = run(vision, evals, page, settings, imaging, storage)

    assert (out.stats.calls, out.stats.cache_hits, out.stats.crops_sent) == (0, 1, 0)
    assert out.stats.estimated_usd == 0.0


def test_cost_is_priced_from_tokens(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision(answers={FieldKey.first_name: answer("דנה")})
    evals = {FieldKey.first_name: weak(FieldKey.first_name, NAME_BOX)}

    out = run(vision, evals, page, settings, imaging, storage)

    cfg = EscalationConfig()
    expected = (
        100 * cfg.usd_per_million_input_tokens + 20 * cfg.usd_per_million_output_tokens
    ) / 1e6
    assert out.stats.estimated_usd == pytest.approx(expected)


def test_non_sample_without_consent_sends_nothing_and_flags(
    page: NormalizedPage, settings: Settings, imaging: CvImaging, storage: TmpdirStorage
) -> None:
    vision = ScriptedVision()

    out = run(vision, three_weak(), page, settings, imaging, storage, cloud_allowed=False)

    assert vision.requests == []
    assert out.stats.gated is True
    assert out.evaluations[FieldKey.first_name].result.reason == REASON_TEXT["providers_exhausted"]


def test_needs_escalation_ignores_reader_confidence() -> None:
    confident = weak(FieldKey.first_name, NAME_BOX)
    boosted = confident.result.model_copy(update={"score": 0.5})
    assert needs_escalation(
        confident.__class__(result=boosted, signals=None, evidence=None), threshold=0.8
    )


def test_escalation_defaults_match_models_yaml() -> None:
    data = yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8"))

    assert EscalationConfig.model_validate(data["escalation"]) == EscalationConfig()


def test_provider_defaults_match_models_yaml() -> None:
    data = yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8"))

    assert ProvidersConfig.model_validate(data["providers"]) == ProvidersConfig()
