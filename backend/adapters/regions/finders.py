"""Text-and-geometry finders of the rule-based region detector (PLAN §4 S4).

Pure functions of OCR words, barcodes and the page size: no pixels, no I/O. A finder says
where a thing is by the labels printed around it, never by the length or shape of a value -
a sticker's number may have any digit count, and may be absent altogether.

Boxes are normalized 0..1 (y_min, x_min, y_max, x_max); distances are measured in pixels
through the page size so a portrait page is not stretched.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from rapidfuzz import fuzz, utils

from backend.core.geometry import (
    Size,
    center,
    diagonal,
    distance_to_box,
    grow,
    iou,
    union,
)
from backend.core.models import BarcodeHit, Box, OcrResult, OcrWord, Region, RegionKind
from backend.core.reading_order import group_lines, has_hebrew

DETECTOR_NAME: Final = "rule-based"

# --- vocabulary ---------------------------------------------------------------------------
CENTRAL_CAPTION: Final = "מרכזי"
STAMP_PHRASES: Final = ("התקבל", "המעבדה המרכזית", "לנגיפים")
PATIENT_LABELS: Final = (
    "ת.ז",
    "מ.ז",
    "ת. זהות",
    "מספר זהות",
    "שם משפחה",
    "שם פרטי",
    "פרטי המבוטח",
    "פרטי המטופל",
)
# The plural "אבחנות" is the form printed on the SAP-derived forms of the sample set.
CLINICAL_LABELS: Final = ("אבחנה", "אבחנות", "הבדיקה הנדרשת", "הערות", "הערה", "סיבת", "ממצאים")

# Matches of one thing closer than this many proximity radii belong together (a stamp's
# phrases, a patient block's labels); farther ones are another thing, e.g. the same words in
# an address line elsewhere on the page.
_CLUSTER_GAP_RADII: Final = 2.0
# Vertical margin added around a label-anchored region, as a share of the label's own height:
# a handwritten value rarely sits on exactly the same baseline as its printed label.
_LABEL_V_PAD: Final = 0.5
# A token counts as a number when it holds a digit and nothing but digits and separators.
_NUMBER_SEPARATORS: Final = frozenset("-/.:,")
# A caption word that is not printed sideways must match this exactly (similarity, 0..100).
_EXACT: Final = 100.0
# Two detections of one kind overlapping this much are the same region seen twice.
_SAME_REGION_IOU: Final = 0.5


@dataclass(frozen=True)
class Match:
    """A phrase found on the page: where, how well it matched, and what was read there."""

    box: Box
    ratio: float  # 0..100
    text: str
    phrase: str = ""  # the vocabulary entry this matched


def normalize(text: str) -> str:
    """Lower-cased, punctuation-free text for fuzzy comparison ('ת"ז' and 'ת.ז' compare equal)."""
    return utils.default_process(text)


def is_number(text: str) -> bool:
    """True for a token made of digits and separators with at least one digit."""
    token = text.strip()
    return any(ch.isdigit() for ch in token) and all(
        ch.isdigit() or ch in _NUMBER_SEPARATORS for ch in token
    )


def word_ratio(text: str, phrase: str) -> float:
    """Similarity (0..100) of one OCR token to `phrase`, ignoring case and punctuation."""
    return float(fuzz.ratio(normalize(text), normalize(phrase)))


def find_phrase(lines: Sequence[Sequence[OcrWord]], phrase: str, *, fuzz_min: float) -> list[Match]:
    """Places in `lines` where `phrase` is printed, best first, overlaps removed.

    A phrase may span a few consecutive words of a line (one fewer or one more than it has
    tokens, because OCR splits and merges words); word order does not matter, since the label
    text may be stored in either reading direction.
    """
    target = normalize(phrase)
    tokens = max(1, len(target.split()))
    sizes = sorted({max(1, tokens - 1), tokens, tokens + 1})
    found: list[Match] = []
    for line in lines:
        for size in sizes:
            for start in range(len(line) - size + 1):
                window = line[start : start + size]
                text = " ".join(w.text for w in window)
                ratio = float(fuzz.token_sort_ratio(normalize(text), target))
                if ratio >= fuzz_min:
                    found.append(Match(union(w.box for w in window), ratio, text, phrase))
    found.sort(key=lambda m: m.ratio, reverse=True)
    kept: list[Match] = []
    for match in found:
        if all(iou(match.box, other.box) < _SAME_REGION_IOU for other in kept):
            kept.append(match)
    return kept


def find_central_stickers(
    barcodes: Sequence[BarcodeHit],
    ocr: OcrResult,
    *,
    radius: float,
    fuzz_min: float,
    upright_fuzz_min: float,
    size: Size,
) -> list[Region]:
    """Central-number stickers, by two independent branches. Never filters by digit count.

    `fuzz_min` is the similarity a sideways caption needs; `upright_fuzz_min` is the (higher)
    similarity a caption needs on a page with no barcode, where it is ordinary horizontal text
    and nothing but its spelling tells it from "המרכזית" or "מרכז".

    (a) A barcode with the "מרכזי" caption near it. The caption is owned by the nearest
        barcode within `radius` (a share of the page diagonal), so stacked stickers do not
        borrow each other's. The region spans the bars, the caption and the printed number.
    (b) Only on a page with no barcode at all (the SAP pages): the caption with a numeric
        token near it. The region spans both.
    """
    radius_px = radius * diagonal(size)
    captions = [(w, r) for w in ocr.words if (r := word_ratio(w.text, CENTRAL_CAPTION)) >= fuzz_min]
    numbers = [w for w in ocr.words if is_number(w.text)]
    regions: list[Region] = []

    if barcodes:
        # Printed sideways, the caption is a tall word. A wide one must read exactly: upright
        # "מרכז רפואי" in a letterhead scores 89 against the caption and must not pass.
        stood_up = [(w, r) for w, r in captions if r >= _EXACT or _is_tall(w, size)]
        owned = _assign_to_nearest(
            [w for w, _ in stood_up], [b.box for b in barcodes], radius_px, size
        )
        ratio_of = {id(w): r for w, r in stood_up}
        digits = _assign_to_nearest(numbers, [b.box for b in barcodes], radius_px, size)
        for index, hit in enumerate(barcodes):
            caption_words = owned.get(index, [])
            if not caption_words:
                continue
            parts = [
                hit.box,
                *(w.box for w in caption_words),
                *(w.box for w in digits.get(index, [])),
            ]
            best = max(ratio_of[id(w)] for w in caption_words)
            regions.append(
                Region(
                    kind=RegionKind.central_sticker,
                    box=union(parts),
                    detector=DETECTOR_NAME,
                    score=best / 100.0,
                    label_text=max(caption_words, key=lambda w: ratio_of[id(w)]).text,
                )
            )
        return regions

    for word, ratio in captions:
        if ratio < upright_fuzz_min:
            continue
        near = [n for n in numbers if distance_to_box(center(n.box), word.box, size) <= radius_px]
        if near:
            regions.append(
                Region(
                    kind=RegionKind.central_sticker,
                    box=union([word.box, *(n.box for n in near)]),
                    detector=DETECTOR_NAME,
                    score=ratio / 100.0,
                    label_text=word.text,
                )
            )
    return _drop_duplicates(regions)


def find_receipt_stamp(
    ocr: OcrResult,
    *,
    fuzz_min: float,
    tolerance: float,
    radius: float,
    size: Size,
) -> Region | None:
    """The receipt stamp, from its printed phrases ("התקבל", the lab's name).

    The phrases are faint and overprinted, hence the lower `fuzz_min`. Matches are grouped by
    proximity and the best group wins, provided it holds the stamp's own word and at least one
    more phrase: the lab's name alone also appears in address lines, and "התקבל" alone is a
    near match for the "ת.קבלה" column header, so either by itself marks a stamp that is not
    there. The region spans the group, which also covers the date printed between its
    phrases. None when no such group is found.
    """
    lines = group_lines(ocr.words, tolerance=tolerance)
    matches = [m for p in STAMP_PHRASES for m in find_phrase(lines, p, fuzz_min=fuzz_min)]
    clusters = [
        c
        for c in _clusters(matches, radius * diagonal(size), size)
        if STAMP_PHRASES[0] in {m.phrase for m in c} and len({m.phrase for m in c}) >= 2
    ]
    cluster = sorted(
        max(clusters, key=lambda c: sum(m.ratio for m in c), default=[]),
        key=lambda m: m.ratio,
        reverse=True,
    )
    if not cluster:
        return None
    return Region(
        kind=RegionKind.receipt_stamp,
        box=union(m.box for m in cluster),
        detector=DETECTOR_NAME,
        score=sum(m.ratio for m in cluster) / len(cluster) / 100.0,
        label_text=cluster[0].text,
    )


def find_patient_box(
    ocr: OcrResult,
    *,
    fuzz_min: float,
    tolerance: float,
    radius: float,
    value_expand: float,
    size: Size,
) -> tuple[Region, Box] | None:
    """The patient-details block, from the labels it carries (ID, names).

    Returns the region and its anchor (the box spanning the labels found together). The
    region reaches `value_expand` page widths past the anchor toward the value side, where
    the values are written; a detector that finds the block drawn as a ruled frame uses the
    anchor to pick that frame instead. None when no label is found.
    """
    lines = group_lines(ocr.words, tolerance=tolerance)
    matches = [m for p in PATIENT_LABELS for m in find_phrase(lines, p, fuzz_min=fuzz_min)]
    cluster = _best_cluster(matches, radius * diagonal(size), size)
    if not cluster:
        return None
    anchor = union(m.box for m in cluster)
    pad = _LABEL_V_PAD * (anchor[2] - anchor[0])
    region = Region(
        kind=RegionKind.patient_box,
        box=_toward_value(anchor, cluster[0].text, value_expand, pad),
        detector=DETECTOR_NAME,
        score=sum(m.ratio for m in cluster) / len(cluster) / 100.0,
        label_text=cluster[0].text,
    )
    return region, anchor


def find_commitment_labels(
    ocr: OcrResult,
    labels: Iterable[str],
    *,
    fuzz_min: float,
    tolerance: float,
    value_expand: float,
) -> list[Region]:
    """One region per printed occurrence of the sender's commitment labels.

    Each spans its label plus `value_expand` page widths toward the value side, so the
    commitment number written beside the label is inside. Which tokens are numbers, and which
    belong to a deny list, is for S6 to decide.
    """
    lines = group_lines(ocr.words, tolerance=tolerance)
    matches = [m for label in labels for m in find_phrase(lines, label, fuzz_min=fuzz_min)]
    regions = [
        Region(
            kind=RegionKind.commitment_label,
            box=_toward_value(m.box, m.text, value_expand, _LABEL_V_PAD * (m.box[2] - m.box[0])),
            detector=DETECTOR_NAME,
            score=m.ratio / 100.0,
            label_text=m.text,
        )
        for m in matches
    ]
    return _drop_duplicates(regions)


def find_clinical(
    ocr: OcrResult,
    *,
    fuzz_min: float,
    tolerance: float,
    radius: float,
    value_expand: float,
    size: Size,
) -> list[Region]:
    """Clinical areas: the diagnosis, requested test, remarks and reason fields.

    Deliberately generous - toward the value side by `value_expand` page widths and down by
    one proximity radius, where a multi-line answer runs - because these regions only ever
    exclude data from leaving the machine: too large costs a crop, too small leaks.
    """
    lines = group_lines(ocr.words, tolerance=tolerance)
    radius_down = radius * diagonal(size) / size[1]
    regions: list[Region] = []
    for label in CLINICAL_LABELS:
        for match in find_phrase(lines, label, fuzz_min=fuzz_min):
            box = _toward_value(match.box, match.text, value_expand, 0.0)
            regions.append(
                Region(
                    kind=RegionKind.clinical,
                    box=grow(box, bottom=radius_down),
                    detector=DETECTOR_NAME,
                    score=match.ratio / 100.0,
                    label_text=match.text,
                )
            )
    return _drop_duplicates(regions)


def letterhead(fraction: float) -> Region:
    """The top `fraction` of the page, where every sample's letterhead sits (used by S5)."""
    return Region(
        kind=RegionKind.letterhead,
        box=(0.0, 0.0, fraction, 1.0),
        detector=DETECTOR_NAME,
        score=1.0,
    )


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------


def _assign_to_nearest(
    words: Sequence[OcrWord], anchors: Sequence[Box], radius_px: float, size: Size
) -> dict[int, list[OcrWord]]:
    """Give every word to the nearest anchor within `radius_px`; words farther from all are
    dropped. Returns anchor index -> its words."""
    owned: dict[int, list[OcrWord]] = defaultdict(list)
    for word in words:
        distances = [(distance_to_box(center(word.box), a, size), i) for i, a in enumerate(anchors)]
        nearest, index = min(distances)
        if nearest <= radius_px:
            owned[index].append(word)
    return owned


def _best_cluster(matches: Sequence[Match], gap_px: float, size: Size) -> list[Match]:
    """The group of matches lying close together whose summed similarity is highest."""
    groups = _clusters(matches, gap_px, size)
    best = max(groups, key=lambda g: sum(m.ratio for m in g), default=[])
    return sorted(best, key=lambda m: m.ratio, reverse=True)


def _clusters(matches: Sequence[Match], gap_px: float, size: Size) -> list[list[Match]]:
    """Matches grouped by proximity: each lies within the cluster gap of another in its group."""
    gap = _CLUSTER_GAP_RADII * gap_px
    unplaced = list(matches)
    groups: list[list[Match]] = []
    while unplaced:
        group = [unplaced.pop()]
        grew = True
        while grew:
            grew = False
            for other in list(unplaced):
                if any(distance_to_box(center(other.box), g.box, size) <= gap for g in group):
                    group.append(other)
                    unplaced.remove(other)
                    grew = True
        groups.append(group)
    return groups


def _is_tall(word: OcrWord, size: Size) -> bool:
    """True when the word is taller than wide on the page: text printed sideways."""
    return (word.box[2] - word.box[0]) * size[1] > (word.box[3] - word.box[1]) * size[0]


def _toward_value(box: Box, label: str, expand: float, v_pad: float) -> Box:
    """`box` extended `expand` page widths toward the side where the value is written: left
    for a Hebrew label (right-to-left), right for a Latin one."""
    if has_hebrew(label):
        return grow(box, left=expand, top=v_pad, bottom=v_pad)
    return grow(box, right=expand, top=v_pad, bottom=v_pad)


def _drop_duplicates(regions: Sequence[Region]) -> list[Region]:
    """Keep the best-scored of regions that overlap heavily, best first."""
    kept: list[Region] = []
    for region in sorted(regions, key=lambda r: r.score, reverse=True):
        if all(r.kind != region.kind or iou(r.box, region.box) < _SAME_REGION_IOU for r in kept):
            kept.append(region)
    return kept
