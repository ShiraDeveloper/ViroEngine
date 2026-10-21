"""S6 candidates for national_id, first_name, last_name and commitment_number (PLAN §4 S6, T12).

A token becomes a candidate from the label that governs it and the region it sits in. Length and
shape never create or reject a candidate (project rule §3); the only digit-count limits are the
documented ID acceptance window (7-10 digits, resolved later in normalization) and the digit
test that ends a multi-token value. Deny labels are resolved BEFORE accept labels, so a father's
ID or a visit number can never be reached through a nearer accept label.

Pure functions: an OcrResult, barcodes and regions in, a list of `Candidate` out. Empty is a valid
answer (level D, reason `not_found` is assigned later); a candidate is never invented.
"""

# ruff: noqa: RUF001  (Hebrew letters are not confusable text here)

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import hypot
from typing import Literal

from pydantic import Field

from backend.core.models import (
    BarcodeHit,
    Box,
    Candidate,
    FieldKey,
    OcrResult,
    OcrWord,
    ReaderName,
    Region,
    RegionKind,
    SenderSpec,
    SourceKind,
    Strict,
)
from backend.core.pipeline._tokens import (
    UNIT_DIAGONAL,
    Token,
    center,
    digits_of,
    gap,
    has_digit,
    inside,
    inside_any,
    mean_confidence,
    overlaps,
    phrase,
    tokenize,
    union,
)

LabelKind = Literal["allow", "deny", "stop"]

# --- Lexicon (labels as printed on the forms; VERIFY against the samples in T5/T7) ---------------

ID_LABELS = ("תעודת זהות", "ת.ז", "תז", "מספר זהות", "מס' זהות")
# Another person's ID, or the physician's licence. Checked BEFORE ID_LABELS.
ID_DENY_LABELS = (
    "ת.ז האב",
    "ת.ז האם",
    "ת.ז רופא",
    "ת.ז הרופא",
    "תז האב",
    "תז האם",
    "תז רופא",
    "תז הרופא",
    "תעודת זהות האב",
    "תעודת זהות האם",
    "תעודת זהות רופא",
    "תעודת זהות הרופא",
    "מספר רישיון",
    "מס' רישיון",
    "רישיון",
)

NAME_FIRST_LABELS = ("שם פרטי",)
NAME_LAST_LABELS = ("שם משפחה",)
# A generic label says nothing about the order of the parts, so it yields ONE combined candidate.
NAME_FULL_LABELS = ("שם", "שם מלא", "שם פרטי ומשפחה", "שם פרטי ושם משפחה")
NAME_DENY_LABELS = ("שם האב", "שם האם", "שם הרופא", "שם רופא", "שם המזמין", "שם הבעל")

# Used when the sender is unknown; a known sender brings its own synonyms.
GENERIC_COMMITMENT_LABELS = ("מספר התחייבות", "התחייבות מספר", "התחייבות")
GLOBAL_COMMITMENT_DENY = (
    "מקרה",
    "מס' מקרה",
    "מספר מקרה",
    "מס' ביקור",
    "מספר ביקור",
    "מס' קליטה",
    "מספר קליטה",
    "מספר מבחנה",
    "מס' מבחנה",
    "פקס",
    "טלפון",
    "טל'",
)

# Labels of other fields. They are never accepted here; they only end a value run.
OTHER_LABELS = ("תאריך", "תאריך לידה", "ת.לידה", "מין", "כתובת", "נייד", "גיל", "קופת חולים")

NOTE_COMBINED_NAME = "combined_name_label"
NOTE_DIGITS_JOINED = "digits_joined"
NOTE_SAP_ROW = "sap_row"
NOTE_BARCODE_HINT = "barcode_hint"
NOTE_BELOW_LABEL = "value_below_label"


