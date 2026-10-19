from __future__ import annotations

import inspect
import json
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, ValidationError
import pytest

from backend.api import schemas
from backend.api.schemas import FieldEditRequest, JobView
from backend.core import errors, models
from backend.core.models import (
    REASON_TEXT,
    ConfidenceLevel,
    FieldKey,
    FieldResult,
    RunReport,
    SourceKind,
)
from scripts import export_types

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "fixtures" / "sample_job.json"
TYPES_TS = ROOT / "frontend" / "src" / "api" / "types.ts"

# Keys that may never appear in a counters-only structure (PLAN §7.1).
FORBIDDEN_KEYS = {
    "value",
    "values",
    "text",
    "payload",
    "first_name",
    "last_name",
    "national_id",
    "central_number",
    "commitment_number",
    "receipt_date",
    "image",
}


def _model_classes() -> list[type[BaseModel]]:
    classes: list[type[BaseModel]] = []
    for module in (models, schemas):
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseModel) and obj.__module__ == module.__name__:
                classes.append(obj)
    return classes


def _load_fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def _field_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "field": "commitment_number",
        "value": None,
        "display_value": None,
        "level": "C",
        "score": 0.5,
        "source": "printed",
        "box": None,
        "candidates": [],
        "validations": [],
        "trail": [],
        "reason": None,
        "escalated": False,
    }
    base.update(overrides)
    return base


def test_sample_job_fixture_validates() -> None:
    job = JobView.model_validate(_load_fixture())
    assert len(job.rows) == 4
    assert job.orphan_page_ids == ["fx#7"]


def test_sample_job_fixture_contains_all_four_levels() -> None:
    job = JobView.model_validate(_load_fixture())
    levels = {f.level for r in job.rows for f in r.fields.values()}
    assert levels == {ConfidenceLevel.A, ConfidenceLevel.B, ConfidenceLevel.C, ConfidenceLevel.D}


def test_sample_job_fixture_row_two_is_level_d() -> None:
    job = JobView.model_validate(_load_fixture())
    assert job.rows[2].row_level is ConfidenceLevel.D


def test_sample_job_fixture_keeps_commitment_letter() -> None:
    job = JobView.model_validate(_load_fixture())
    assert job.rows[1].fields[FieldKey.commitment_number].value == "Q480215"


def test_sample_job_fixture_empty_commitment_is_flagged() -> None:
    job = JobView.model_validate(_load_fixture())
    field = job.rows[3].fields[FieldKey.commitment_number]
    assert field.value is None
    assert field.level is ConfidenceLevel.D
    assert field.reason == REASON_TEXT["not_found"]


@pytest.mark.parametrize("cls", _model_classes(), ids=lambda c: c.__name__)
def test_every_model_is_strict_and_frozen(cls: type[BaseModel]) -> None:
    assert cls.model_config.get("extra") == "forbid"
    assert cls.model_config.get("frozen") is True


def test_strict_models_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        schemas.UploadResponse.model_validate(
            {"job_id": "j", "status": "queued", "page_count": 1, "typo_field": 1}
        )


def test_strict_models_are_immutable() -> None:
    resp = schemas.UploadResponse(job_id="j", status=models.JobStatus.queued, page_count=1)
    with pytest.raises(ValidationError):
        resp.page_count = 2


def test_field_result_allows_none_value() -> None:
    result = FieldResult.model_validate(_field_dict(value=None, level="C"))
    assert result.value is None
    assert result.source is SourceKind.printed


def test_level_d_without_reason_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FieldResult.model_validate(_field_dict(level="D", reason=None))


def test_level_d_with_free_text_reason_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FieldResult.model_validate(_field_dict(level="D", reason="something went wrong"))


@pytest.mark.parametrize("reason", sorted(REASON_TEXT.values()))
def test_level_d_accepts_every_closed_set_reason(reason: str) -> None:
    assert FieldResult.model_validate(_field_dict(level="D", reason=reason)).reason == reason


def test_reason_set_matches_plan_size() -> None:
    assert len(REASON_TEXT) == 12


