"""S5 - sender and page-role classification (PLAN §4 S5, T10).

Order: suppress the destination -> text match -> logo -> vision model -> none. The sender is
chosen from the closed list or is None; it is never invented (project rule §2). Pure function:
one page in, one `Classification` out. Side effects (fuzzy matching, ORB, the provider) are
reached through ports.
"""

# ruff: noqa: RUF001  (Hebrew letters are not confusable text here)

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import re
from typing import Literal
import unicodedata

from pydantic import Field, model_validator
import structlog

from backend.core.errors import SafeLineError
from backend.core.models import (
    REASON_TEXT,
    BarcodeHit,
    Box,
    CropPayload,
    FieldKey,
    ImageRef,
    NormalizedPage,
    OcrResult,
    OcrWord,
    Region,
    RegionKind,
    Strict,
    VisionRequest,
)
from backend.core.ports.logo import LogoMatcherPort
from backend.core.ports.senders import SenderIndexPort
from backend.core.ports.vision_llm import VisionLLMPort
from backend.core.vision_schema import SENDER_SCHEMA

log = structlog.get_logger()

PageRole = Literal["primary_form", "sap_screen", "lab_report", "continuation", "blank", "unknown"]
SenderMethod = Literal["text", "logo", "model", "none"]

# The institution a form is addressed TO. It appears on nearly every page and must never win.
# "אל:" and "לכבוד" also remove the tokens that follow them (the addressee's name).
DESTINATION_TOKENS = (
    "תל השומר",
    "שיבא",
    "מעבדת נגיפים",
    "המעבדה המרכזית לנגיפים",
    "המרכז הרפואי שיבא",
    "אל:",
    "לכבוד",
)

# What the vision model may answer when it cannot choose a list entry.
NOT_RECOGNISED = "לא מוכר"

# Labels on a SAP screenshot (VERIFY against the source in T5/T7: read from the sample pages).
# `גורם מפנה` is the issuer; `מבטח` is the payer and is used only to cross-check it.
SAP_REFERRER_LABEL = ("גורם", "מפנה")
SAP_PAYER_LABEL = ("מבטח",)
SAP_LABELS = (
    SAP_REFERRER_LABEL,
    SAP_PAYER_LABEL,
    ("קשרי", "ביטוח"),
    ("נתוני", "ביקור"),
    ("תאריך",),
)

# Printed cues for the page role (PLAN §4 S5 table).
_SAP_CUES = (("נתוני", "ביקור"), ("קשרי", "ביטוח"))
_LAB_CUES = (("דוח",), ("labos",), ("רשימת", "עבודה"))
_PAGE_OF_RE = re.compile(r"עמוד (\d+) מתוך (\d+)")

# Ordinal role-confidence levels. S8 owns the weights; these only say how the role was decided,
# so they are signal values and not pass/fail thresholds.
ROLE_EXPLICIT = 0.9  # a printed cue decided the role
ROLE_STRUCTURAL = 0.7  # inferred from regions or word count
ROLE_NONE = 0.0  # nothing decided it

_NIQQUD = re.compile("[\u0591-\u05c7]")
_QUOTES = re.compile("[\"'`\u05f3\u05f4\u2018\u2019\u201c\u201d]")
# Structural: punctuation OCR attaches to the edge of a word; never part of a label's identity.
_EDGE_PUNCT = ".,;:|()[]{}<>-\u2013\u2014\u05be/\\"


class ClassifyConfig(Strict):
    """S5 thresholds. `config/models.yaml: classify` is authoritative; a test keeps these
    defaults equal to it. `eval/calibrate.py` sweeps them."""

    sender_min_ratio: int | None = None  # None -> the closed list's own min_ratio
    logo_min_inlier: float = Field(default=0.35, ge=0.0, le=1.0)  # ORB inlier ratio
    letterhead_top_fraction: float = Field(default=0.22, gt=0.0, le=1.0)  # of page height
    blank_max_words: int = Field(default=15, ge=0)  # fewer words than this (and no barcode)
    destination_follow_tokens: int = Field(default=4, ge=0)  # removed after "אל:"/"לכבוד"
    field_line_tolerance: float = Field(default=0.015, gt=0.0)  # normalised y distance
    field_value_max_tokens: int = Field(default=6, ge=1)
    prompt_version: str = "v1"


