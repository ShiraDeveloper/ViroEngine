from __future__ import annotations

from datetime import date

import pytest

from backend.core.validators import (
    date_not_future,
    israeli_id_ok,
    pad_id,
    parse_il_date,
    pattern_fit,
    repair_confusable,
    shape_of,
    strip_surplus_leading_zeros,
)

FAKE_NOW = date(2026, 8, 20)  # fixed "today" so future checks are deterministic


@pytest.mark.parametrize("raw", ["011223344", "11223344"])
def test_valid_id_passes(raw: str) -> None:
    assert israeli_id_ok(raw)


def test_ten_digit_id_with_surplus_leading_zero_passes() -> None:
    assert israeli_id_ok("0011223344")
    assert pad_id("0011223344") == "011223344"


def test_ten_digits_of_real_data_rejected() -> None:
    assert not israeli_id_ok("1234567890")
    assert pad_id("1234567890") is None


def test_leading_zero_stripped_is_recorded() -> None:
    raw = "0011223344"
    stripped = strip_surplus_leading_zeros(raw)
    assert stripped == "011223344"
    assert stripped != raw  # the caller can emit a ValidationOutcome from the difference


def test_invalid_id_fails() -> None:
    assert not israeli_id_ok("011223345")


def test_id_non_numeric_stripped() -> None:
    assert israeli_id_ok("011-223 344")
    assert not israeli_id_ok("abc")
    assert not israeli_id_ok("")


def test_stamp_date_with_spaced_digits() -> None:
    assert parse_il_date("1 8 -06- 2026") == date(2026, 6, 18)


def test_two_digit_year() -> None:
    assert parse_il_date("18/06/26") == date(2026, 6, 18)


@pytest.mark.parametrize("raw", ["31/02/2026", "", "no date", "32/01/2026"])
def test_impossible_date_returns_none(raw: str) -> None:
    assert parse_il_date(raw) is None


def test_future_date_fails_hard() -> None:
    assert not date_not_future(date(2026, 8, 21), now=FAKE_NOW)
    assert date_not_future(FAKE_NOW, now=FAKE_NOW)
    assert date_not_future(date(2026, 6, 18), now=FAKE_NOW)


def test_repair_unique() -> None:
    assert repair_confusable("011223394", israeli_id_ok) == ("011223344", "repaired_1")


def test_repair_as_read() -> None:
    assert repair_confusable("011223344", israeli_id_ok) == ("011223344", "as_read")


def test_repair_ambiguous() -> None:
    # Found by exhaustive search: the PLAN's example '011228844' has no single-swap survivor.
    assert repair_confusable("000007919", israeli_id_ok) == (None, "ambiguous")


def test_repair_unrepairable() -> None:
    assert repair_confusable("111111111", israeli_id_ok) == (None, "unrepairable")


def test_repair_respects_swap_cap() -> None:
    value, status = repair_confusable("011223394", lambda s: False)
    assert value is None
    assert status == "unrepairable"


@pytest.mark.parametrize("value", ["", "garbage", "X132767", "000"])
def test_pattern_fit_never_rejects(value: str) -> None:
    assert 0.0 < pattern_fit(value, [r"[A-Z]\d{6}", "[unclosed"]) <= 1.0


def test_pattern_fit_neutral_without_hints() -> None:
    assert pattern_fit("anything", []) == 0.5


def test_pattern_fit_levels() -> None:
    assert pattern_fit("X132767", [r"[A-Z]\d{6}"]) == 1.0
    assert pattern_fit("7254627", ["#######"]) == 0.75  # learned shape, not a regex
    assert pattern_fit("abc", [r"\d{6}"]) == 0.25


def test_shape_of_preserves_letters() -> None:
    assert shape_of("X132767") == "A######"
    assert shape_of("011223344") == "#########"
    assert shape_of("000351/2026") == "######/####"
