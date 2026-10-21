"""JSON exporter - the reference shape: the same rows GET /results returns, plus the report."""

from __future__ import annotations

import json

from backend.core.models import CommitmentRow, RunReport


class JsonExporter:
    fmt = "json"
    media_type = "application/json"

    def render(self, report: RunReport, rows: list[CommitmentRow]) -> bytes:
        """UTF-8 JSON `{"rows": [...], "report": {...}}`; an empty job gives empty rows."""
        payload = {
            "rows": [row.model_dump(mode="json") for row in rows],
            "report": report.model_dump(mode="json"),
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")