class Classification(Strict):
    """Output of S5. `sender_score` is 0-100 for text, inlier ratio x 100 for logo, and 0.0 for
    the model (the model never assigns its own confidence; S8 does). `sender_flags` holds
    REASON_TEXT keys: `sender_not_in_list` when no sender was resolved, `multiple_candidates`
    when a SAP page's referrer and payer disagree."""

    sender: str | None
    sender_score: float
    sender_method: SenderMethod
    family: str | None
    page_role: PageRole
    role_score: float
    sender_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _flags_are_closed_set(self) -> Classification:
        unknown = set(self.sender_flags) - set(REASON_TEXT)
        if unknown:
            raise ValueError(f"sender_flags outside the closed reason set: {sorted(unknown)}")
        return self


# --- tokens ------------------------------------------------------------------------------------


def _plain(text: str) -> str:
    """Lower-cased word without niqqud, quotes or edge punctuation. Never raises."""
    cleaned = _QUOTES.sub("", _NIQQUD.sub("", unicodedata.normalize("NFKC", text))).lower()
    return cleaned.strip().strip(_EDGE_PUNCT)


def _expand(words: Sequence[OcrWord]) -> list[OcrWord]:
    """One token per whitespace-separated part, so a word holding two tokens counts as two."""
    out: list[OcrWord] = []
    for word in words:
        out.extend(
            OcrWord(text=part, box=word.box, confidence=word.confidence)
            for part in word.text.split()
        )
    return out


def _is_bare_colon(word: OcrWord) -> bool:
    return word.text.strip() == ":"


def _destination_phrases() -> list[tuple[str, ...]]:
    """Multi-token destination phrases; the two trigger tokens are handled separately."""
    phrases = []
    for token in DESTINATION_TOKENS:
        parts = tuple(p for p in (_plain(x) for x in token.split()) if p)
        if parts and parts not in {("אל",), ("לכבוד",)}:
            phrases.append(parts)
    return phrases


def suppress_destination(words: Sequence[OcrWord], *, follow_tokens: int) -> list[OcrWord]:
    """Tokens without the destination: DESTINATION_TOKENS removed, plus `follow_tokens` tokens
    after "אל:"/"לכבוד". Input order is reading order, kept. Never raises."""
    toks = _expand(words)
    plains = [_plain(t.text) for t in toks]
    drop = [False] * len(toks)
    for i, tok in enumerate(toks):
        has_next_colon = i + 1 < len(toks) and _is_bare_colon(toks[i + 1])
        is_trigger = plains[i] == "לכבוד" or (
            plains[i] == "אל" and (tok.text.strip().endswith(":") or has_next_colon)
        )
        if not is_trigger:
            continue
        end = i + 1 if plains[i] == "אל" and has_next_colon else i
        for j in range(i, min(len(toks), end + 1 + follow_tokens)):
            drop[j] = True
    for phrase in _destination_phrases():
        size = len(phrase)
        for i in range(len(toks) - size + 1):
            if tuple(plains[i : i + size]) == phrase:
                for j in range(i, i + size):
                    drop[j] = True
    return [t for t, gone in zip(toks, drop, strict=True) if not gone]


def _join(tokens: Sequence[OcrWord]) -> str:
    return " ".join(p for p in (_plain(t.text) for t in tokens) if p)