class IdentityConfig(Strict):
    """S6 identity thresholds. `config/models.yaml: identity` is authoritative; a test keeps
    these defaults equal to it. All distances are normalised page fractions."""

    id_label_radius: float = Field(default=0.12, gt=0.0)  # of page width, label to first digit
    id_min_digits: int = Field(default=7, ge=1)  # a lost leading zero is padded in normalization
    id_max_digits: int = Field(default=10, ge=1)  # a surplus leading zero is stripped there
    id_max_tokens: int = Field(default=4, ge=1)  # handwritten digits may be split into groups
    name_radius: float = Field(default=0.25, gt=0.0)  # ASSUMPTION: a name runs to the line end
    name_max_tokens: int = Field(default=4, ge=1)
    commitment_radius: float = Field(default=0.18, gt=0.0)  # of page width, in reading direction
    commitment_max_tokens: int = Field(default=3, ge=1)  # '2648 00879' is two tokens
    chain_gap: float = Field(default=0.05, gt=0.0)  # largest gap between tokens of one value
    line_tolerance: float = Field(default=0.015, gt=0.0)  # y distance that still counts as a line
    below_gap: float = Field(default=0.05, gt=0.0)  # label-to-value vertical gap, value below
    column_margin: float = Field(default=0.03, ge=0.0)  # SAP column slack around the header
    barcode_confidence: float = Field(default=0.95, ge=0.0, le=1.0)  # a decoded symbol, as reported


@dataclass(frozen=True)
class _Label:
    kind: LabelKind
    tag: str
    start: int  # index of the first token
    end: int  # index after the last token
    box: Box


_KIND_ORDER = {"deny": 0, "allow": 1, "stop": 2}


# --- label and value search --------------------------------------------------------------------


def _find_labels(
    toks: Sequence[Token],
    entries: Sequence[tuple[LabelKind, str, tuple[str, ...]]],
    cfg: IdentityConfig,
) -> list[_Label]:
    """Non-overlapping label occurrences, longest phrase first and deny before accept on a tie,
    so 'ת.ז האב' is one deny label and never an 'ת.ז' accept label. Never raises."""
    found: list[_Label] = []
    for kind, tag, words in entries:
        size = len(words)
        if not size:
            continue
        for i in range(len(toks) - size + 1):
            window = toks[i : i + size]
            if tuple(t.plain for t in window) != words:
                continue
            ys = [center(t.box)[0] for t in window]
            if max(ys) - min(ys) > cfg.line_tolerance:
                continue  # the words of one label sit on one line
            found.append(_Label(kind, tag, i, i + size, union([t.box for t in window])))
    found.sort(key=lambda lab: (-(lab.end - lab.start), _KIND_ORDER[lab.kind], lab.start))
    chosen: list[_Label] = []
    used: set[int] = set()
    for lab in found:
        span = set(range(lab.start, lab.end))
        if span & used:
            continue
        used |= span
        chosen.append(lab)
    return sorted(chosen, key=lambda lab: lab.start)


def _entries(
    allow: Sequence[tuple[str, Sequence[str]]],
    deny: Sequence[str],
    stops: Sequence[str],
) -> list[tuple[LabelKind, str, tuple[str, ...]]]:
    out: list[tuple[LabelKind, str, tuple[str, ...]]] = []
    for tag, labels in allow:
        out.extend(("allow", tag, phrase(text)) for text in labels)
    out.extend(("deny", "deny", phrase(text)) for text in deny)
    out.extend(("stop", "stop", phrase(text)) for text in stops)
    return out


def _all_labels() -> list[str]:
    """Every identity-lexicon label, for ending a value run at any neighbouring label."""
    return [
        *ID_LABELS,
        *ID_DENY_LABELS,
        *NAME_FIRST_LABELS,
        *NAME_LAST_LABELS,
        *NAME_FULL_LABELS,
        *NAME_DENY_LABELS,
        *GENERIC_COMMITMENT_LABELS,
        *GLOBAL_COMMITMENT_DENY,
        *OTHER_LABELS,
    ]


def _chain(
    order: Sequence[int],
    toks: Sequence[Token],
    taken: set[int],
    *,
    start_edge: float,
    radius: float,
    max_tokens: int,
    digits_continue: bool,
    cfg: IdentityConfig,
) -> list[int]:
    """Walk `order` (nearest first, reading direction) and collect one value. Ends at a label
    token, at a gap over `radius` (first token) / `chain_gap` (later tokens), or - when
    `digits_continue` - at the first later token without a digit. Never raises."""
    picked: list[int] = []
    edge = start_edge
    for idx in order:
        if idx in taken:
            break
        tok = toks[idx]
        if (edge - tok.box[3]) > (radius if not picked else cfg.chain_gap):
            break
        if picked and digits_continue and not has_digit(tok.text):
            break
        picked.append(idx)
        edge = tok.box[1]
        if len(picked) >= max_tokens:
            break
    return picked


