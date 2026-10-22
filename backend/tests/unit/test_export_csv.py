from __future__ import annotations

import csv
import io

import pytest

from backend.adapters.export.csv_exp import CsvExporter
from backend.adapters.export.table import HEADERS
from backend.tests.unit.export_helpers import make_report, make_row


def _parse(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def test_utf8_bom_present() -> None:
    data = CsvExporter().render(make_report(), [make_row()])
    assert data.startswith(b"\xef\xbb\xbf")


def test_header_and_values_round_trip() -> None:
    table = _parse(CsvExporter().render(make_report(), [make_row(centrals=("1", "2"))]))
    assert tuple(table[0]) == HEADERS
    assert table[1][2] == "011223344"
    assert table[1][3] == "1; 2"
    assert table[1][6] == "X132767"


@pytest.mark.parametrize("name", ["=1+1", "+cmd", "-x", "@sum"])
def test_formula_prefix_is_neutralised(name: str) -> None:
    table = _parse(CsvExporter().render(make_report(), [make_row(commitment=name)]))
    assert table[1][6] == f"'{name}"
