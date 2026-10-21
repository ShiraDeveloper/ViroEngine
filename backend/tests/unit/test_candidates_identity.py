"""Tests for S6 national_id and name candidates (T12).

test_patient_id_found_from_blue_handwriting      - ID after its label; an ink hint marks it handwritten
test_father_id_rejected / _doctor_license_       - deny labels win; no value is reached
test_seven_digit_id_accepted_as_candidate        - a lost leading zero never rejects on length
test_ten_digit_id_accepted_as_candidate          - a surplus leading zero never rejects on length
test_id_from_barcode_in_patient_box              - A10: the barcode is a candidate
test_father_name_rejected                        - deny label wins
test_combined_name_label_returns_single_candidate_with_note - order of the parts is never guessed
"""

# ruff: noqa: RUF001  (Hebrew letters are not confusable text here)

from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from backend.core.models import Box, FieldKey, RegionKind, SourceKind
from backend.core.pipeline.s6_candidates import generate_candidates
from backend.core.pipeline.s6_candidates_identity import (
    NOTE_COMBINED_NAME,
    IdentityConfig,
    candidates_names,
    candidates_national_id,
)
from backend.tests.fixtures.layout import barcode, page_ocr, patient_box, region, row


def _values(candidates: list) -> list[str]:  # type: ignore[type-arg]
    return [c.value for c in candidates]


def test_patient_id_found_from_blue_handwriting() -> None:
    ocr = page_ocr(row(0.3, ["ת.ז", "011223344"]))
    value_box: Box = ocr.words[1].box
    found = candidates_national_id(ocr, [], [patient_box()], ink_boxes=[value_box])
    assert _values(found) == ["011223344"]
    assert found[0].source is SourceKind.handwritten
    assert found[0].region is RegionKind.patient_box


def test_id_without_ink_hint_is_printed() -> None:
    found = candidates_national_id(page_ocr(row(0.3, ["ת.ז", "011223344"])), [], [patient_box()])
    assert found[0].source is SourceKind.printed


def test_father_id_rejected() -> None:
    ocr = page_ocr(row(0.3, ["ת.ז", "האב", "123456782"]))
    assert candidates_national_id(ocr, [], [patient_box()]) == []


def test_father_id_does_not_hide_patient_id() -> None:
    ocr = page_ocr(row(0.3, ["ת.ז", "011223344"]), row(0.4, ["ת.ז", "האב", "123456782"]))
    assert _values(candidates_national_id(ocr, [], [patient_box()])) == ["011223344"]


def test_doctor_license_rejected() -> None:
    ocr = page_ocr(row(0.3, ["מספר", "רישיון", "12345678"]))
    assert candidates_national_id(ocr, [], [patient_box()]) == []


def test_physician_id_rejected() -> None:
    ocr = page_ocr(row(0.3, ["ת.ז", "רופא", "123456782"]))
    assert candidates_national_id(ocr, [], [patient_box()]) == []


def test_seven_digit_id_accepted_as_candidate() -> None:
    ocr = page_ocr(row(0.3, ["ת.ז", "1122334"]))
    assert _values(candidates_national_id(ocr, [], [patient_box()])) == ["1122334"]


def test_ten_digit_id_accepted_as_candidate() -> None:
    ocr = page_ocr(row(0.3, ["ת.ז", "0011223344"]))
    assert _values(candidates_national_id(ocr, [], [patient_box()])) == ["0011223344"]


def test_id_split_into_groups_is_joined_with_note() -> None:
    # Digits read left to right, so the first group is the leftmost token on the line.
    ocr = page_ocr(row(0.3, ["תעודת", "זהות", "344", "223", "011"]))
    found = candidates_national_id(ocr, [], [patient_box()])
    assert _values(found) == ["011223344"]
    assert "digits_joined" in found[0].notes


@pytest.mark.parametrize("digits", ["123456", "12345678901"])
def test_digit_count_outside_window_gives_no_candidate(digits: str) -> None:
    ocr = page_ocr(row(0.3, ["ת.ז", digits]))
    assert candidates_national_id(ocr, [], [patient_box()]) == []


def test_id_outside_patient_box_is_ignored() -> None:
    ocr = page_ocr(row(0.9, ["ת.ז", "011223344"]))
    assert candidates_national_id(ocr, [], [patient_box()]) == []


def test_id_without_patient_box_gives_nothing() -> None:
    assert candidates_national_id(page_ocr(row(0.3, ["ת.ז", "011223344"])), [], []) == []