def _value_indices(
    label: _Label,
    toks: Sequence[Token],
    taken: set[int],
    cfg: IdentityConfig,
    *,
    radius: float,
    max_tokens: int,
    digits_continue: bool,
) -> tuple[list[int], bool]:
    """Indices of the value governed by `label` and whether it was found below the label.

    Reading direction is right to left: the value is to the LEFT of the label on the same line;
    only when that line holds none, the nearest line directly below is used. Never raises."""
    label_y = center(label.box)[0]
    own = range(label.start, label.end)
    line = [
        i
        for i, t in enumerate(toks)
        if i not in own
        and abs(center(t.box)[0] - label_y) <= cfg.line_tolerance
        and center(t.box)[1] < label.box[1]
    ]
    line.sort(key=lambda i: (-center(toks[i].box)[1], i))
    picked = _chain(
        line,
        toks,
        taken,
        start_edge=label.box[1],
        radius=radius,
        max_tokens=max_tokens,
        digits_continue=digits_continue,
        cfg=cfg,
    )
    if picked:
        return picked, False
    below = [
        i
        for i, t in enumerate(toks)
        if i not in own
        and center(t.box)[0] - label_y > cfg.line_tolerance
        and t.box[0] - label.box[2] <= cfg.below_gap
        and label.box[1] - radius <= center(t.box)[1] <= label.box[3] + radius
    ]
    if not below:
        return [], False
    nearest = min(center(toks[i].box)[0] for i in below)
    row = [i for i in below if center(toks[i].box)[0] - nearest <= cfg.line_tolerance]
    row.sort(key=lambda i: (-center(toks[i].box)[1], i))
    picked = _chain(
        row,
        toks,
        taken,
        start_edge=label.box[3],
        radius=radius,
        max_tokens=max_tokens,
        digits_continue=digits_continue,
        cfg=cfg,
    )
    return picked, bool(picked)


def _denied_indices(
    labels: Sequence[_Label],
    toks: Sequence[Token],
    taken: set[int],
    cfg: IdentityConfig,
    *,
    radius: float,
    max_tokens: int,
) -> set[int]:
    """Tokens governed by a deny label. Computed first; no accept label may claim them."""
    claimed: set[int] = set()
    for lab in labels:
        if lab.kind == "deny":
            idx, _ = _value_indices(
                lab, toks, taken, cfg, radius=radius, max_tokens=max_tokens, digits_continue=False
            )
            claimed.update(idx)
    return claimed


# --- candidate construction --------------------------------------------------------------------


def _source(box: Box, ink_boxes: Sequence[Box]) -> SourceKind:
    """`handwritten` when a blue-ink hint overlaps the box, else `printed`.

    ASSUMPTION: without an ink hint an OCR-read token is treated as printed. The ink mask is a
    hint only (project rule §4); S7 re-reads every handwritten or low-confidence value."""
    return (
        SourceKind.handwritten
        if any(overlaps(box, ink) for ink in ink_boxes)
        else SourceKind.printed
    )


def _region_of(
    box: Box, regions: Sequence[Region], kinds: Sequence[RegionKind]
) -> RegionKind | None:
    for kind in kinds:
        if any(r.kind is kind and inside(box, r.box) for r in regions):
            return kind
    return None


def _label_distance(label_box: Box, value_box: Box) -> float:
    """Distance from the governing label to the value, as a fraction of the page diagonal."""
    dy, dx = gap(label_box, value_box)
    return min(1.0, hypot(dy, dx) / UNIT_DIAGONAL)


def _make(
    value: str,
    toks: Sequence[Token],
    label: _Label,
    ocr: OcrResult,
    regions: Sequence[Region],
    ink_boxes: Sequence[Box],
    kinds: Sequence[RegionKind],
    notes: Sequence[str] = (),
) -> Candidate:
    box = union([t.box for t in toks])
    return Candidate(
        value=value,
        reader=ocr.reader,
        source=_source(box, ink_boxes),
        box=box,
        reader_confidence=mean_confidence(toks),
        label_distance=_label_distance(label.box, box),
        region=_region_of(box, regions, kinds),
        notes=list(notes),
    )


