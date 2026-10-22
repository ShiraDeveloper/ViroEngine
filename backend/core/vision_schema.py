"""The extraction schema a vision model must answer in (PLAN Appendix B), enforced twice.

The provider is asked to follow `EXTRACTION_SCHEMA`; whatever arrives is validated again here
with strict Pydantic models. The provider's enforcement is a convenience, this is the guarantee:
an unknown key (a `confidence`, an instruction the model decided to obey) makes the answer
invalid, and an invalid answer never becomes a value. Pure; standard library plus pydantic.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from pydantic import Field, ValidationError

from backend.core.errors import ProviderBadResponse
from backend.core.models import (
    VISION_TEXT_MAX,
    Box,
    FieldKey,
    SourceKind,
    Strict,
    VisionAnswer,
)

# Structural: the response schema's bounds (PLAN Appendix B). They only cap a pathological answer.
MAX_VALUES: Final = 10
MAX_ALTS: Final = 5
MAX_NOTE: Final = 200
MAX_AMBIGUITIES: Final = 10

_SOURCES: Final = [s.value for s in SourceKind]
_BOX_SCHEMA: Final = {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}
_ALTS_SCHEMA: Final = {
    "type": "array",
    "items": {"type": "string", "maxLength": VISION_TEXT_MAX},
    "maxItems": MAX_ALTS,
}
_NOTE_SCHEMA: Final = {"type": ["string", "null"], "maxLength": MAX_NOTE}

# Fields the model answers as one value, and as a list (multiplicity is normal for these two).
TEXT_FIELDS: Final = (
    FieldKey.first_name,
    FieldKey.last_name,
    FieldKey.national_id,
    FieldKey.receipt_date,
    FieldKey.sender,
)
MULTI_FIELDS: Final = (FieldKey.central_number, FieldKey.commitment_number)

# Appendix B verbatim in structure. There is deliberately no confidence field anywhere.
EXTRACTION_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields", "ambiguities"],
    "properties": {
        "fields": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                **{f.value: {"$ref": "#/$defs/textField"} for f in TEXT_FIELDS},
                **{f.value: {"$ref": "#/$defs/multiField"} for f in MULTI_FIELDS},
            },
        },
        "ambiguities": {
            "type": "array",
            "items": {"type": "string", "maxLength": MAX_NOTE},
            "maxItems": MAX_AMBIGUITIES,
        },
    },
    "$defs": {
        "textField": {
            "type": "object",
            "additionalProperties": False,
            "required": ["value", "source", "alts"],
            "properties": {
                "value": {"type": ["string", "null"], "maxLength": VISION_TEXT_MAX},
                "source": {"enum": _SOURCES},
                "box": _BOX_SCHEMA,
                "alts": _ALTS_SCHEMA,
                "note": _NOTE_SCHEMA,
            },
        },
        "multiField": {
            "type": "object",
            "additionalProperties": False,
            "required": ["values", "source", "alts"],
            "properties": {
                "values": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": VISION_TEXT_MAX},
                    "maxItems": MAX_VALUES,
                },
                "source": {"enum": _SOURCES},
                "box": _BOX_SCHEMA,
                "alts": _ALTS_SCHEMA,
                "note": _NOTE_SCHEMA,
            },
        },
    },
}

# The secondary prompt (Appendix A, sender_v1) answers with exactly this.
SENDER_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sender"],
    "properties": {"sender": {"type": "string", "maxLength": VISION_TEXT_MAX}},
}


class _TextField(Strict):
    value: str | None = Field(max_length=VISION_TEXT_MAX)
    source: SourceKind
    box: Box | None = None
    alts: list[str] = Field(max_length=MAX_ALTS)
    note: str | None = Field(default=None, max_length=MAX_NOTE)


class _MultiField(Strict):
    values: list[str] = Field(max_length=MAX_VALUES)
    source: SourceKind
    box: Box | None = None
    alts: list[str] = Field(max_length=MAX_ALTS)
    note: str | None = Field(default=None, max_length=MAX_NOTE)


class _Fields(Strict):
    first_name: _TextField | None = None
    last_name: _TextField | None = None
    national_id: _TextField | None = None
    receipt_date: _TextField | None = None
    sender: _TextField | None = None
    central_number: _MultiField | None = None
    commitment_number: _MultiField | None = None


class ExtractionPayload(Strict):
    """A model answer after validation. Unknown keys (e.g. `confidence`) are rejected."""

    fields: _Fields
    ambiguities: list[str] = Field(max_length=MAX_AMBIGUITIES)


class SenderPayload(Strict):
    """The sender-question answer: one string, checked against the closed list by S5."""

    sender: str = Field(max_length=VISION_TEXT_MAX)


_FENCE = re.compile(r"^```[A-Za-z]*\s*\n?(.*?)\n?```$", re.DOTALL)


def _load_json(text: str) -> object:
    """Parse the model's text as JSON. A single enclosing code fence is tolerated; prose around
    it is not. Raises ProviderBadResponse."""
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced is not None:
        body = fenced.group(1).strip()
    try:
        return json.loads(body)
    except ValueError as exc:  # json.JSONDecodeError subclasses ValueError
        raise ProviderBadResponse("response is not JSON") from exc


def parse_extraction(text: str) -> ExtractionPayload:
    """Validate a model answer against the extraction schema.

    Raises ProviderBadResponse (never returns a partial result) when the text is not JSON or does
    not match the schema, including any extra key."""
    data = _load_json(text)
    try:
        return ExtractionPayload.model_validate(data)
    except ValidationError as exc:
        raise ProviderBadResponse("response does not match the extraction schema") from exc


def parse_sender(text: str) -> SenderPayload:
    """Validate a sender answer. Raises ProviderBadResponse on anything but `{"sender": str}`."""
    data = _load_json(text)
    try:
        return SenderPayload.model_validate(data)
    except ValidationError as exc:
        raise ProviderBadResponse("response does not match the sender schema") from exc


def to_answers(payload: ExtractionPayload) -> dict[FieldKey, VisionAnswer]:
    """One VisionAnswer per field the model returned. A null or blank value yields an answer
    with no values (the model saw nothing), never an empty-string value."""
    answers: dict[FieldKey, VisionAnswer] = {}
    for key in FieldKey:
        item: _TextField | _MultiField | None = getattr(payload.fields, key.value)
        if item is None:
            continue
        raw = [item.value] if isinstance(item, _TextField) else item.values
        values = [v.strip() for v in raw if v is not None and v.strip()]
        answers[key] = VisionAnswer(
            values=values,
            source=item.source,
            box=item.box,
            alts=[a.strip() for a in item.alts if a.strip()],
            note=item.note,
        )
    return answers
