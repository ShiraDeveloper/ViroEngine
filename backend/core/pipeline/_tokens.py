"""Token and box helpers shared by S6 and S7. Pure functions, standard library only.

Boxes are normalised (y_min, x_min, y_max, x_max) with the origin top-left, as everywhere else.
Nothing here raises on empty input.
"""

# ruff: noqa: RUF001  (Hebrew letters are not confusable text here)

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import sqrt
import re
import unicodedata

from backend.core.models import Box, OcrWord

# Structural: Hebrew points and cantillation marks (U+0591-U+05C7) carry no label identity.
_NIQQUD = re.compile("[\u0591-\u05c7]")
# Structural: every quote-like character OCR may print for a geresh/gershayim/apostrophe.
_QUOTES = re.compile("[\"'`\u05f3\u05f4\u2018\u2019\u201c\u201d]")
# Structural: punctuation OCR attaches to the edge of a word; never part of a label's identity.
_EDGE_PUNCT = ".,;:|()[]{}<>-\u2013\u2014\u05be/\\"

# Structural: the page is the unit square, so its diagonal is sqrt(2).
UNIT_DIAGONAL = sqrt(2.0)


@dataclass(frozen=True)
class Token:
    """One whitespace-separated OCR token. `text` keeps case; `plain` is for label matching."""

    text: str
    plain: str
    box: Box
    confidence: float


def plain(text: str) -> str:
    """Lower-cased text without niqqud, quotes or edge punctuation. Never raises."""
    cleaned = _QUOTES.sub("", _NIQQUD.sub("", unicodedata.normalize("NFKC", text))).lower()
    return cleaned.strip().strip(_EDGE_PUNCT)


def clean(text: str) -> str:
    """Value text: NFKC and edge punctuation removed; case and inner characters are kept, so
    'X132767' stays 'X132767'. Never raises."""
    return unicodedata.normalize("NFKC", text).strip().strip(_EDGE_PUNCT).strip()


def tokenize(words: Iterable[OcrWord]) -> list[Token]:
    """One token per whitespace-separated part; parts with nothing left after cleaning (a bare
    colon, a dash) are dropped. Reading order of the input is kept."""
    out: list[Token] = []
    for word in words:
        for part in word.text.split():
            key = plain(part)
            if key:
                out.append(Token(clean(part) or part, key, word.box, word.confidence))
    return out


def phrase(text: str) -> tuple[str, ...]:
    """A label as a tuple of plain tokens; an empty label yields an empty tuple."""
    return tuple(p for p in (plain(t) for t in text.split()) if p)


def center(box: Box) -> tuple[float, float]:
    """Centre of a box as (y, x)."""
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def union(boxes: Sequence[Box]) -> Box:
    """Smallest box containing all of `boxes`; the whole page for an empty sequence."""
    if not boxes:
        return (0.0, 0.0, 1.0, 1.0)
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def inside(box: Box, outer: Box) -> bool:
    """True when the centre of `box` lies within `outer`."""
    y, x = center(box)
    return outer[0] <= y <= outer[2] and outer[1] <= x <= outer[3]


def inside_any(box: Box, outers: Iterable[Box]) -> bool:
    """True when the centre of `box` lies within at least one of `outers`."""
    return any(inside(box, o) for o in outers)


def overlaps(a: Box, b: Box) -> bool:
    """True when the two boxes share area."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def gap(a: Box, b: Box) -> tuple[float, float]:
    """Empty space between two boxes as (dy, dx); 0.0 on an axis where they overlap."""
    dy = max(0.0, a[0] - b[2], b[0] - a[2])
    dx = max(0.0, a[1] - b[3], b[1] - a[3])
    return dy, dx


def has_digit(text: str) -> bool:
    """True when `text` holds at least one ASCII digit."""
    return any("0" <= ch <= "9" for ch in text)


def digits_of(text: str) -> str:
    """Only the ASCII digits of `text`, in order."""
    return "".join(ch for ch in text if "0" <= ch <= "9")


def mean_confidence(tokens: Sequence[Token]) -> float:
    """Mean reader confidence of `tokens`; 0.0 for none."""
    return sum(t.confidence for t in tokens) / len(tokens) if tokens else 0.0