def _barcode_candidate(
    value: str,
    hit: BarcodeHit,
    cfg: IdentityConfig,
    region: RegionKind | None,
    notes: Sequence[str],
) -> Candidate:
    return Candidate(
        value=value,
        reader=ReaderName.zxing,
        source=SourceKind.barcode,
        box=hit.box,
        reader_confidence=cfg.barcode_confidence,
        label_distance=None,
        region=region,
        notes=list(notes),
    )


def _text_has_phrase(texts: Sequence[str], phrases: Sequence[tuple[str, ...]]) -> bool:
    """True when any deny phrase occurs as consecutive tokens in the barcode's nearby text."""
    plains = [t.plain for t in tokenize_text(texts)]
    return any(tuple(plains[i : i + len(p)]) == p for p in phrases if p for i in range(len(plains)))


def tokenize_text(texts: Sequence[str]) -> list[Token]:
    """Tokens of plain strings (no geometry), for barcode neighbourhood text."""
    return tokenize(OcrWord(text=t, box=(0.0, 0.0, 0.0, 0.0), confidence=1.0) for t in texts)


def _dedupe(candidates: Sequence[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, SourceKind]] = set()
    out: list[Candidate] = []
    for cand in candidates:
        key = (cand.value, cand.source)
        if key not in seen:
            seen.add(key)
            out.append(cand)
    return out


def _patient_and_clinical(regions: Sequence[Region]) -> tuple[list[Box], list[Box]]:
    patient = [r.box for r in regions if r.kind is RegionKind.patient_box]
    clinical = [r.box for r in regions if r.kind is RegionKind.clinical]
    return patient, clinical


# --- national_id -------------------------------------------------------------------------------


def candidates_national_id(
    ocr: OcrResult,
    barcodes: Sequence[BarcodeHit],
    regions: Sequence[Region],
    *,
    ink_boxes: Sequence[Box] = (),
    config: IdentityConfig | None = None,
) -> list[Candidate]:
    """ID candidates: digits governed by an ID label inside a `patient_box`, plus barcode
    payloads inside it. Accepts 7-10 digits (config); a lost or surplus leading zero is resolved
    in normalization. Deny labels (father, mother, physician, licence) are resolved first.
    No patient box -> []. Never raises."""
    cfg = config or IdentityConfig()
    patient, clinical = _patient_and_clinical(regions)
    if not patient:
        return []
    toks = [t for t in tokenize(ocr.words) if not inside_any(t.box, clinical)]
    entries = _entries(
        [("id", ID_LABELS)], ID_DENY_LABELS, [x for x in _all_labels() if x not in ID_LABELS]
    )
    labels = _find_labels(toks, entries, cfg)
    taken = {i for lab in labels for i in range(lab.start, lab.end)}
    denied = _denied_indices(
        labels, toks, taken, cfg, radius=cfg.id_label_radius, max_tokens=cfg.id_max_tokens
    )
    out: list[Candidate] = []
    for lab in labels:
        if lab.kind != "allow" or not inside_any(lab.box, patient):
            continue
        idx, below = _value_indices(
            lab,
            toks,
            taken,
            cfg,
            radius=cfg.id_label_radius,
            max_tokens=cfg.id_max_tokens,
            digits_continue=True,
        )
        used = [toks[i] for i in idx if i not in denied and inside_any(toks[i].box, patient)]
        used = sorted((t for t in used if has_digit(t.text)), key=lambda t: center(t.box)[1])
        digits = "".join(digits_of(t.text) for t in used)
        if not used or not cfg.id_min_digits <= len(digits) <= cfg.id_max_digits:
            continue
        notes = ([NOTE_DIGITS_JOINED] if len(used) > 1 else []) + (
            [NOTE_BELOW_LABEL] if below else []
        )
        out.append(
            _make(digits, used, lab, ocr, regions, ink_boxes, [RegionKind.patient_box], notes)
        )
    deny_phrases = [phrase(x) for x in ID_DENY_LABELS]
    for hit in barcodes:
        digits = digits_of(hit.payload)
        if not inside_any(hit.box, patient) or inside_any(hit.box, clinical):
            continue
        if not cfg.id_min_digits <= len(digits) <= cfg.id_max_digits:
            continue
        if _text_has_phrase(hit.nearby_text, deny_phrases):
            continue
        out.append(_barcode_candidate(digits, hit, cfg, RegionKind.patient_box, []))
    return _dedupe(out)


