"""CSV exporter - UTF-8 with BOM so Excel on Windows opens Hebrew correctly."""

from __future__ import annotations

import csv
import io

from backend.adapters.export.table import HEADERS, flatten
from backend.core.models import CommitmentRow, RunReport

# A leading one of these makes a spreadsheet treat the cell as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(text: str) -> str:
    """Neutralise spreadsheet formula injection by prefixing an apostrophe."""
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


class CsvExporter:
    fmt = "csv"
    media_type = "text/csv"

    def render(self, report: RunReport, rows: list[CommitmentRow]) -> bytes:
        """Header plus one line per row, CRLF terminated. The report is not part of a CSV."""
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(HEADERS)
        for row in rows:
            writer.writerow([_safe(cell.text) for cell in flatten(row).cells])
        return buffer.getvalue().encode("utf-8-sig")
