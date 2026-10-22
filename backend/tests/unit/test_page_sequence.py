"""Explicit page markers ("עמוד N מתוך M") - one of S10's three grouping signals."""

# ruff: noqa: RUF001  (Hebrew letters are not confusable text here)

from __future__ import annotations

import pytest

from backend.core.pipeline.page_sequence import find_page_sequence


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("עמוד 2 מתוך 3", (2, 3)),
        ("דף 1 מתוך 2", (1, 2)),
        ("שם החולה עמוד 12 מתוך 15 תאריך", (12, 15)),
        ("Page 2 of 4", (2, 4)),
        ("page 3/3", (3, 3)),
        ("עמוד2מתוך3", (2, 3)),
    ],
)
def test_marker_is_read(text: str, expected: tuple[int, int]) -> None:
    assert find_page_sequence(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "no marker here", "עמוד 4 מתוך 3", "עמוד 0 מתוך 3", "מתוך 3", "עמוד 2", "1234 מתוך 5678"],
)
def test_implausible_or_missing_marker_is_none(text: str) -> None:
    assert find_page_sequence(text) is None


def test_first_plausible_marker_wins() -> None:
    assert find_page_sequence("עמוד 9 מתוך 2 ואז עמוד 1 מתוך 2") == (1, 2)
