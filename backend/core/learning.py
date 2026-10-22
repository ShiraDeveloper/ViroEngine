"""The learning layer's pure logic: what may be remembered, and how it is fed back (PLAN §7, T24).

Only **where** a field was found and **what shape** its value had are ever remembered, never the
value. `LayoutObservation` has no value field, `assert_pii_free` refuses one that grew a forbidden
key, and `abstract_shape` throws the digits away before anything reaches storage. Learning comes
only from fields a person can stand behind (levels A-C, or edited by a human); an unresolved field
teaches nothing. Hints compete with the engine's own detections and only ever adjust a score.
"""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Final

from pydantic import BaseModel

from backend.core.models import (
    CommitmentRow,
    ConfidenceLevel,
    FieldKey,
    FieldResult,
    LayoutObservation,
    ReaderName,
    RegionKind,
    SenderSpec,
)
from backend.core.pipeline.outcome import PageOutcome
from backend.core.ports.learning import LearningStorePort
from backend.core.scoring import SENDER_NOT_LISTED
from backend.core.validators import shape_of

# Structural: PLAN §7.1. A key like these in anything handed to the store is a bug, not input.
FORBIDDEN_KEYS: Final = frozenset(
    {
        "value",
        "values",
        "text",
        "payload",
        "first_name",
        "last_name",
        "national_id",
        "central_number",
        "commitment_number",
        "receipt_date",
        "image",
        "png",
        "raw_text",
    }
)

# Structural: where a field normally sits on a form, used when the winning reading carries no
# region of its own. Lets a learned location become a region S4 can emit.
_DEFAULT_REGION: Final = {
    FieldKey.first_name: RegionKind.patient_box,
    FieldKey.last_name: RegionKind.patient_box,
    FieldKey.national_id: RegionKind.patient_box,
    FieldKey.central_number: RegionKind.central_sticker,
    FieldKey.receipt_date: RegionKind.receipt_stamp,
    FieldKey.commitment_number: RegionKind.commitment_label,
    FieldKey.sender: RegionKind.letterhead,
}
# Structural: only the commitment number has a per-sender shape worth remembering (PLAN §2.1: the
# central number has no fixed format, and names, IDs and dates carry no sender-specific shape).
_SHAPED: Final = frozenset({FieldKey.commitment_number})

_LEARNABLE: Final = frozenset({ConfidenceLevel.A, ConfidenceLevel.B, ConfidenceLevel.C})


class PiiLeak(ValueError):
    """An observation carried a forbidden key. The store refuses it (a hard failure)."""


def assert_pii_free(obs: BaseModel) -> None:
    """Raise PiiLeak when `obs` has any key in FORBIDDEN_KEYS, by declared field or extra.
    Checked on every write: a leak must stop the write, not be logged after it."""
    keys = set(obs.model_dump(exclude_none=True)) | set(type(obs).model_fields)
    leaked = FORBIDDEN_KEYS & keys
    if leaked:
        raise PiiLeak(f"learning store received forbidden keys: {sorted(leaked)}")


def abstract_shape(value: str) -> str:
    """'X132767' -> 'A######'. The digits are discarded here, which is what lets a pattern be
    persisted without the value."""
    return shape_of(value)


def shape_to_regex(shape: str) -> str:
    """The anchored regex a stored shape stands for: `#` a digit, `A` a letter, anything else
    literal. It is a hint for `pattern_fit`, never a validation."""
    parts = {"#": r"\d", "A": r"[^\W\d_]"}
    return "^" + "".join(parts.get(c, re.escape(c)) for c in shape) + "$"


def _source_page(
    key: FieldKey, result: FieldResult, pages: Sequence[PageOutcome]
) -> PageOutcome | None:
    """The page this field was read from: the one whose own result for `key` is this reading."""
    for page in pages:
        mine = page.results.get(key)
        if mine is None or result.box is None:
            continue
        # The untouched reading, or a candidate a person picked: both have a known place.
        if mine.box == result.box or any(c.box == result.box for c in mine.candidates):
            return page
    return None


def _region_of(key: FieldKey, result: FieldResult) -> RegionKind:
    for cand in result.candidates:
        if cand.region is not None and cand.box == result.box:
            return cand.region
    return _DEFAULT_REGION[key]


def observations_from_row(
    row: CommitmentRow, pages: Sequence[PageOutcome]
) -> list[LayoutObservation]:
    """What a confirmed row teaches: for each field a person can stand behind, where it was found.

    A field is learned from only when its level is A-C or a human set it (level B after an edit),
    it has a location, and the row's sender is a real institution. Unresolved (D) fields, fields
    with no box and the unlisted sender teach nothing. Never carries a value."""
    sender_field = row.fields.get(FieldKey.sender)
    sender = sender_field.value if sender_field is not None else None
    if not sender or sender == SENDER_NOT_LISTED:
        return []
    by_id = {p.page_id: p for p in pages if p.page_id in set(row.page_ids)}
    observed: list[LayoutObservation] = []
    for key, result in row.fields.items():
        if result.level not in _LEARNABLE or result.value is None or result.box is None:
            continue
        page = _source_page(key, result, list(by_id.values()))
        if page is None or page.page_role is None:
            continue
        shape = abstract_shape(result.value) if key in _SHAPED else None
        label = next(
            (
                n[len("label:") :]
                for c in result.candidates
                for n in c.notes
                if n.startswith("label:")
            ),
            None,
        )
        observed.append(
            LayoutObservation(
                sender=sender,
                field=key,
                region_kind=_region_of(key, result),
                box=result.box,
                source=result.source,
                page_role=page.page_role,
                label_text=label,
                shape=shape,
            )
        )
    return observed


def field_outcomes(row: CommitmentRow) -> list[tuple[str, FieldKey, bool]]:
    """(sender, field, corrected) for every field of a confirmed row that had a reading or was
    set by a person: what the review says about how well the engine read it. No value."""
    sender_field = row.fields.get(FieldKey.sender)
    sender = sender_field.value if sender_field is not None else None
    if not sender or sender == SENDER_NOT_LISTED:
        return []
    out: list[tuple[str, FieldKey, bool]] = []
    for key, result in row.fields.items():
        corrected = any(step.reader is ReaderName.human for step in result.trail)
        if result.value is None and not corrected:
            continue  # nothing was read and nobody filled it: no evidence either way
        out.append((sender, key, corrected))
    return out


def with_learned(spec: SenderSpec | None, learning: LearningStorePort) -> SenderSpec | None:
    """`spec` widened with what was learned for its sender: approved label synonyms join the
    commitment labels and promoted shapes join the pattern hints. The result only ever adds search
    labels and score hints; it never replaces anything the closed list says. None stays None."""
    if spec is None:
        return None
    field = FieldKey.commitment_number.value
    labels = [
        s for s in learning.label_synonyms(spec.canonical, field) if s not in spec.commitment_labels
    ]
    patterns = [
        h.pattern
        for h in learning.pattern_hints(spec.canonical, field)
        if h.pattern not in spec.pattern_hints
    ]
    if not labels and not patterns:
        return spec
    return spec.model_copy(
        update={
            "commitment_labels": [*spec.commitment_labels, *labels],
            "pattern_hints": [*spec.pattern_hints, *patterns],
        }
    )
