"""The extraction schema a vision model must answer in (PLAN Appendix B), enforced twice.

The provider is asked to follow `EXTRACTION_SCHEMA`; whatever arrives is validated again here
with strict Pydantic models. The provider's enforcement is a convenience, this is the guarantee:
an unknown key (a `confidence`, an instruction the model decided to obey) makes the answer
invalid, and an invalid answer never becomes a value. Pure; standard library plus pydantic.
"""

# ruff: noqa: RUF001  (Hebrew label words are data, not confusable text)

from __future__ import annotations

from collections.abc import Sequence
import json
import re
from typing import Any, Final

from pydantic import Field, ValidationError

from backend.core.errors import ProviderBadResponse
from backend.core.models import (
    VISION_TEXT_MAX,
    Box,
    FieldKey,
    PageAnswer,
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


# ---------------------------------------------------------------------------------------------
# Vision-first engine (ADR-017): one whole-page read, then a blind re-read of zoomed crops.
# The schemas are written out inline (no $ref, no null types) because that is the subset every
# Gemini model accepts in `responseJsonSchema`. An absent value is the empty string.
# ---------------------------------------------------------------------------------------------

PAGE_ROLES: Final = ("primary_form", "sap_screen", "lab_report", "continuation", "blank")
SENDER_NOT_IN_LIST: Final = "לא ברשימה"
# Gemini's native box convention: [y_min, x_min, y_max, x_max] on a 0-1000 grid.
BOX_SCALE: Final = 1000.0

_PAGE_BOX: Final = {"type": "array", "items": {"type": "number"}}
_PAGE_ITEM: Final = {
    "type": "object",
    "properties": {
        "value": {"type": "string"},
        "label": {"type": "string"},
        "source": {"type": "string", "enum": _SOURCES},
        "box": _PAGE_BOX,
    },
    "required": ["value", "label", "source", "box"],
}
_PAGE_MULTI: Final = {"type": "array", "items": _PAGE_ITEM}
_SINGLE_FIELDS: Final = tuple(f for f in TEXT_FIELDS if f is not FieldKey.sender)


def page_schema(sender_options: Sequence[str]) -> dict[str, Any]:
    """Response schema of the whole-page read: one object per page sent, numbered from 1. The
    sender is an enumeration of the closed list plus SENDER_NOT_IN_LIST, so the model can pick
    an institution but never invent one."""
    page = {
        "type": "object",
        "properties": {
            "page": {"type": "integer"},
            "page_role": {"type": "string", "enum": list(PAGE_ROLES)},
            "sender": {"type": "string", "enum": [*sender_options, SENDER_NOT_IN_LIST]},
            "sender_box": _PAGE_BOX,
            "patient_address": {"type": "string"},
            **{f.value: _PAGE_ITEM for f in _SINGLE_FIELDS},
            **{f.value: _PAGE_MULTI for f in MULTI_FIELDS},
            "ambiguities": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "page",
            "page_role",
            "sender",
            "sender_box",
            "patient_address",
            *[f.value for f in _SINGLE_FIELDS],
            *[f.value for f in MULTI_FIELDS],
            "ambiguities",
        ],
    }
    return {
        "type": "object",
        "properties": {"pages": {"type": "array", "items": page}},
        "required": ["pages"],
    }


VERIFY_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "crops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "crop": {"type": "integer"},
                    "values": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["crop", "values"],
            },
        }
    },
    "required": ["crops"],
}


class _PageItem(Strict):
    value: str = Field(max_length=VISION_TEXT_MAX)
    label: str = Field(default="", max_length=VISION_TEXT_MAX)
    source: SourceKind = SourceKind.unknown
    box: list[float] = Field(default_factory=list, max_length=8)


class _PageRead(Strict):
    """One page of a whole-page answer. Unknown keys are rejected like every other answer."""

    page: int = Field(ge=1, le=50)
    page_role: str
    sender: str = Field(max_length=VISION_TEXT_MAX)
    sender_box: list[float] = Field(default_factory=list, max_length=8)
    patient_address: str = Field(default="", max_length=VISION_TEXT_MAX)
    first_name: _PageItem
    last_name: _PageItem
    national_id: _PageItem
    receipt_date: _PageItem
    central_number: list[_PageItem] = Field(max_length=MAX_VALUES)
    commitment_number: list[_PageItem] = Field(max_length=MAX_VALUES)
    ambiguities: list[str] = Field(default_factory=list, max_length=MAX_AMBIGUITIES)


