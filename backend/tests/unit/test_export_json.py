from __future__ import annotations

import json

import pytest

from backend.adapters.export.csv_exp import CsvExporter
from backend.adapters.export.json_exp import JsonExporter
from backend.adapters.export.xlsx import XlsxExporter
from backend.adapters.fakes import FakeExporter
from backend.core.ports.exporter import ExporterPort
from backend.tests.unit.export_helpers import make_report, make_row


def test_matches_api_results_shape() -> None:
    rows = [make_row("r1"), make_row("r2")]
    payload = json.loads(JsonExporter().render(make_report(), rows))
    assert payload["rows"] == [r.model_dump(mode="json") for r in rows]
    assert payload["report"]["job_id"] == "job-1"


def test_empty_job_gives_valid_json() -> None:
    payload = json.loads(JsonExporter().render(make_report(), []))
    assert payload["rows"] == []


@pytest.mark.parametrize(
    "exporter", [JsonExporter(), CsvExporter(), XlsxExporter(), FakeExporter()]
)
def test_exporters_all_satisfy_port(exporter: ExporterPort) -> None:
    assert isinstance(exporter, ExporterPort)
    assert isinstance(exporter.render(make_report(), [make_row()]), bytes)
    assert exporter.fmt and exporter.media_type
