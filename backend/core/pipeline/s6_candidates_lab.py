"""S6 Candidates, lab fields - central number and receipt date (PLAN §4 S6, T13).

A token becomes a candidate from the label governing it and the region it sits in. Length and
shape never create or reject a candidate: a central number may have any digit count, a sticker
may carry several, and an empty list is a valid answer (S8 turns it into level D, `not_found`).

* ``candidates_central`` - three sources in priority order, every distinct value kept:
  1. the payload of a barcode inside a ``central_sticker`` region;
  2. numeric tokens printed inside that region;
  3. numeric tokens near the caption "מרכזי" when no sticker region formed (the SAP case).
  A barcode or token whose nearest label is another field's label is a decoy and is dropped.
* ``candidates_receipt_date`` - two sources only:
  1. dates inside the ``receipt_stamp`` region, via the whitespace-collapsing parser;
  2. dates near a ``central_sticker``, tagged ``handwritten``.
  Any date governed by another date label (``ת.קבלה``, ``תאריך``, ``תאריך לקיחת הדגימה``,
  ``תאריך הפקה``, ``תאריך הדפסה``, ``תוקף ההתחייבות``) is a decoy and is dropped.

Hard validations come from T8 and only annotate: a date that does not exist or lies in the
future is still emitted, with the closed-set reason code in ``notes``, so S8 can flag it
instead of the field silently going empty. There is deliberately no plausibility check
between the central number and the receipt date (PLAN §5.3).

Pure functions of the S3/S4 outputs: no I/O, no ports other than the clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
import re
from typing import Final

from backend.core.geometry import (
    Size,
    center,
    diagonal,
    distance_to_box,
    readable_line_box,
    union,
)
from backend.core.models import (
    BarcodeHit,
    Box,
    Candidate,
    OcrResult,
    OcrWord,
    ReaderName,
    Region,
    RegionKind,
    SourceKind,
)
from backend.core.normalize import format_date, normalize_central
from backend.core.ports.clock import ClockPort
from backend.core.reading_order import group_lines, has_hebrew
from backend.core.text_match import best_similarity, normalize, similarity
from backend.core.validators import date_not_future, parse_il_date
from backend.settings import Settings

# --- vocabulary ---------------------------------------------------------------------------
CENTRAL_CAPTION: Final = "מרכזי"
# Labels of the other numbers printed around a sticker: commitment, ID, request, case,
# referral, phone and fax numbers. A number governed by one of them is not the central number.
CENTRAL_DECOY_LABELS: Final = (
    "התחייבות",
    "ת.ז",
    "זהות",
    "דרישה",
    "מקרה",
    "הפניה",
    "טלפון",
    "פקס",
    "ביקור",
)
# Labels of the other dates on a form (PLAN T13: the five decoy rules plus the print date),
# with the verb forms the samples print for the same labels ("הופק", "הודפס": B2, A20) and the
# birth date. Matched word by word, so "תאריך לקיחת הדגימה" is caught by any of its words.
DATE_DECOY_LABELS: Final = (
    "ת.קבלה",
    "תאריך",
    "לקיחת",
    "הדגימה",
    "הפקה",
    "הופק",
    "הדפסה",
    "הודפס",
    "תוקף",
    "לידה",
)

# Reason codes from the closed set (models.REASON_TEXT), recorded in Candidate.notes for S8.
NOTE_DATE_FUTURE: Final = "date_future"
NOTE_DATE_IMPOSSIBLE: Final = "date_impossible"
NOTE_STAMP_HANDWRITTEN_DIFFER: Final = "stamp_and_handwritten_differ"

# A decoded barcode passed its symbology's own check; zxing reports no confidence of its own.
BARCODE_READER_CONFIDENCE: Final = 1.0
# Structural: the stamp prints its date with gapped digits ("1 8 -06- 2026"), which OCR splits
# into up to four tokens; one more allows for a stray mark between them.
_DATE_MAX_WORDS: Final = 5
# Structural: day, month and year with one of the separators forms and stamps print. Used only
# to recognise a date-like token that the calendar then rejects; real dates are parsed by T8.
_DATE_SHAPE_RE: Final = re.compile(r"(?<!\d)\d{1,2}[-./]\d{1,2}[-./](?:\d{4}|\d{2})(?!\d)")


# =========================================================================================
# central number
# =========================================================================================


def candidates_central(
    barcodes: Sequence[BarcodeHit],
    ocr: OcrResult,
    regions: Sequence[Region],
    *,
    settings: Settings,
    size: Size,
) -> list[Candidate]:
    """Central-number candidates in source priority order; ALL distinct values kept.

    Intentionally multi-valued (A9 carries three stickers). Never filtered by digit count or
    shape. The same value read by two readers (the barcode and the printed digits) yields one
    candidate per reader, so S8 can see that they agree. `size` is the page in pixels.
    Never raises; nothing found is an empty list.
    """
    diag = diagonal(size)
    stickers = [r for r in regions if r.kind is RegionKind.central_sticker]
    captions = [w for w in ocr.words if _is_caption(w, size, settings)]
    found: list[Candidate] = []

    for region in stickers:
        for hit in barcodes:
            if not _inside(hit.box, region.box) or _barcode_is_decoy(hit, settings):
                continue
            value = normalize_central(hit.payload)
            if value is None:
                continue
            found.append(
                Candidate(
                    value=value,
                    reader=ReaderName.zxing,
                    source=SourceKind.barcode,
                    box=hit.box,
                    reader_confidence=BARCODE_READER_CONFIDENCE,
                    label_distance=_caption_distance(hit.box, captions, size, diag),
                    region=RegionKind.central_sticker,
                )
            )

    for region in stickers:
        # On a printed sticker the number is the human-readable line under the bars; anywhere
        # else in the region a digit is the caption's or a bar read as a "1". On a sticker with
        # no decoded barcode (handwritten), the whole region is the number's place.
        bars = [h for h in barcodes if _inside(h.box, region.box)]
        lines = [
            readable_line_box(
                h.box,
                h.orientation_deg,
                reach=settings.readable_line_reach,
                margin=settings.readable_line_margin,
            )
            for h in bars
        ]
        for word in ocr.words:
            if not _is_value_token(word, settings):
                continue
            if lines:
                if not any(_inside(word.box, line) for line in lines):
                    continue
            elif not _inside(word.box, region.box):
                continue
            # Read off the bars themselves: the printed number stands beside the bars.
            if any(_inside(word.box, hit.box) for hit in barcodes):
                continue
            if _governed_by_decoy(word.box, ocr.words, CENTRAL_DECOY_LABELS, size, settings):
                continue
            value = normalize_central(word.text)
            if value is None:
                continue
            found.append(
                Candidate(
                    value=value,
                    reader=ocr.reader,
                    source=SourceKind.printed,
                    box=word.box,
                    reader_confidence=word.confidence,
                    label_distance=_caption_distance(word.box, captions, size, diag),
                    region=RegionKind.central_sticker,
                )
            )

    if not stickers:
        radius_px = settings.proximity_radius * diag
        for caption in captions:
            for word in ocr.words:
                if not _is_value_token(word, settings):
                    continue
                distance = distance_to_box(center(word.box), caption.box, size)
                if distance > radius_px:
                    continue
                if _governed_by_decoy(word.box, ocr.words, CENTRAL_DECOY_LABELS, size, settings):
                    continue
                value = normalize_central(word.text)
                if value is None:
                    continue
                found.append(
                    Candidate(
                        value=value,
                        reader=ocr.reader,
                        source=SourceKind.handwritten,
                        box=word.box,
                        reader_confidence=word.confidence,
                        label_distance=distance / diag,
                        region=None,
                    )
                )

    return _distinct(found)


# =========================================================================================
# receipt date
# =========================================================================================


def candidates_receipt_date(
    ocr: OcrResult,
    regions: Sequence[Region],
    *,
    settings: Settings,
    clock: ClockPort,
    size: Size,
) -> list[Candidate]:
    """Receipt-date candidates: the 'התקבל' stamp first, then a date beside a central sticker.

    Everything else on the page is an explicit decoy. Values are DD/MM/YYYY (``format_date``); a
    date-like token that is not on the calendar keeps its text and carries
    ``date_impossible``; a future date carries ``date_future``. When the stamp and the
    handwritten date differ, both are emitted and both carry ``stamp_and_handwritten_differ``.
    Never raises; nothing found is an empty list.
    """
    diag = diagonal(size)
    today = clock.now().date()
    lines = group_lines(ocr.words, tolerance=settings.line_height_tolerance)
    windows = [w for line in lines for w in _date_windows(line)]
    stamps = [r for r in regions if r.kind is RegionKind.receipt_stamp]
    stickers = [r for r in regions if r.kind is RegionKind.central_sticker]
    found: list[Candidate] = []

    for words, text in windows:
        box = union(w.box for w in words)
        if _governed_by_decoy(box, ocr.words, DATE_DECOY_LABELS, size, settings):
            continue
        if any(_inside(box, r.box) for r in stamps):
            source, region, distance = SourceKind.stamp, RegionKind.receipt_stamp, 0.0
        else:
            near = [distance_to_box(center(box), r.box, size) for r in stickers]
            if not near or min(near) > settings.receipt_date_radius * diag:
                continue
            source, region = SourceKind.handwritten, RegionKind.central_sticker
            distance = min(near) / diag
        value, notes = _date_value(text, today)
        found.append(
            Candidate(
                value=value,
                reader=ocr.reader,
                source=source,
                box=box,
                reader_confidence=min(w.confidence for w in words),
                label_distance=distance,
                region=region,
                notes=notes,
            )
        )

    found = _distinct(found)
    stamp_values = {c.value for c in found if c.source is SourceKind.stamp}
    hand_values = {c.value for c in found if c.source is SourceKind.handwritten}
    if stamp_values and hand_values and stamp_values != hand_values:
        found = [
            c.model_copy(update={"notes": [*c.notes, NOTE_STAMP_HANDWRITTEN_DIFFER]}) for c in found
        ]
    return found


# =========================================================================================
# helpers
# =========================================================================================


def _date_windows(line: Sequence[OcrWord]) -> list[tuple[list[OcrWord], str]]:
    """Runs of consecutive words on a line that hold exactly one date-like text.

    Digits are written left to right even inside a Hebrew line, so the run is joined in page
    order (left to right). Each start keeps its shortest run. Of two runs where one contains
    the other, the one whose date text is longer wins: a stamp's gapped day ("2 1 -06-") must
    not lose its first digit, while a label glued to a date ("התקב 18-06-") adds nothing.
    """
    ordered = sorted(line, key=lambda w: w.box[1])
    spans: list[tuple[int, int, str]] = []
    for start in range(len(ordered)):
        for stop in range(start + 1, min(start + _DATE_MAX_WORDS, len(ordered)) + 1):
            text = re.sub(r"\s+", "", "".join(w.text for w in ordered[start:stop]))
            match = _DATE_SHAPE_RE.search(text)
            if match:
                spans.append((start, stop, match.group()))
                break

    def beaten(s: tuple[int, int, str]) -> bool:
        for o in spans:
            if o == s:
                continue
            if s[0] <= o[0] and o[1] <= s[1] and len(o[2]) >= len(s[2]):
                return True  # s only adds words that add nothing to the date
            if o[0] <= s[0] and s[1] <= o[1] and len(o[2]) > len(s[2]):
                return True  # s is a truncation of a longer date
        return False

    return [(ordered[a:b], text) for a, b, text in spans if not beaten((a, b, text))]


def _date_value(text: str, today: date) -> tuple[str, list[str]]:
    """Display value DD/MM/YYYY (the form S8 parses and shows) and reason notes (hard rules
    from T8)."""
    parsed = parse_il_date(text)
    if parsed is None:
        return text, [NOTE_DATE_IMPOSSIBLE]
    notes = [] if date_not_future(parsed, now=today) else [NOTE_DATE_FUTURE]
    return format_date(parsed), notes


def _is_caption(word: OcrWord, size: Size, settings: Settings) -> bool:
    """True when the word is the "מרכזי" caption: a sideways (tall) word at the sideways
    threshold, an upright one at the stricter upright threshold. A shorter word ("מרכז") is
    a different word, however similar."""
    if len(normalize(word.text)) < len(CENTRAL_CAPTION):
        return False
    tall = (word.box[2] - word.box[0]) * size[1] > (word.box[3] - word.box[1]) * size[0]
    floor = settings.central_caption_fuzz if tall else settings.upright_caption_fuzz
    return similarity(word.text, CENTRAL_CAPTION) >= floor


def _is_value_token(word: OcrWord, settings: Settings) -> bool:
    """True for a token that can carry a central number: it holds a digit, it is not below
    the text-stream confidence floor, it carries no Hebrew letters (a token with letters is a
    label, or a label glued to its own value), and it is not a date - a date written by a
    sticker is the receipt date, which ``candidates_receipt_date`` claims (date source 2)."""
    return (
        any(ch.isdigit() for ch in word.text)
        and word.confidence >= settings.ocr_min_word_confidence
        and not has_hebrew(word.text)
        and _DATE_SHAPE_RE.search(word.text) is None
    )


def _barcode_is_decoy(hit: BarcodeHit, settings: Settings) -> bool:
    """True when the label nearest the barcode is another field's label, not the caption.

    ``nearby_text`` is ordered nearest first; the first word that is a label decides.
    """
    for text in hit.nearby_text:
        if similarity(text, CENTRAL_CAPTION) >= settings.central_caption_fuzz:
            return False
        if best_similarity(text, CENTRAL_DECOY_LABELS) >= settings.label_fuzz:
            return True
    return False


def _governed_by_decoy(
    box: Box,
    words: Sequence[OcrWord],
    decoys: tuple[str, ...],
    size: Size,
    settings: Settings,
) -> bool:
    """True when a label governing `box` is one of `decoys`.

    A label governs a value from where labels stand on these forms: on the same line on the
    reading-order side (to the right, up to ``label_value_expand`` page widths - the label may
    span several words, "תאריך לקיחת הדגימה"), or directly above it within the proximity
    radius (a column header). Any decoy there decides: the words between a label and its
    value are often other words of the label.
    """
    return any(
        best_similarity(w.text, decoys) >= settings.label_fuzz
        for w in label_positions(box, words, size, settings)
    )


def label_positions(
    box: Box, words: Sequence[OcrWord], size: Size, settings: Settings
) -> list[OcrWord]:
    """The Hebrew words standing where a label of `box` would, nearest first."""
    height = box[2] - box[0]
    cy = center(box)[0]
    reach_x = settings.label_value_expand
    reach_px = settings.proximity_radius * diagonal(size)
    found: list[tuple[float, OcrWord]] = []
    for word in words:
        if not has_hebrew(word.text) or _inside(word.box, box):
            continue
        same_line = abs(center(word.box)[0] - cy) <= settings.line_height_tolerance * height / 2.0
        to_the_right = word.box[1] >= center(box)[1] and word.box[1] - box[3] <= reach_x
        overlaps_x = word.box[1] < box[3] and box[1] < word.box[3]
        above = word.box[2] <= box[0] + height / 2.0 and overlaps_x
        distance = distance_to_box(center(word.box), box, size)
        if (same_line and to_the_right) or (above and distance <= reach_px):
            found.append((distance, word))
    found.sort(key=lambda item: item[0])
    return [word for _, word in found]


def _caption_distance(
    box: Box, captions: Sequence[OcrWord], size: Size, diag: float
) -> float | None:
    """Distance from `box` to the nearest caption, as a share of the diagonal; None if none."""
    if not captions:
        return None
    return min(distance_to_box(center(c.box), box, size) for c in captions) / diag


def _inside(box: Box, region: Box) -> bool:
    """True when the centre of `box` lies within `region`."""
    y, x = center(box)
    return region[0] <= y <= region[2] and region[1] <= x <= region[3]


def _distinct(candidates: Iterable[Candidate]) -> list[Candidate]:
    """One candidate per (value, reader, source), first one kept (sources come in priority
    order), order preserved."""
    seen: set[tuple[str, ReaderName, SourceKind]] = set()
    kept: list[Candidate] = []
    for c in candidates:
        key = (c.value, c.reader, c.source)
        if key not in seen:
            seen.add(key)
            kept.append(c)
    return kept
