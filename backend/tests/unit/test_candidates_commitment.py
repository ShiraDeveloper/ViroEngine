"""Tests for S6 commitment_number candidates (T12), one per PLAN §2.7 trap.

test_meuhedet_header_number            - A1: 'התחייבות מספר 730578626'
test_laniado_letter_preserved          - B14: X132767 keeps its letter and its case
test_barzilai_handwritten              - B6: a handwritten value after 'מס' התחייבות'
test_maccabi_karmiel_drisha_label      - A9: the 'דרישה' synonym
test_kelita_is_rejected                - 'מס' קליטה' never yields a commitment
test_case_and_visit_numbers_rejected   - 'מקרה' / 'מס' ביקור'
test_fax_number_rejected               - footer fax number
test_rosenblatt_returns_empty_list     - A17: no commitment exists; [] is correct (hard gate)
test_sap_multirow_yields_multiple      - B20: one candidate per table row
"""

# ruff: noqa: RUF001  (Hebrew letters are not confusable text here)

from __future__ import annotations

import pytest

from backend.adapters.senders.loader import SenderIndex
from backend.core.models import Box, RegionKind, SenderSpec, SourceKind
from backend.core.pipeline.s6_candidates_identity import candidates_commitment
from backend.tests.fixtures.layout import barcode, page_ocr, region, row


@pytest.fixture(scope="module")
def index() -> SenderIndex:
    return SenderIndex.load()


def _spec(index: SenderIndex, canonical: str) -> SenderSpec:
    spec = index.get(canonical)
    assert spec is not None, canonical
    return spec


def _values(candidates: list) -> list[str]:  # type: ignore[type-arg]
    return [c.value for c in candidates]


def test_meuhedet_header_number(index: SenderIndex) -> None:
    ocr = page_ocr(row(0.1, ["התחייבות", "מספר", "730578626"]))
    found = candidates_commitment(ocr, [], [], _spec(index, "מאוחדת"), "primary_form")
    assert _values(found) == ["730578626"]
    assert found[0].source is SourceKind.printed


def test_laniado_letter_preserved(index: SenderIndex) -> None:
    ocr = page_ocr(row(0.3, ["מספר", "התחייבות", "X132767"]))
    found = candidates_commitment(ocr, [], [], _spec(index, "לניאדו"), "primary_form")
    assert _values(found) == ["X132767"]


def test_barzilai_handwritten(index: SenderIndex) -> None:
    ocr = page_ocr(row(0.3, ["מס'", "התחייבות", "11001"]))
    ink: Box = ocr.words[2].box
    found = candidates_commitment(
        ocr, [], [], _spec(index, "ברזילי"), "primary_form", ink_boxes=[ink]
    )
    assert _values(found) == ["11001"]
    assert found[0].source is SourceKind.handwritten


def test_maccabi_karmiel_drisha_label(index: SenderIndex) -> None:
    ocr = page_ocr(row(0.3, ["דרישה:", "50364186"]))
    found = candidates_commitment(ocr, [], [], _spec(index, "מכבי שירותי בריאות"), "primary_form")
    assert _values(found) == ["50364186"]


def test_two_token_value_is_joined(index: SenderIndex) -> None:
    ocr = page_ocr(row(0.3, ["מספר", "התחייבות", "2648", "00879"]))
    found = candidates_commitment(ocr, [], [], _spec(index, "הלל יפה"), "primary_form")
    assert _values(found) == ["2648 00879"]


def test_value_stops_before_a_word_without_digits(index: SenderIndex) -> None:
    ocr = page_ocr(row(0.3, ["מספר", "התחייבות", "730578626", "תאריך", "12/03/2026"]))
    found = candidates_commitment(ocr, [], [], _spec(index, "לניאדו"), "primary_form")
    assert _values(found) == ["730578626"]


def test_kelita_is_rejected(index: SenderIndex) -> None:
    ocr = page_ocr(row(0.3, ["מס'", "קליטה", "654120"]))
    assert candidates_commitment(ocr, [], [], _spec(index, "לניאדו"), "primary_form") == []
    assert candidates_commitment(ocr, [], [], _spec(index, "מעבדת רוזנבלט"), "primary_form") == []


@pytest.mark.parametrize(
    "label", [["מקרה"], ["מס'", "ביקור"], ["מספר", "ביקור"], ["מספר", "מבחנה"]]
)
def test_case_and_visit_numbers_rejected(index: SenderIndex, label: list[str]) -> None:
    ocr = page_ocr(row(0.3, [*label, "987654"]))
    assert candidates_commitment(ocr, [], [], _spec(index, "לניאדו"), "primary_form") == []


def test_deny_label_does_not_hide_the_real_commitment(index: SenderIndex) -> None:
    ocr = page_ocr(row(0.2, ["מקרה", "222222"]), row(0.4, ["מספר", "התחייבות", "111111"]))
    found = candidates_commitment(ocr, [], [], _spec(index, "לניאדו"), "primary_form")
    assert _values(found) == ["111111"]


def test_fax_number_rejected(index: SenderIndex) -> None:
    ocr = page_ocr(row(0.95, ["פקס:", "03-1234567"]))
    assert candidates_commitment(ocr, [], [], _spec(index, "לניאדו"), "primary_form") == []


