"""ExporterPort — contract for result exporters (xlsx, csv, json).

Exporters are rendered on demand and never stored. PII is present
in the rendered bytes and must not be logged or cached by the exporter.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.core.models import CommitmentRow, RunReport


@runtime_checkable
class ExporterPort(Protocol):
    """Render a job result to bytes in a specific format."""

    fmt: str  # "xlsx" | "csv" | "json"
    media_type: str  # MIME type, e.g. "application/vnd.openxmlformats-..."

    def render(self, report: RunReport, rows: list[CommitmentRow]) -> bytes:
        """Render the complete result to bytes.

        report: job counters (no PII).
        rows: commitment rows with field results and candidates.
        Returns serialised bytes ready for streaming to the client.
        Never raises on valid input; an empty job produces valid empty output.
        """
        ...