class PagePayload(Strict):
    """A validated whole-page answer: one reading per page sent."""

    pages: list[_PageRead] = Field(max_length=50)


class _VerifyCrop(Strict):
    crop: int = Field(ge=1, le=50)
    values: list[str] = Field(max_length=MAX_VALUES)


class VerifyPayload(Strict):
    """A validated blind re-read: the values seen in each numbered crop."""

    crops: list[_VerifyCrop] = Field(max_length=50)


def to_box(raw: Sequence[float]) -> Box | None:
    """A model box as a normalized Box. Accepts the native 0-1000 grid or 0-1 values; None for
    anything that is not four numbers describing a non-empty area."""
    if len(raw) != 4:
        return None
    scale = BOX_SCALE if max(raw) > 1.0 else 1.0
    y0, x0, y1, x1 = (min(1.0, max(0.0, v / scale)) for v in raw)
    y0, y1 = min(y0, y1), max(y0, y1)
    x0, x1 = min(x0, x1), max(x0, x1)
    if y1 - y0 <= 0.0 or x1 - x0 <= 0.0:
        return None
    return (y0, x0, y1, x1)


def parse_page(text: str) -> PagePayload:
    """Validate a whole-page answer. Raises ProviderBadResponse on anything off-schema."""
    data = _load_json(text)
    try:
        payload = PagePayload.model_validate(data)
    except ValidationError as exc:
        raise ProviderBadResponse("response does not match the page schema") from exc
    if any(p.page_role not in PAGE_ROLES for p in payload.pages):
        raise ProviderBadResponse("page role outside the enumeration")
    return payload


def parse_verify(text: str) -> VerifyPayload:
    """Validate a blind re-read. Raises ProviderBadResponse on anything off-schema."""
    data = _load_json(text)
    try:
        return VerifyPayload.model_validate(data)
    except ValidationError as exc:
        raise ProviderBadResponse("response does not match the verify schema") from exc


# Label words that show a value belongs to ANOTHER field or person, per field (ADR-017). The
# model reports the label it read the value beside; a value whose own label says "address",
# "father", "case number"... is dropped. Evidence from the page itself, so it holds on any form.
_FOREIGN_LABEL_WORDS: Final[dict[FieldKey, frozenset[str]]] = {
    FieldKey.first_name: frozenset(
        {"כתובת", "רחוב", "עיר", "ישוב", "יישוב", "אב", "האב", "אם", "האם", "רופא", "הרופא",
         "מזמין", "המזמין", "מנהל", "חתימה", "מטפל", "המטפל"}
    ),
    FieldKey.national_id: frozenset(
        {"אב", "האב", "אם", "האם", "רופא", "הרופא", "רישיון", "מקרה", "ביקור", "טלפון",
         "פקס", "קליטה", "תיק", "מבחנה"}
    ),
    FieldKey.commitment_number: frozenset(
        {"מקרה", "ביקור", "קליטה", "מבחנה", "טלפון", "פקס", "תיק", "זהות", "ת.ז", "מרכזי",
         "קטלוגי", "מק\"ט"}
    ),
}
_FOREIGN_LABEL_WORDS[FieldKey.last_name] = _FOREIGN_LABEL_WORDS[FieldKey.first_name]


def label_belongs(key: FieldKey, label: str) -> bool:
    """False when the label a value was read beside names another field or person. An empty
    label (a sticker, a stamp) is no evidence either way and passes."""
    words = {w.strip(".:\"'׳״()") for w in label.replace("/", " ").split()}
    return not (words & _FOREIGN_LABEL_WORDS.get(key, frozenset()))