# --- names -------------------------------------------------------------------------------------

_NAME_ALLOW = (
    ("first", NAME_FIRST_LABELS),
    ("last", NAME_LAST_LABELS),
    ("full", NAME_FULL_LABELS),
)


def candidates_names(
    ocr: OcrResult,
    regions: Sequence[Region],
    *,
    ink_boxes: Sequence[Box] = (),
    config: IdentityConfig | None = None,
) -> dict[FieldKey, list[Candidate]]:
    """Name candidates by label, inside a `patient_box`, at most `name_max_tokens` tokens.

    'שם פרטי' -> first_name, 'שם משפחה' -> last_name. A generic 'שם' yields ONE combined
    candidate under first_name with the note `combined_name_label`: guessing the order of the
    parts is forbidden, splitting is the reviewer's decision. Father/physician/requester names
    are denied first. Keys with no candidate are absent. Never raises."""
    cfg = config or IdentityConfig()
    patient, clinical = _patient_and_clinical(regions)
    if not patient:
        return {}
    toks = [t for t in tokenize(ocr.words) if not inside_any(t.box, clinical)]
    own = [*NAME_FIRST_LABELS, *NAME_LAST_LABELS, *NAME_FULL_LABELS]
    entries = _entries(_NAME_ALLOW, NAME_DENY_LABELS, [x for x in _all_labels() if x not in own])
    labels = _find_labels(toks, entries, cfg)
    taken = {i for lab in labels for i in range(lab.start, lab.end)}
    denied = _denied_indices(
        labels, toks, taken, cfg, radius=cfg.name_radius, max_tokens=cfg.name_max_tokens
    )
    result: dict[FieldKey, list[Candidate]] = {}
    for lab in labels:
        if lab.kind != "allow" or not inside_any(lab.box, patient):
            continue
        idx, below = _value_indices(
            lab,
            toks,
            taken,
            cfg,
            radius=cfg.name_radius,
            max_tokens=cfg.name_max_tokens,
            digits_continue=False,
        )
        used = [toks[i] for i in idx if i not in denied and inside_any(toks[i].box, patient)]
        if not used:
            continue
        key = {"first": FieldKey.first_name, "last": FieldKey.last_name}.get(
            lab.tag, FieldKey.first_name
        )
        notes = ([NOTE_COMBINED_NAME] if lab.tag == "full" else []) + (
            [NOTE_BELOW_LABEL] if below else []
        )
        cand = _make(
            " ".join(t.text for t in used),
            used,
            lab,
            ocr,
            regions,
            ink_boxes,
            [RegionKind.patient_box],
            notes,
        )
        result.setdefault(key, []).append(cand)
    return {k: _dedupe(v) for k, v in result.items()}


# --- commitment_number -------------------------------------------------------------------------


def _sap_rows(
    toks: Sequence[Token],
    labels: Sequence[_Label],
    taken: set[int],
    tables: Sequence[Box],
    cfg: IdentityConfig,
) -> tuple[_Label, list[list[Token]]] | None:
    """The commitment column header inside a table and the cells below it, one list per row.
    None when no header is found (the normal label search then runs)."""
    for lab in labels:
        if lab.kind != "allow" or lab.tag != "commitment":
            continue
        table = next((t for t in tables if inside(lab.box, t)), None)
        if table is None:
            continue
        lo, hi = lab.box[1] - cfg.column_margin, lab.box[3] + cfg.column_margin
        cells = [
            i
            for i, t in enumerate(toks)
            if i not in taken
            and inside(t.box, table)
            and lo <= center(t.box)[1] <= hi
            and center(t.box)[0] > center(lab.box)[0] + cfg.line_tolerance
        ]
        cells.sort(key=lambda i: (center(toks[i].box)[0], -center(toks[i].box)[1]))
        rows: list[list[Token]] = []
        row_y = -1.0
        for i in cells:
            y = center(toks[i].box)[0]
            if rows and y - row_y <= cfg.line_tolerance:
                rows[-1].append(toks[i])
            else:
                rows.append([toks[i]])
                row_y = y
        return lab, rows
    return None


