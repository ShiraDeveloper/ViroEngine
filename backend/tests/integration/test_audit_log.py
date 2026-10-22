"""Review actions: edits, undo, confirm and the append-only audit log (PLAN §3.4, T22)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.core.models import REASON_TEXT, ConfidenceLevel, JobStatus
from backend.tests.fixtures.api import DATE, HEADERS, Harness, edit, running_app, seed_job

# Row r1 (page p1) is fully closed; row r2 (page p2) has no stamp date and is flagged.
CLOSED_ROW = "row-p1-1"
FLAGGED_ROW = "row-p2-1"
VALID_ID = "011223344"


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    with running_app(monkeypatch, tmp_path) as harness:
        yield harness


def audit(h: Harness, job_id: str) -> list[dict[str, Any]]:
    response = h.client.get(f"/v1/jobs/{job_id}/audit", headers=HEADERS)
    assert response.status_code == 200
    body: list[dict[str, Any]] = response.json()
    return body


def test_every_edit_is_recorded(h: Harness) -> None:
    entry = seed_job(h)

    typed = edit(h, entry, FLAGGED_ROW, "receipt_date", {"value": "17/06/2026", "actor": "dana"})
    picked = edit(h, entry, CLOSED_ROW, "first_name", {"pick_candidate_index": 0})
    cleared = edit(h, entry, CLOSED_ROW, "last_name", {"value": None})

    assert (typed.status_code, picked.status_code, cleared.status_code) == (200, 200, 200)
    log = audit(h, entry.job_id)
    assert [e["action"] for e in log] == ["edit", "pick_candidate", "clear"]
    assert [e["field"] for e in log] == ["receipt_date", "first_name", "last_name"]
    assert log[0]["old_value"] is None and log[0]["new_value"] == "17/06/2026"
    assert log[0]["actor"] == "dana" and log[1]["actor"] == "technician"
    assert log[2]["new_value"] is None and log[2]["old_value"] == "כהן"
    assert all(e["job_id"] == entry.job_id and e["at"] for e in log)
    assert len({e["edit_id"] for e in log}) == 3


def test_an_edit_returns_the_row_marked_edited(h: Harness) -> None:
    entry = seed_job(h)

    response = edit(h, entry, FLAGGED_ROW, "receipt_date", {"value": DATE})

    body = response.json()
    assert body["status"] == "edited"
    field = body["fields"]["receipt_date"]
    assert field["value"] == DATE and field["level"] == "B" and field["reason"] is None
    assert field["trail"][-1]["reader"] == "human"


def test_row_level_recomputed_after_edit(h: Harness) -> None:
    entry = seed_job(h)
    before = next(r for r in entry.record.rows if r.row_id == FLAGGED_ROW)
    assert before.row_level is ConfidenceLevel.D

    response = edit(h, entry, FLAGGED_ROW, "receipt_date", {"value": DATE})

    assert response.json()["row_level"] in ("A", "B")
    stored = next(r for r in h.store.lookup(entry.job_id).record.rows if r.row_id == FLAGGED_ROW)
    assert stored.row_level is not ConfidenceLevel.D


def test_clearing_a_critical_field_flags_the_row(h: Harness) -> None:
    entry = seed_job(h)

    response = edit(h, entry, CLOSED_ROW, "receipt_date", {"value": None})

    body = response.json()
    assert body["row_level"] == "D"
    assert body["fields"]["receipt_date"]["reason"] == REASON_TEXT["not_found"]


def test_undo_restores_and_is_itself_logged(h: Harness) -> None:
    entry = seed_job(h)
    original = h.client.get(f"/v1/jobs/{entry.job_id}", headers=HEADERS).json()["rows"]
    edit(h, entry, FLAGGED_ROW, "receipt_date", {"value": DATE})

    undone = h.client.post(f"/v1/jobs/{entry.job_id}/rows/{FLAGGED_ROW}/undo", headers=HEADERS)

    assert undone.status_code == 200
    assert undone.json() == next(r for r in original if r["row_id"] == FLAGGED_ROW)
    after = h.client.get(f"/v1/jobs/{entry.job_id}", headers=HEADERS).json()["rows"]
    assert after == original
    log = audit(h, entry.job_id)
    assert [e["action"] for e in log] == ["edit", "undo"], "the log is append-only"
    assert log[1]["old_value"] == DATE and log[1]["new_value"] is None


def test_undo_reverts_edits_last_first(h: Harness) -> None:
    entry = seed_job(h)
    edit(h, entry, CLOSED_ROW, "first_name", {"value": "משה"})
    edit(h, entry, CLOSED_ROW, "first_name", {"value": "יוסף"})
    url = f"/v1/jobs/{entry.job_id}/rows/{CLOSED_ROW}/undo"

    first = h.client.post(url, headers=HEADERS).json()["fields"]["first_name"]["value"]
    second = h.client.post(url, headers=HEADERS).json()["fields"]["first_name"]["value"]

    assert (first, second) == ("משה", "דנה")
    assert h.client.post(url, headers=HEADERS).status_code == 409


def test_undo_with_nothing_to_undo_is_409(h: Harness) -> None:
    entry = seed_job(h)

    response = h.client.post(f"/v1/jobs/{entry.job_id}/rows/{CLOSED_ROW}/undo", headers=HEADERS)

    assert (response.status_code, response.json()["error"]) == (409, "NOTHING_TO_UNDO")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("national_id", "123456789"),  # fails the check digit
        ("national_id", "not a number"),
        ("receipt_date", "31/02/2026"),  # does not exist
        ("receipt_date", "01/01/2099"),  # in the future
        ("receipt_date", "yesterday"),
        ("sender", "בית חולים שלא קיים"),  # not in the closed list
    ],
)
def test_a_value_that_breaks_a_hard_rule_is_refused_and_logged_nowhere(
    h: Harness, field: str, value: str
) -> None:
    entry = seed_job(h)
    before = h.client.get(f"/v1/jobs/{entry.job_id}", headers=HEADERS).json()["rows"]

    response = edit(h, entry, CLOSED_ROW, field, {"value": value})

    assert response.status_code == 422
    assert response.json()["error"] == "EDIT_INVALID"
    assert value not in response.text, "the message never echoes the value"
    assert audit(h, entry.job_id) == []
    assert h.client.get(f"/v1/jobs/{entry.job_id}", headers=HEADERS).json()["rows"] == before


def test_a_valid_id_is_padded_and_accepted(h: Harness) -> None:
    entry = seed_job(h)

    response = edit(h, entry, CLOSED_ROW, "national_id", {"value": "11223344"})

    assert response.status_code == 200
    assert response.json()["fields"]["national_id"]["value"] == VALID_ID


def test_central_numbers_can_be_replaced_by_several(h: Harness) -> None:
    entry = seed_job(h)

    response = edit(h, entry, CLOSED_ROW, "central_number", {"value": "937309; 937310 ;937309"})

    body = response.json()
    assert body["central_numbers"] == ["937309", "937310"]
    assert body["fields"]["central_number"]["value"] == "937309; 937310"


def test_a_commitment_keeps_its_letters(h: Harness) -> None:
    entry = seed_job(h)

    response = edit(h, entry, CLOSED_ROW, "commitment_number", {"value": "X 132-767"})

    assert response.json()["fields"]["commitment_number"]["value"] == "X132767"


def test_the_unlisted_sender_marker_is_accepted(h: Harness) -> None:
    entry = seed_job(h)

    response = edit(h, entry, CLOSED_ROW, "sender", {"value": "לא ברשימה"})

    assert response.status_code == 200


def test_a_candidate_out_of_range_is_422(h: Harness) -> None:
    entry = seed_job(h)

    response = edit(h, entry, CLOSED_ROW, "first_name", {"pick_candidate_index": 99})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "body", [{}, {"value": "x", "pick_candidate_index": 0}, {"pick_candidate_index": -1}]
)
def test_exactly_one_of_value_or_pick_is_required(h: Harness, body: dict[str, Any]) -> None:
    entry = seed_job(h)

    assert edit(h, entry, CLOSED_ROW, "first_name", body).status_code == 422


def test_unknown_row_and_field_are_404(h: Harness) -> None:
    entry = seed_job(h)

    assert (
        edit(h, entry, "row-zzz", "first_name", {"value": "x"}).json()["error"] == "ROW_NOT_FOUND"
    )
    assert edit(h, entry, CLOSED_ROW, "not_a_field", {"value": "x"}).status_code == 422


def test_edit_on_confirmed_job_is_409(h: Harness) -> None:
    entry = seed_job(h)
    assert h.client.post(f"/v1/jobs/{entry.job_id}/confirm", headers=HEADERS).status_code == 200

    response = edit(h, entry, CLOSED_ROW, "first_name", {"value": "x"})

    assert (response.status_code, response.json()["error"]) == (409, "EDIT_CONFLICT")
    undo = h.client.post(f"/v1/jobs/{entry.job_id}/rows/{CLOSED_ROW}/undo", headers=HEADERS)
    assert undo.status_code == 409


def test_edit_on_purged_job_is_409(h: Harness) -> None:
    entry = seed_job(h)
    h.store.purge(entry.job_id)

    response = edit(h, entry, CLOSED_ROW, "first_name", {"value": "x"})

    assert response.status_code == 409


def test_confirm_marks_rows_and_records_flagged_fields_accepted_as_they_stand(h: Harness) -> None:
    entry = seed_job(h)
    edit(h, entry, CLOSED_ROW, "first_name", {"value": "משה"})

    response = h.client.post(f"/v1/jobs/{entry.job_id}/confirm", headers=HEADERS)

    body = response.json()
    assert body["status"] == "confirmed"
    statuses = {r["row_id"]: r["status"] for r in body["rows"]}
    assert statuses == {CLOSED_ROW: "edited", FLAGGED_ROW: "confirmed"}
    accepted = [e for e in audit(h, entry.job_id) if e["action"] == "confirm"]
    assert {(e["row_id"], e["field"]) for e in accepted} == {(FLAGGED_ROW, "receipt_date")}
    assert accepted[0]["old_value"] == accepted[0]["new_value"]


def test_confirm_while_running_is_409(h: Harness) -> None:
    entry = seed_job(h, status=JobStatus.running, with_report=False)

    response = h.client.post(f"/v1/jobs/{entry.job_id}/confirm", headers=HEADERS)

    assert (response.status_code, response.json()["error"]) == (409, "JOB_NOT_READY")


def test_audit_of_an_unknown_job_is_404(h: Harness) -> None:
    assert h.client.get("/v1/jobs/nope/audit", headers=HEADERS).status_code == 404
