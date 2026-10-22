"""Human edits of a row (PLAN §3.4, T22): validation, the resulting field and the row level.

A technician's decision is applied with the same two hard rules the engine uses (the national ID
check digit; a date that exists and is not in the future) and nothing else: a value is never
"corrected" for her. The edited field closes at level B (a person and the engine's candidates
agree on it, as the UI's mock does), its reading trail gains a `human` step, and the row level is
recomputed from the four critical fields. Clearing a field returns it to level D `not_found`:
an empty, flagged field is always better than a guessed one (project rule §2).

Pure functions; the clock and the sender list arrive through ports.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from backend.core.models import (
    REASON_TEXT,
    CommitmentRow,
    ConfidenceLevel,
    FieldKey,
    FieldResult,
    ReaderName,
    ReadingTrailStep,
    ValidationOutcome,
)
from backend.core.normalize import (
    format_date,
    normalize_central,
    normalize_commitment,
    normalize_name,
)
from backend.core.pipeline.s10_rows import worst_critical_level
from backend.core.ports.clock import ClockPort
from backend.core.ports.senders import SenderIndexPort
from backend.core.scoring import SENDER_NOT_LISTED
from backend.core.validators import date_not_future, israeli_id_ok, pad_id, parse_il_date

EditAction = Literal["edit", "pick_candidate", "clear"]

# Structural: a human decision is a score of 1 (certain); a cleared field has none.
_HUMAN_SCORE: Final = 1.0
# Structural: separator that joins several central numbers in one field value (PLAN §9.7).
CENTRAL_SEPARATOR: Final = "; "


class EditInvalid(ValueError):
    """The submitted value is not acceptable for the field. The message names the rule, never
    the value, so it is safe to return to the client and to log."""

    code = "EDIT_INVALID"


def _normalize(
    key: FieldKey, raw: str, *, clock: ClockPort, senders: SenderIndexPort | None
) -> tuple[str, list[ValidationOutcome]]:
    """(canonical value, validation outcomes) for `raw`. Raises EditInvalid."""
    outcomes: list[ValidationOutcome] = []
    if key is FieldKey.national_id:
        padded = pad_id(raw)
        if padded is None or not israeli_id_ok(padded):
            raise EditInvalid("the ID number fails the check digit")
        outcomes.append(ValidationOutcome(name="israeli_id_check", kind="hard", passed=True))
        return padded, outcomes
    if key is FieldKey.receipt_date:
        parsed = parse_il_date(raw)
        if parsed is None:
            raise EditInvalid("the date does not exist")
        outcomes.append(ValidationOutcome(name="calendar_valid", kind="hard", passed=True))
        if not date_not_future(parsed, now=clock.now().date()):
            raise EditInvalid("the date is in the future")
        outcomes.append(ValidationOutcome(name="not_future", kind="hard", passed=True))
        return format_date(parsed), outcomes
    if key is FieldKey.central_number:
        parts = [normalize_central(p) for p in raw.split(";")]
        values = [p for p in parts if p]
        if not values:
            raise EditInvalid("the central number is empty")
        return CENTRAL_SEPARATOR.join(dict.fromkeys(values)), outcomes
    if key is FieldKey.commitment_number:
        commitment = normalize_commitment(raw)
        if commitment is None:
            raise EditInvalid("the commitment number is empty")
        return commitment, outcomes
    if key is FieldKey.sender:
        text = " ".join(raw.split())
        listed = senders.get(text) if senders is not None else None
        if text != SENDER_NOT_LISTED and listed is None:
            raise EditInvalid("the sender is not in the closed list")
        return text, outcomes
    name = normalize_name(raw)
    if name is None:
        raise EditInvalid("the name is empty")
    return name, outcomes


def resolve_edit(
    row: CommitmentRow,
    key: FieldKey,
    *,
    value: str | None,
    pick_index: int | None,
    clock: ClockPort,
    senders: SenderIndexPort | None,
) -> tuple[FieldResult, EditAction]:
    """The field as it stands after the edit, and which action it was.

    Exactly one of a `value` (None or blank clears) and a `pick_index` into the field's
    candidates. Raises KeyError when the row has no such field and EditInvalid for an
    unacceptable value or an out-of-range pick."""
    current = row.fields[key]
    action: EditAction
    if pick_index is not None:
        if pick_index >= len(current.candidates):
            raise EditInvalid("there is no such candidate")
        raw: str | None = current.candidates[pick_index].value
        action = "pick_candidate"
    else:
        raw = value
        action = "edit"
    if raw is None or not raw.strip():
        return _cleared(current), "clear"
    canonical, validations = _normalize(key, raw, clock=clock, senders=senders)
    step = ReadingTrailStep(
        stage="review",
        reader=ReaderName.human,
        outcome="value",
        value=None,  # the value is on the field itself; the trail records that a person decided
        reader_confidence=None,
        duration_ms=0,
        cloud=False,
    )
    located: dict[str, Any] = {}
    if pick_index is not None:  # a picked candidate brings its own location and source
        picked = current.candidates[pick_index]
        located = {"box": picked.box, "source": picked.source}
    edited = current.model_copy(
        update={
            **located,
            "value": canonical,
            "display_value": canonical,
            "level": ConfidenceLevel.B,
            "score": _HUMAN_SCORE,
            "reason": None,
            "validations": validations or list(current.validations),
            "trail": [*current.trail, step],
        }
    )
    return edited, action


def _cleared(current: FieldResult) -> FieldResult:
    step = ReadingTrailStep(
        stage="review",
        reader=ReaderName.human,
        outcome="no_value",
        value=None,
        reader_confidence=None,
        duration_ms=0,
        cloud=False,
    )
    return current.model_copy(
        update={
            "value": None,
            "display_value": None,
            "level": ConfidenceLevel.D,
            "score": 0.0,
            "reason": REASON_TEXT["not_found"],
            "trail": [*current.trail, step],
        }
    )


def apply_edit(row: CommitmentRow, key: FieldKey, edited: FieldResult) -> CommitmentRow:
    """`row` with `key` replaced, the row level recomputed and the row marked `edited`. Editing
    the central number replaces the group's central numbers with the edited ones."""
    fields = {**row.fields, key: edited}
    centrals = row.central_numbers
    if key is FieldKey.central_number:
        centrals = [v.strip() for v in (edited.value or "").split(";") if v.strip()]
    return row.model_copy(
        update={
            "fields": fields,
            "central_numbers": centrals,
            "row_level": worst_critical_level(fields),
            "status": "edited",
        }
    )