def test_catalogue_number_far_from_any_label_is_not_a_candidate(index: SenderIndex) -> None:
    ocr = page_ocr(row(0.3, ["מספר", "התחייבות"]), row(0.95, ["6541201007"], x_right=0.3))
    assert candidates_commitment(ocr, [], [], _spec(index, "לניאדו"), "primary_form") == []


def test_rosenblatt_returns_empty_list(index: SenderIndex) -> None:
    spec = _spec(index, "מעבדת רוזנבלט")
    ocr = page_ocr(row(0.3, ["מס'", "קליטה", "654120"]), row(0.4, ["מספר", "התחייבות", "111"]))
    stray = barcode("66608446", (0.5, 0.1, 0.55, 0.4), ["מספר", "התחייבות"])
    assert candidates_commitment(ocr, [stray], [], spec, "primary_form") == []


def test_unknown_sender_uses_generic_labels() -> None:
    ocr = page_ocr(row(0.3, ["מספר", "התחייבות", "555123"]))
    assert _values(candidates_commitment(ocr, [], [], None, "primary_form")) == ["555123"]


def test_value_below_label_when_line_is_empty(index: SenderIndex) -> None:
    ocr = page_ocr(row(0.3, ["מספר", "התחייבות"]), row(0.33, ["424242"], x_right=0.85))
    found = candidates_commitment(ocr, [], [], _spec(index, "לניאדו"), "primary_form")
    assert _values(found) == ["424242"]


def _sap_page(rows: list[str]) -> tuple[object, list]:  # type: ignore[type-arg]
    header = row(0.2, ["מספר", "התחייבות"], x_right=0.6)
    cells = [row(0.3 + 0.05 * i, [v], x_right=0.55) for i, v in enumerate(rows)]
    other = row(0.3, ["יום"], x_right=0.9)
    table = region(RegionKind.table, (0.15, 0.05, 0.9, 0.95))
    return page_ocr(header, other, *cells), [table]


def test_sap_multirow_yields_multiple(index: SenderIndex) -> None:
    ocr, regions = _sap_page(["1111111", "2222222"])
    found = candidates_commitment(ocr, [], regions, _spec(index, "לניאדו"), "sap_screen")  # type: ignore[arg-type]
    assert _values(found) == ["1111111", "2222222"]
    assert {c.source for c in found} == {SourceKind.screen}


def test_sap_column_excludes_other_columns(index: SenderIndex) -> None:
    ocr, regions = _sap_page(["1111111"])
    found = candidates_commitment(ocr, [], regions, _spec(index, "לניאדו"), "sap_screen")  # type: ignore[arg-type]
    assert "יום" not in _values(found)


def test_sap_rows_only_on_sap_screen_role(index: SenderIndex) -> None:
    ocr, regions = _sap_page(["1111111", "2222222"])
    found = candidates_commitment(ocr, [], regions, _spec(index, "לניאדו"), "primary_form")  # type: ignore[arg-type]
    assert len(found) < 2


def test_barcode_used_when_spec_says_so(index: SenderIndex) -> None:
    hit = barcode("260630224", (0.5, 0.1, 0.55, 0.4))
    found = candidates_commitment(
        page_ocr(), [hit], [], _spec(index, "מכבי שירותי בריאות"), "primary_form"
    )
    assert _values(found) == ["260630224"]
    assert found[0].source is SourceKind.barcode


def test_barcode_ignored_when_spec_says_no(index: SenderIndex) -> None:
    hit = barcode("260630224", (0.5, 0.1, 0.55, 0.4), ["התחייבות"])
    assert (
        candidates_commitment(page_ocr(), [hit], [], _spec(index, "מאוחדת"), "primary_form") == []
    )


def test_barcode_hint_needs_a_nearby_commitment_label(index: SenderIndex) -> None:
    spec = _spec(index, "לניאדו")  # barcode_is_commitment is None
    bare = barcode("111111", (0.5, 0.1, 0.55, 0.4))
    labelled = barcode("222222", (0.6, 0.1, 0.65, 0.4), ["מספר", "התחייבות"])
    found = candidates_commitment(page_ocr(), [bare, labelled], [], spec, "primary_form")
    assert _values(found) == ["222222"]
    assert "barcode_hint" in found[0].notes


def test_barcode_in_central_sticker_is_not_a_commitment(index: SenderIndex) -> None:
    sticker = region(RegionKind.central_sticker, (0.1, 0.05, 0.3, 0.4))
    hit = barcode("937309", (0.15, 0.1, 0.2, 0.3))
    found = candidates_commitment(
        page_ocr(), [hit], [sticker], _spec(index, "מכבי שירותי בריאות"), "primary_form"
    )
    assert found == []


def test_barcode_next_to_deny_label_is_rejected(index: SenderIndex) -> None:
    hit = barcode("7254627", (0.5, 0.1, 0.55, 0.4), ["מס'", "ביקור"])
    found = candidates_commitment(
        page_ocr(), [hit], [], _spec(index, "מכבי שירותי בריאות"), "primary_form"
    )
    assert found == []