def candidates_commitment(
    ocr: OcrResult,
    barcodes: Sequence[BarcodeHit],
    regions: Sequence[Region],
    spec: SenderSpec | None,
    page_role: str,
    *,
    ink_boxes: Sequence[Box] = (),
    config: IdentityConfig | None = None,
) -> list[Candidate]:
    """Commitment candidates: the value after each of the sender's label synonyms, minus anything
    a deny label governs (global list plus `spec.commitment_deny`), plus barcode payloads when the
    spec allows. On a `sap_screen` every row of the commitment column is its own candidate.

    A sender whose spec has no commitment labels (private lab, unknown form) yields [] even when
    the page has barcodes: empty is the correct answer, never a wrong value. Barcodes: used when
    `spec.barcode_is_commitment` is True; hint-level (needs a commitment label nearby) when None;
    never when False. Barcodes inside a `central_sticker` are the central number. Never raises."""
    cfg = config or IdentityConfig()
    if spec is not None and not spec.commitment_labels:
        return []
    allow = list(spec.commitment_labels) if spec is not None else list(GENERIC_COMMITMENT_LABELS)
    deny = [*GLOBAL_COMMITMENT_DENY, *(spec.commitment_deny if spec is not None else [])]
    skip = {*allow, *deny}
    toks = tokenize(ocr.words)
    entries = _entries([("commitment", allow)], deny, [x for x in _all_labels() if x not in skip])
    labels = _find_labels(toks, entries, cfg)
    taken = {i for lab in labels for i in range(lab.start, lab.end)}
    out: list[Candidate] = []

    tables = [r.box for r in regions if r.kind is RegionKind.table]
    if page_role == "sap_screen" and tables:
        sap = _sap_rows(toks, labels, taken, tables, cfg)
        if sap is not None:
            header, rows = sap
            for row in rows:
                value = " ".join(t.text for t in row)
                out.append(_make(value, row, header, ocr, regions, ink_boxes, [], [NOTE_SAP_ROW]))
            return _dedupe([c.model_copy(update={"source": SourceKind.screen}) for c in out])

    denied = _denied_indices(
        labels, toks, taken, cfg, radius=cfg.commitment_radius, max_tokens=cfg.commitment_max_tokens
    )
    for lab in labels:
        if lab.kind != "allow":
            continue
        idx, below = _value_indices(
            lab,
            toks,
            taken,
            cfg,
            radius=cfg.commitment_radius,
            max_tokens=cfg.commitment_max_tokens,
            digits_continue=True,
        )
        used = [toks[i] for i in idx if i not in denied]
        if not used:
            continue
        notes = [NOTE_BELOW_LABEL] if below else []
        value = " ".join(t.text for t in used)
        out.append(
            _make(value, used, lab, ocr, regions, ink_boxes, [RegionKind.commitment_label], notes)
        )

    out.extend(_commitment_barcodes(barcodes, regions, spec, allow, deny, cfg))
    return _dedupe(out)


def _commitment_barcodes(
    barcodes: Sequence[BarcodeHit],
    regions: Sequence[Region],
    spec: SenderSpec | None,
    allow: Sequence[str],
    deny: Sequence[str],
    cfg: IdentityConfig,
) -> list[Candidate]:
    mode = spec.barcode_is_commitment if spec is not None else None
    if mode is False:
        return []
    stickers = [r.box for r in regions if r.kind is RegionKind.central_sticker]
    label_regions = [r.box for r in regions if r.kind is RegionKind.commitment_label]
    allow_phrases = [phrase(x) for x in allow]
    deny_phrases = [phrase(x) for x in deny]
    out: list[Candidate] = []
    for hit in barcodes:
        payload = hit.payload.strip()
        if not payload or inside_any(hit.box, stickers):
            continue
        if _text_has_phrase(hit.nearby_text, deny_phrases):
            continue
        if mode is None:
            labelled = inside_any(hit.box, label_regions) or _text_has_phrase(
                hit.nearby_text, allow_phrases
            )
            if not labelled:
                continue
        notes = [NOTE_BARCODE_HINT] if mode is None else []
        region = RegionKind.commitment_label if inside_any(hit.box, label_regions) else None
        out.append(_barcode_candidate(payload, hit, cfg, region, notes))
    return out
