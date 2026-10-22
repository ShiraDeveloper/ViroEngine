"""The learning layer never holds a value (project rule §7, PLAN §7.1, T24).

A person's name, ID, date or a form's numbers must not be reachable from the store: not through
the model, not through the gate, not in any table and not in the bytes of the database file.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend.adapters.learning.sqlite_store import SqliteLearningStore
from backend.core.learning import FORBIDDEN_KEYS, PiiLeak, assert_pii_free, observations_from_row
from backend.core.models import (
    FieldKey,
    LayoutObservation,
    RegionKind,
    RunMetric,
    SourceKind,
)
from backend.core.pipeline.s10_rows import assemble_rows
from backend.settings import Settings
from backend.tests.fixtures.api import HEADERS, Harness, running_app, seed_job
from backend.tests.fixtures.rows import ctx, page

SENDER = "בית חולים הדסה"
OBSERVATION = LayoutObservation(
    sender=SENDER,
    field=FieldKey.central_number,
    region_kind=RegionKind.central_sticker,
    box=(0.1, 0.1, 0.2, 0.2),
    source=SourceKind.barcode,
)
# Values a form carries; none may be found anywhere in the store.
SECRETS = ("937309", "937310", "X132767", "132767", "011223344", "18/06/2026", "דנה", "כהן")


@pytest.fixture
def store(tmp_path: Path) -> SqliteLearningStore:
    return SqliteLearningStore(settings=Settings(_env_file=None, data_dir=str(tmp_path)))  # type: ignore[call-arg]


def leaking(key: str) -> LayoutObservation:
    """An observation class that grew a forbidden field, as a careless change would make it."""
    cls = type(
        "LeakyObservation",
        (LayoutObservation,),
        {"__annotations__": {key: str}, key: "937309"},
    )
    return cls(**OBSERVATION.model_dump(), **{key: "937309"})  # type: ignore[no-any-return]


def test_rejects_payload_with_value_key(store: SqliteLearningStore) -> None:
    with pytest.raises(PiiLeak, match="value"):
        store.record_confirmation(leaking("value"))

    assert store.dump_tables()["layout_hint"] == [], "nothing was written"


def test_rejects_national_id_key(store: SqliteLearningStore) -> None:
    with pytest.raises(PiiLeak, match="national_id"):
        store.record_confirmation(leaking("national_id"))


@pytest.mark.parametrize("key", sorted(FORBIDDEN_KEYS))
def test_every_forbidden_key_is_refused(store: SqliteLearningStore, key: str) -> None:
    if key in LayoutObservation.model_fields:
        pytest.skip("not a field of the observation")

    with pytest.raises(PiiLeak):
        store.record_confirmation(leaking(key))
    assert all(rows == [] for rows in store.dump_tables().values())


def test_a_leak_is_a_hard_failure_not_a_log_line() -> None:
    assert issubclass(PiiLeak, ValueError)
    with pytest.raises(PiiLeak):
        assert_pii_free(leaking("text"))


def test_observation_model_has_no_value_field() -> None:
    assert not (FORBIDDEN_KEYS & set(LayoutObservation.model_fields))
    with pytest.raises(ValidationError):
        LayoutObservation.model_validate({**OBSERVATION.model_dump(), "value": "937309"})
    assert LayoutObservation.model_config.get("extra") == "forbid"


def test_observation_type_is_frozen() -> None:
    with pytest.raises(ValidationError):
        OBSERVATION.sender = "someone else"  # type: ignore[misc]


def test_run_metric_contains_no_values(store: SqliteLearningStore) -> None:
    store.record_run(
        RunMetric(
            at=datetime(2026, 10, 22, tzinfo=UTC),
            fields_total=42,
            fields_closed_local=30,
            fields_escalated=5,
            fields_flagged=7,
            local_close_rate=0.71,
            provider_calls=2,
            engine_version="0.1.0",
        )
    )

    (row,) = store.run_metrics()

    assert set(row) == {
        "id", "at", "fields_total", "fields_closed_local", "fields_escalated", "fields_flagged",
        "local_close_rate", "provider_calls", "engine_version",
    }  # fmt: skip
    assert all(isinstance(v, int | float | str) for v in row.values())
    assert not (FORBIDDEN_KEYS & set(RunMetric.model_fields))


def test_a_dump_of_every_table_and_the_database_file_shows_no_form_value(
    store: SqliteLearningStore,
) -> None:
    pages = [
        page(
            "p1",
            centrals=["937309", "937310"],
            date="18/06/2026",
            commitments=["X132767"],
            first_name="דנה",
            last_name="כהן",
        )
    ]
    (row, *_), _, _ = assemble_rows(pages, ctx=ctx())
    observations = observations_from_row(row, pages)
    assert observations, "the row taught something"
    for obs in observations * 3:
        store.record_confirmation(obs)
    store.propose_sender("מכון חדש", ["מכון"])

    tables = json.dumps(store.dump_tables(), ensure_ascii=False)
    raw = b"".join(p.read_bytes() for p in store._path.parent.glob("learning.db*"))

    for secret in SECRETS:
        assert secret not in tables, f"{secret!r} is in a table"
        assert secret.encode("utf-8") not in raw, f"{secret!r} is in the database file"
    assert "A######" in tables, "the shape was kept"


def test_seed_export_carries_only_boxes_and_shapes(store: SqliteLearningStore) -> None:
    for obs in [OBSERVATION] * 3:
        store.record_confirmation(obs)

    seed = json.dumps(store.seed(), ensure_ascii=False)

    assert not any(secret in seed for secret in SECRETS)
    assert set(json.loads(seed)) == {"layout_hints", "pattern_hints"}


# --- through the API --------------------------------------------------------------------------


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    with running_app(monkeypatch, tmp_path) as harness:
        harness.app.state.container = dataclasses.replace(
            harness.container,
            learning=SqliteLearningStore(
                settings=Settings(_env_file=None, data_dir=str(tmp_path / "learn"))  # type: ignore[call-arg]
            ),
        )
        yield harness


def learned(h: Harness) -> SqliteLearningStore:
    store = h.app.state.container.learning
    assert isinstance(store, SqliteLearningStore)
    return store


def test_confirm_hands_locations_to_the_learning_layer_and_nothing_else(h: Harness) -> None:
    entry = seed_job(h)

    assert h.client.post(f"/v1/jobs/{entry.job_id}/confirm", headers=HEADERS).status_code == 200

    tables = learned(h).dump_tables()
    assert tables["layout_hint"], "the confirmed rows taught their locations"
    assert all(r["hits"] >= 1 for r in tables["layout_hint"])
    dumped = json.dumps(tables, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in dumped


def test_three_confirmed_jobs_promote_the_layout(h: Harness) -> None:
    for _ in range(3):
        entry = seed_job(h)
        h.client.post(f"/v1/jobs/{entry.job_id}/confirm", headers=HEADERS)

    hints = learned(h).layout_hints(SENDER, "central_number")

    assert len(hints) == 1 and hints[0].region_kind is RegionKind.central_sticker


def test_a_leak_stops_the_confirmation(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = seed_job(h)
    monkeypatch.setattr(
        "backend.api.review.observations_from_row", lambda row, pages: [leaking("value")]
    )

    tolerant = TestClient(h.app, raise_server_exceptions=False)

    response = tolerant.post(f"/v1/jobs/{entry.job_id}/confirm", headers=HEADERS)

    assert response.status_code == 500
    assert h.store.lookup(entry.job_id).record.status.value == "needs_review", "not confirmed"
    assert all(rows == [] for rows in learned(h).dump_tables().values())


def test_the_learning_curve_endpoint_returns_the_series(h: Harness) -> None:
    for rate in (0.61, 0.74):
        learned(h).record_run(
            RunMetric(
                at=datetime(2026, 10, 22, tzinfo=UTC),
                fields_total=100,
                fields_closed_local=int(rate * 100),
                fields_escalated=10,
                fields_flagged=5,
                local_close_rate=rate,
                provider_calls=3,
                engine_version="0.1.0",
            )
        )

    series = h.client.get("/v1/metrics/learning", headers=HEADERS).json()

    assert [p["local_close_rate"] for p in series] == [0.61, 0.74]


def test_the_seed_and_proposals_endpoints_need_the_api_key(h: Harness) -> None:
    for path in ("/v1/metrics/learning", "/v1/learning/seed", "/v1/learning/proposals"):
        assert h.client.get(path).status_code == 401


def test_approving_a_proposal_is_an_explicit_call_and_unknown_ones_are_404(h: Harness) -> None:
    learned(h).propose_sender("מכון חדש", ["מכון"])
    (proposal,) = h.client.get("/v1/learning/proposals", headers=HEADERS).json()["senders"]
    assert proposal["approved"] == 0

    approved = h.client.post(
        f"/v1/learning/proposals/sender/{proposal['id']}/approve", headers=HEADERS
    )
    missing = h.client.post("/v1/learning/proposals/label/999/approve", headers=HEADERS)

    assert (approved.status_code, missing.status_code) == (200, 404)
    assert missing.json()["error"] == "PROPOSAL_NOT_FOUND"