# Words that never form part of a person's name on these forms: tests, specimens, clinical
# terms, form words. A "name" containing one was read from the clinical or form text.
_NOT_NAME_WORDS: Final = frozenset(
    {"בדיקה", "בדיקת", "בדיקות", "דגימה", "דם", "סרום", "שתן", "נוזל", "וירוס", "נגיף", "נגיפים",
     "נוגדנים", "PCR", "IgG", "IgM", "HIV", "CMV", "EBV", "HBV", "HCV", "HSV", "אבחנה", "מעבדה",
     "מחלקה", "רופא", "ד\"ר", "פרופ'", "בית", "חולים", "מרכז", "רפואי", "קופת", "שם", "משפחה",
     "פרטי", "כתובת", "טלפון", "תאריך", "חתימה", "מטופל", "מבוטח"}
)


def name_plausible(value: str) -> bool:
    """False for a "name" with a digit, a clinical or form word, or more than four words: it was
    read from the wrong text. A real name is never "corrected" - it is kept or dropped."""
    words = value.replace("-", " ").split()
    if not words or len(words) > 4 or any(ch.isdigit() for ch in value):
        return False
    return not any(w.strip(".,:;()") in _NOT_NAME_WORDS for w in words)


def _single(item: _PageItem, key: FieldKey) -> VisionAnswer:
    value = item.value.strip()
    if not label_belongs(key, item.label):
        value = ""
    if value and key in (FieldKey.first_name, FieldKey.last_name) and not name_plausible(value):
        value = ""
    return VisionAnswer(
        values=[value] if value else [],
        source=item.source,
        box=to_box(item.box),
        note=item.label.strip()[:MAX_NOTE] or None,
    )


def _union(boxes: Sequence[Box]) -> Box | None:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _page_fields(read: _PageRead) -> dict[FieldKey, VisionAnswer]:
    """One answer per field. A multi-valued field keeps its values in page order and the union
    of their boxes. The sender stays verbatim: closed-list matching belongs to the core."""
    answers: dict[FieldKey, VisionAnswer] = {
        key: _single(getattr(read, key.value), key) for key in _SINGLE_FIELDS
    }
    for key in MULTI_FIELDS:
        items = [i for i in getattr(read, key.value) if label_belongs(key, i.label)]
        values = list(dict.fromkeys(v for i in items if (v := i.value.strip())))
        boxes = [b for i in items if (b := to_box(i.box)) is not None]
        answers[key] = VisionAnswer(
            values=values[:MAX_VALUES],
            source=items[0].source if items else SourceKind.unknown,
            box=_union(boxes),
            note=(items[0].label.strip()[:MAX_NOTE] or None) if items else None,
        )
    sender = read.sender.strip()
    answers[FieldKey.sender] = VisionAnswer(
        values=[sender] if sender and sender != SENDER_NOT_IN_LIST else [],
        source=SourceKind.printed,
        box=to_box(read.sender_box),
    )
    return answers


def page_answers(payload: PagePayload, pages_sent: int) -> list[PageAnswer]:
    """The answer of every page that was sent, by its 1-based number. A page the model numbered
    outside the request, or answered twice, is dropped (the first answer wins)."""
    seen: dict[int, PageAnswer] = {}
    for read in payload.pages:
        if 1 <= read.page <= pages_sent and read.page not in seen:
            seen[read.page] = PageAnswer(
                page=read.page,
                page_role=read.page_role,
                address=read.patient_address.strip(),
                answers=_page_fields(read),
                ambiguities=[a[:MAX_NOTE] for a in read.ambiguities[:MAX_AMBIGUITIES]],
            )
    return [seen[n] for n in sorted(seen)]


def crop_values(payload: VerifyPayload, crops_sent: int) -> list[list[str]]:
    """The values seen in each crop that was sent, in request order. A crop the model did not
    mention is an empty list (nothing seen is not a value)."""
    out: list[list[str]] = [[] for _ in range(crops_sent)]
    for item in payload.crops:
        if 1 <= item.crop <= crops_sent:
            seen = [v.strip() for v in item.values if v.strip()]
            kept = [v for v in seen if len(v) <= VISION_TEXT_MAX]
            out[item.crop - 1] = list(dict.fromkeys([*out[item.crop - 1], *kept]))[:MAX_VALUES]
    return out
