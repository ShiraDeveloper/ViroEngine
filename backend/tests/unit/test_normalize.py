from __future__ import annotations

from datetime import date

import pytest

from backend.core.normalize import (
    format_date,
    normalize_central,
    normalize_commitment,
    normalize_id,
    normalize_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("X 132-767", "X132767"), ("2648 00879", "264800879"), ("000351/2026", "000351/2026")],
)
def test_commitment_keeps_letter(raw: str, expected: str) -> None:
    assert normalize_commitment(raw) == expected


def test_commitment_empty_is_none() -> None:
    assert normalize_commitment(" - ") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("11223344", "011223344"),
        ("1122334", "001122334"),
        ("0011223344", "011223344"),
        ("011-223-344", "011223344"),
    ],
)
def test_id_padded_to_nine(raw: str, expected: str) -> None:
    assert normalize_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "1234567890"])
def test_id_impossible_is_none(raw: str) -> None:
    assert normalize_id(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  דוד   כהן ", "דוד כהן"),
        ("אברהמ", "אברהם"),  # medial form at word end -> final
        ("םשה", "משה"),  # final form mid-word -> medial
        ("יוסף-כהן", "יוסף-כהן"),
    ],
)
def test_name_final_letters(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_name_blank_is_none() -> None:
    assert normalize_name("   ") is None


def test_name_never_fixes_spelling() -> None:
    assert normalize_name("Jon  Smyth") == "Jon Smyth"


def test_central_strips_separators_only() -> None:
    assert normalize_central(" 937.309 ") == "937309"
    assert normalize_central("A-12 34") == "A1234"


def test_format_date_display() -> None:
    assert format_date(date(2026, 6, 18)) == "18/06/2026"