def _center(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _in_box(word: OcrWord, box: Box) -> bool:
    y, x = _center(word.box)
    return box[0] <= y <= box[2] and box[1] <= x <= box[3]


def _overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _has_phrase(plains: Sequence[str], phrase: tuple[str, ...]) -> bool:
    size = len(phrase)
    return any(tuple(plains[i : i + size]) == phrase for i in range(len(plains) - size + 1))


# --- page role ---------------------------------------------------------------------------------


def classify_role(
    page: NormalizedPage,
    ocr: OcrResult,
    regions: Sequence[Region],
    barcodes: Sequence[BarcodeHit],
    cfg: ClassifyConfig,
) -> tuple[PageRole, float]:
    """Page role and how it was decided. Uses the un-suppressed tokens. Never raises."""
    plains = [p for p in (_plain(t.text) for t in _expand(ocr.words)) if p]
    kinds = {r.kind for r in regions}
    if len(plains) < cfg.blank_max_words and not barcodes:
        return "blank", ROLE_STRUCTURAL
    rotated = page.measurement.rotation_deg in (90, 270) and "rotate" in page.corrections_applied
    if RegionKind.table in kinds:
        if any(_has_phrase(plains, cue) for cue in _SAP_CUES):
            return "sap_screen", ROLE_EXPLICIT
        if rotated:
            return "sap_screen", ROLE_STRUCTURAL
    if any(_has_phrase(plains, cue) for cue in _LAB_CUES):
        return "lab_report", ROLE_EXPLICIT
    found = _PAGE_OF_RE.search(" ".join(plains))
    if found is not None and int(found.group(1)) > 1:
        return "continuation", ROLE_EXPLICIT
    if kinds & {RegionKind.commitment_label, RegionKind.patient_box}:
        return "primary_form", ROLE_STRUCTURAL
    return "unknown", ROLE_NONE


# --- sender ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Resolved:
    canonical: str | None
    score: float
    method: SenderMethod
    flags: list[str] = field(default_factory=list)


_UNRESOLVED = _Resolved(None, 0.0, "none")


def _field_value(
    tokens: Sequence[OcrWord], label: tuple[str, ...], cfg: ClassifyConfig
) -> str | None:
    """Text after `label` on the same line, up to the next SAP label. None when absent/empty."""
    plains = [_plain(t.text) for t in tokens]
    size = len(label)
    stops = [s for s in SAP_LABELS if s != label]
    for i in range(len(tokens) - size + 1):
        if tuple(plains[i : i + size]) != label:
            continue
        line_y = _center(tokens[i + size - 1].box)[0]
        value: list[str] = []
        for j in range(i + size, len(tokens)):
            if abs(_center(tokens[j].box)[0] - line_y) > cfg.field_line_tolerance:
                break
            if any(tuple(plains[j : j + len(s)]) == s for s in stops):
                break
            if plains[j]:
                value.append(plains[j])
            if len(value) >= cfg.field_value_max_tokens:
                break
        if value:
            return " ".join(value)
    return None


def _from_sap_screen(
    kept: Sequence[OcrWord], senders: SenderIndexPort, ratio: int, cfg: ClassifyConfig
) -> _Resolved | None:
    """Sender from `גורם מפנה`; None means the field is absent and the normal steps run."""
    referrer = _field_value(kept, SAP_REFERRER_LABEL, cfg)
    if referrer is None:
        return None
    hit = senders.match(referrer, min_ratio=ratio)
    if hit is None:
        return _UNRESOLVED
    flags: list[str] = []
    payer = _field_value(kept, SAP_PAYER_LABEL, cfg)
    payer_hit = senders.match(payer, min_ratio=ratio) if payer else None
    if payer_hit is not None and payer_hit.canonical != hit.canonical:
        flags.append("multiple_candidates")
    return _Resolved(hit.canonical, hit.score, "text", flags)


def _letterhead_box(regions: Sequence[Region], cfg: ClassifyConfig) -> Box:
    letterheads = [r for r in regions if r.kind is RegionKind.letterhead]
    if letterheads:
        return max(letterheads, key=lambda r: r.score).box
    return (0.0, 0.0, cfg.letterhead_top_fraction, 1.0)


def _ask_model(
    page: NormalizedPage,
    box: Box,
    regions: Sequence[Region],
    senders: SenderIndexPort,
    vision: VisionLLMPort,
    crop: Callable[[ImageRef, Box], bytes],
    cfg: ClassifyConfig,
) -> str | None:
    """Closed-list answer from the model, or None. Only the letterhead crop is sent, never a
    clinical area; an answer outside the list is discarded."""
    if any(r.kind is RegionKind.clinical and _overlaps(r.box, box) for r in regions):
        return None
    names = [s.canonical for s in senders.specs]
    request = VisionRequest(
        job_id=page.raw.image.job_id,
        page_id=page.raw.page_id,
        task="sender",
        crops=[
            CropPayload(
                field=FieldKey.sender,
                png=crop(page.color, box),
                region_kind=RegionKind.letterhead,
                box=box,
            )
        ],
        sender_options=[*names, NOT_RECOGNISED],
        prompt_version=cfg.prompt_version,
        response_schema=SENDER_SCHEMA,
    )
    try:
        response = vision.extract(request)
    except SafeLineError as exc:
        if not exc.recoverable:
            raise
        log.warning("sender_model_failed", page_id=page.raw.page_id, code=exc.code)
        return None
    allowed = {unicodedata.normalize("NFC", n): n for n in names}
    for answer in (*response.candidates, response.raw_text):
        canonical = allowed.get(unicodedata.normalize("NFC", answer.strip()))
        if canonical is not None:
            return canonical
    return None


def _resolve_sender(
    page: NormalizedPage,
    kept: Sequence[OcrWord],
    regions: Sequence[Region],
    role: PageRole,
    senders: SenderIndexPort,
    cfg: ClassifyConfig,
    *,
    vision: VisionLLMPort | None,
    logo: LogoMatcherPort | None,
    crop: Callable[[ImageRef, Box], bytes] | None,
) -> _Resolved:
    ratio = cfg.sender_min_ratio if cfg.sender_min_ratio is not None else senders.min_ratio
    if role == "sap_screen":
        screen = _from_sap_screen(kept, senders, ratio, cfg)
        if screen is not None:
            return screen
    box = _letterhead_box(regions, cfg)
    letterhead = _join([t for t in kept if _in_box(t, box)])
    # A SAP screenshot has no letterhead; elsewhere an empty letterhead falls back to the page.
    text = letterhead if letterhead and role != "sap_screen" else _join(kept)
    hit = senders.match(text, min_ratio=ratio) if text else None
    if hit is not None:
        return _Resolved(hit.canonical, hit.score, "text")
    if logo is not None:
        logo_hit = logo.match(page, box=box)
        if (
            logo_hit is not None
            and logo_hit.inlier_ratio >= cfg.logo_min_inlier
            and senders.get(logo_hit.canonical) is not None
        ):
            return _Resolved(logo_hit.canonical, logo_hit.inlier_ratio * 100.0, "logo")
    if vision is not None and crop is not None:
        answer = _ask_model(page, box, regions, senders, vision, crop, cfg)
        if answer is not None:
            return _Resolved(answer, 0.0, "model")
    return _UNRESOLVED


def classify(
    page: NormalizedPage,
    ocr: OcrResult,
    regions: Sequence[Region],
    senders: SenderIndexPort,
    *,
    vision: VisionLLMPort | None = None,
    logo: LogoMatcherPort | None = None,
    crop: Callable[[ImageRef, Box], bytes] | None = None,
    barcodes: Sequence[BarcodeHit] = (),
    config: ClassifyConfig | None = None,
) -> Classification:
    """Sender (closed list or None) and page role of one page.

    `vision` needs `crop` (letterhead crop; a full page is never sent) and is passed as None
    under LOCAL_ONLY. `barcodes` only feeds the blank-page rule. Never raises on a page whose
    OCR is empty; an unresolved sender is None with `sender_not_in_list` (not on blank pages)."""
    cfg = config or ClassifyConfig()
    role, role_score = classify_role(page, ocr, regions, barcodes, cfg)
    kept = suppress_destination(ocr.words, follow_tokens=cfg.destination_follow_tokens)
    resolved = _resolve_sender(
        page, kept, regions, role, senders, cfg, vision=vision, logo=logo, crop=crop
    )
    flags = list(resolved.flags)
    if resolved.canonical is None and role != "blank":
        flags.append("sender_not_in_list")
    spec = senders.get(resolved.canonical) if resolved.canonical is not None else None
    return Classification(
        sender=resolved.canonical,
        sender_score=resolved.score,
        sender_method=resolved.method,
        family=spec.family if spec is not None else None,
        page_role=role,
        role_score=role_score,
        sender_flags=flags,
    )