@pytest.mark.parametrize(
    "payload",
    [
        {"value": "18/06/2026"},
        {"value": None},
        {"pick_candidate_index": 0},
    ],
)
def test_field_edit_accepts_exactly_one_of_value_or_index(payload: dict[str, Any]) -> None:
    assert FieldEditRequest.model_validate(payload).actor == "technician"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"value": "x", "pick_candidate_index": 1},
        {"value": None, "pick_candidate_index": 1},
        {"pick_candidate_index": -1},
    ],
)
def test_field_edit_rejects_zero_or_both(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FieldEditRequest.model_validate(payload)


def _collect_keys(schema: dict[str, Any], defs: dict[str, Any], seen: set[str]) -> set[str]:
    keys: set[str] = set()
    for name, prop in schema.get("properties", {}).items():
        keys.add(name)
        for node in [prop, *prop.get("anyOf", []), prop.get("items", {})]:
            ref = node.get("$ref")
            if ref and ref not in seen:
                seen.add(ref)
                keys |= _collect_keys(defs[ref.rsplit("/", 1)[-1]], defs, seen)
    return keys


def test_run_report_contains_no_value_fields() -> None:
    schema = RunReport.model_json_schema()
    keys = _collect_keys(schema, schema.get("$defs", {}), set())
    assert keys
    assert keys.isdisjoint(FORBIDDEN_KEYS)


def test_sample_job_report_has_no_value_keys() -> None:
    report = _load_fixture()["report"]

    def walk(node: Any) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for v in node.values() for k in walk(v)}
        if isinstance(node, list):
            return {k for v in node for k in walk(v)}
        return set()

    assert walk(report).isdisjoint(FORBIDDEN_KEYS)


@pytest.mark.parametrize("cls", _model_classes(), ids=lambda c: c.__name__)
def test_every_model_emits_json_schema(cls: type[BaseModel]) -> None:
    json.dumps(cls.model_json_schema())


def test_fixture_round_trips_through_json() -> None:
    job = JobView.model_validate(_load_fixture())
    again = JobView.model_validate_json(job.model_dump_json())
    assert again == job


def test_error_codes_are_stable_and_unique() -> None:
    codes = [e.code for e in errors.ALL_ERRORS]
    assert sorted(codes) == sorted(
        [
            "UNSUPPORTED_MEDIA",
            "TOO_LARGE",
            "ENCRYPTED_PDF",
            "ACTIVE_CONTENT_PDF",
            "RENDER_FAILED",
            "OCR_UNAVAILABLE",
            "PROVIDER_RATE_LIMITED",
            "PROVIDER_INVALID_KEY",
            "PROVIDER_BAD_RESPONSE",
            "PROVIDER_EXHAUSTED",
            "JOB_PURGED",
            "JOB_NOT_FOUND",
            "EDIT_CONFLICT",
        ]
    )
    assert len(set(codes)) == len(codes)


def test_error_codes_are_upper_snake_case() -> None:
    assert all(re.fullmatch(r"[A-Z]+(_[A-Z]+)*", e.code) for e in errors.ALL_ERRORS)


@pytest.mark.parametrize(
    ("exc", "status"), [(errors.TooLarge, 413), (errors.UnsupportedMedia, 415)]
)
def test_safelinerror_maps_to_status(exc: type[errors.SafeLineError], status: int) -> None:
    assert exc().http_status == status


def test_provider_exhausted_is_recoverable() -> None:
    assert errors.ProviderExhausted.recoverable is True


def test_every_error_subclasses_safelinerror() -> None:
    assert all(issubclass(e, errors.SafeLineError) for e in errors.ALL_ERRORS)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "event": "page",
            "data": {
                "page_id": "scan#3",
                "label": "A3",
                "stage": "S4",
                "status": "running",
                "pct": 42,
            },
        },
        {
            "event": "counters",
            "data": {
                "pages_done": 7,
                "pages_total": 30,
                "fields_closed_local": 41,
                "crops_sent": 9,
            },
        },
        {
            "event": "row",
            "data": {"row_id": "r-004", "row_level": "D", "flagged_fields": ["receipt_date"]},
        },
        {"event": "done", "data": {"status": "needs_review", "flagged": 3, "duration_ms": 168000}},
        {
            "event": "error",
            "data": {"code": "PROVIDER_EXHAUSTED", "page_id": "scan#12", "recoverable": True},
        },
    ],
)
def test_plan_sse_examples_validate(payload: dict[str, Any]) -> None:
    matches = [
        t
        for t in schemas.SSE_EVENT_TYPES
        if t.model_fields["event"].annotation
        and payload["event"] in str(t.model_fields["event"].annotation)
    ]
    assert len(matches) == 1
    matches[0].model_validate(payload)


def test_generated_typescript_is_current() -> None:
    committed = TYPES_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == export_types.render()


def test_generated_typescript_exports_key_contracts() -> None:
    ts = export_types.render()
    for name in (
        "export interface JobView",
        "export interface FieldResult",
        "export type ConfidenceLevel",
        "export type SseEvent",
        "export type Box",
    ):
        assert name in ts