def test_id_inside_clinical_region_is_ignored() -> None:
    clinical = region(RegionKind.clinical, (0.25, 0.0, 0.35, 1.0))
    ocr = page_ocr(row(0.3, ["ת.ז", "011223344"]))
    assert candidates_national_id(ocr, [], [patient_box(), clinical]) == []


def test_id_from_barcode_in_patient_box() -> None:
    hit = barcode("011223344", (0.4, 0.2, 0.45, 0.5))
    found = candidates_national_id(page_ocr(), [hit], [patient_box()])
    assert _values(found) == ["011223344"]
    assert found[0].source is SourceKind.barcode


def test_barcode_outside_patient_box_is_not_an_id() -> None:
    hit = barcode("011223344", (0.8, 0.2, 0.85, 0.5))
    assert candidates_national_id(page_ocr(), [hit], [patient_box()]) == []


def test_id_label_radius_comes_from_config() -> None:
    ocr = page_ocr(row(0.3, ["ת.ז"], x_right=0.9), row(0.3, ["011223344"], x_right=0.5))
    assert candidates_national_id(ocr, [], [patient_box()]) == []
    wide = IdentityConfig(id_label_radius=0.5, chain_gap=0.5)
    assert _values(candidates_national_id(ocr, [], [patient_box()], config=wide)) == ["011223344"]


def test_father_name_rejected() -> None:
    ocr = page_ocr(row(0.3, ["שם", "האב", "יוסף"]))
    assert candidates_names(ocr, [patient_box()]) == {}


def test_physician_and_requester_names_rejected() -> None:
    ocr = page_ocr(row(0.3, ["שם", "הרופא", "כהן"]), row(0.4, ["שם", "המזמין", "לוי"]))
    assert candidates_names(ocr, [patient_box()]) == {}


def test_combined_name_label_returns_single_candidate_with_note() -> None:
    ocr = page_ocr(row(0.3, ["שם", "דני", "כהן"]))
    names = candidates_names(ocr, [patient_box()])
    all_candidates = [c for cs in names.values() for c in cs]
    assert len(all_candidates) == 1
    assert all_candidates[0].value == "דני כהן"
    assert NOTE_COMBINED_NAME in all_candidates[0].notes


def test_specific_labels_map_to_their_fields() -> None:
    ocr = page_ocr(row(0.3, ["שם", "פרטי", "דני"]), row(0.4, ["שם", "משפחה", "כהן"]))
    names = candidates_names(ocr, [patient_box()])
    assert _values(names[FieldKey.first_name]) == ["דני"]
    assert _values(names[FieldKey.last_name]) == ["כהן"]
    assert NOTE_COMBINED_NAME not in names[FieldKey.first_name][0].notes


def test_name_value_stops_at_four_tokens() -> None:
    ocr = page_ocr(row(0.3, ["שם", "א", "ב", "ג", "ד", "ה", "ו"]))
    names = candidates_names(ocr, [patient_box()])
    assert len(names[FieldKey.first_name][0].value.split()) == 4


def test_name_value_stops_at_next_label() -> None:
    ocr = page_ocr(row(0.3, ["שם", "משפחה", "כהן", "שם", "פרטי", "דני"]))
    names = candidates_names(ocr, [patient_box()])
    assert _values(names[FieldKey.last_name]) == ["כהן"]
    assert _values(names[FieldKey.first_name]) == ["דני"]


def test_value_below_label_is_used_when_line_is_empty() -> None:
    ocr = page_ocr(row(0.3, ["שם", "פרטי"]), row(0.33, ["דני"], x_right=0.85))
    names = candidates_names(ocr, [patient_box()])
    assert _values(names[FieldKey.first_name]) == ["דני"]
    assert "value_below_label" in names[FieldKey.first_name][0].notes


def test_generate_candidates_returns_every_identity_key() -> None:
    result = generate_candidates(page_ocr(), [], [], spec=None, page_role="primary_form")
    assert set(result) == {
        FieldKey.national_id,
        FieldKey.first_name,
        FieldKey.last_name,
        FieldKey.commitment_number,
    }
    assert all(v == [] for v in result.values())


def test_identity_config_defaults_equal_models_yaml() -> None:
    path = Path(__file__).resolve().parents[3] / "config" / "models.yaml"
    section = yaml.safe_load(path.read_text(encoding="utf-8"))["identity"]
    assert IdentityConfig.model_validate(section) == IdentityConfig()
