"""Flat, format-neutral view of a CommitmentRow shared by the CSV and Excel exporters.

Pure functions, no I/O. The column set and order follow the lab guideline (PLAN §9.7); a
fourth exporter reuses this module and needs no engine change.
"""

# ruff: noqa: RUF001  (Hebrew letters are not confusable text here)

from __future__ import annotations

from dataclasses import dataclass

from backend.core.models import CommitmentRow, ConfidenceLevel, FieldKey, FieldResult

CENTRALS_KEY = "central_numbers"
CENTRAL_SEPARATOR = "; "

# (Hebrew header, key). Seven fields in guideline order, then helpers the lab can hide.
COLUMNS: tuple[tuple[str, FieldKey | str], ...] = (
    ("שם פרטי", FieldKey.first_name),
    ("שם משפחה", FieldKey.last_name),
    ("מספר תעודת זהות", FieldKey.national_id),
    ("מספר מרכזי", CENTRALS_KEY),
    ("תאריך קבלת הדגימה", FieldKey.receipt_date),
    ("גורם שולח", FieldKey.sender),
    ("מספר התחייבות", FieldKey.commitment_number),
    ("גורם מממן", "payer"),
    ("סוג טופס", "family"),
    ("קובץ ועמוד מקור", "source"),
    ("ביטחון שורה", "row_level"),
    ("שדות לבדיקה", "flagged"),
    ("סיבה", "reasons"),
    ("סטטוס", "status"),
)
FIELD_COLUMN_COUNT = 7
HEADERS: tuple[str, ...] = tuple(header for header, _ in COLUMNS)

STATUS_TEXT = {"auto": "אוטומטי", "confirmed": "אושר", "edited": "נערך"}
# The row's confidence in words, with a symbol so it survives printing in black and white.
ROW_LEVEL_TEXT = {
    ConfidenceLevel.A: "✔ אומת בברקוד",
    ConfidenceLevel.B: "✔ אומת",
    ConfidenceLevel.C: "✔ זוהה",
    ConfidenceLevel.D: "⚠ לבדיקה",
}
SOURCE_TEXT = {
    "barcode": "ברקוד",
    "printed": "מודפס",
    "handwritten": "כתב יד",
    "stamp": "חותמת",
    "screen": "מסך",
    "unknown": "לא ידוע",
}


@dataclass(frozen=True)
class Cell:
    """One exported cell. `level` None means the field was absent from the row."""

    text: str
    level: ConfidenceLevel | None
    comment: str | None


@dataclass(frozen=True)
class FlatRow:
    """cells follows COLUMNS exactly; `flagged` is True when any field is level D."""

    cells: tuple[Cell, ...]
    flagged: bool


def _comment(result: FieldResult) -> str | None:
    """Reason, readers that ran and source kind for a non-green cell; None for A/B."""
    if result.level in (ConfidenceLevel.A, ConfidenceLevel.B):
        return None
    readers = list(dict.fromkeys(step.reader.value for step in result.trail))
    lines: list[str] = []
    if result.reason:
        lines.append(f"סיבה: {result.reason}")
    if readers:
        lines.append(f"קוראים: {', '.join(readers)}")
    lines.append(f"מקור: {SOURCE_TEXT.get(result.source.value, result.source.value)}")
    return "\n".join(lines)


def _field_cell(row: CommitmentRow, key: FieldKey) -> Cell:
    result = row.fields.get(key)
    if key is FieldKey.central_number:
        values = row.central_numbers or ([result.value] if result and result.value else [])
        text = CENTRAL_SEPARATOR.join(values)
    else:
        text = (result.value if result else None) or ""
    if result is None:
        return Cell(text, None, None)
    return Cell(text, result.level, _comment(result))


def _page_label(page_id: str) -> str:
    """`file#3` as `file · עמ' 3`: the page the technician opens to check the row."""
    stem, _, number = page_id.rpartition("#")
    if not stem or not number.isdigit():
        return page_id
    return f"{stem.replace('_', ' ').strip()} · עמ' {number}"


def _plain(text: str) -> Cell:
    return Cell(text, None, None)


def flatten(row: CommitmentRow) -> FlatRow:
    """Project a row onto COLUMNS. Absent values become empty strings, never guesses.
    `payer` and `family` stay empty: the exporter port does not carry group data."""
    flagged_fields = [
        (header, row.fields[key])
        for header, key in COLUMNS[:FIELD_COLUMN_COUNT]
        if isinstance(key, FieldKey)
        and key in row.fields
        and row.fields[key].level is ConfidenceLevel.D
    ]
    helpers = {
        "payer": "",
        "family": "",
        "source": "; ".join(_page_label(p) for p in row.page_ids),
        "row_level": ROW_LEVEL_TEXT[row.row_level],
        "flagged": "; ".join(header for header, _ in flagged_fields),
        "reasons": "; ".join(f"{header}: {r.reason}" for header, r in flagged_fields if r.reason),
        "status": STATUS_TEXT[row.status],
    }
    cells: list[Cell] = []
    for _, key in COLUMNS:
        if key == CENTRALS_KEY:
            cells.append(_field_cell(row, FieldKey.central_number))
        elif isinstance(key, FieldKey):
            cells.append(_field_cell(row, key))
        else:
            cells.append(_plain(helpers[key]))
    flagged = bool(flagged_fields) or row.row_level is ConfidenceLevel.D
    return FlatRow(tuple(cells), flagged)
