"""Reading order of OCR words: lines top to bottom, right to left inside Hebrew lines.

Shared by S3 (which builds the text stream) and the region finders (which match phrases that
span several words along a line).
"""

from __future__ import annotations

from collections.abc import Sequence
import statistics
from typing import Final

from backend.core.geometry import center
from backend.core.models import OcrWord

_ALEF: Final = "\u05d0"
_TAV: Final = "\u05ea"


def has_hebrew(text: str) -> bool:
    """True when `text` holds at least one Hebrew letter."""
    return any(_ALEF <= ch <= _TAV for ch in text)


def group_lines(words: Sequence[OcrWord], *, tolerance: float) -> list[list[OcrWord]]:
    """Lines of `words` in reading order.

    Words share a line while their vertical centres stay within a window of `tolerance` x the
    median word height around the line's mean centre (a window, not a radius: ±tolerance/2).
    Lines are ordered top to bottom; inside a line the order is right to left when Hebrew
    letters outnumber Latin ones, left to right otherwise.
    """
    if not words:
        return []
    median_height = statistics.median(w.box[2] - w.box[0] for w in words)
    half_window = tolerance * median_height / 2.0

    lines: list[list[OcrWord]] = []
    centres: list[float] = []  # running mean centre of each line
    for word in sorted(words, key=lambda w: center(w.box)[0]):
        cy = center(word.box)[0]
        if lines and abs(cy - centres[-1]) <= half_window:
            lines[-1].append(word)
            centres[-1] = sum(center(w.box)[0] for w in lines[-1]) / len(lines[-1])
        else:
            lines.append([word])
            centres.append(cy)

    for line in lines:
        line.sort(key=lambda w: w.box[1], reverse=_is_rtl(line))
    return lines


def _is_rtl(line: Sequence[OcrWord]) -> bool:
    """True when the line has more Hebrew letters than Latin ones."""
    hebrew = latin = 0
    for word in line:
        for ch in word.text:
            if _ALEF <= ch <= _TAV:
                hebrew += 1
            elif ch.isascii() and ch.isalpha():
                latin += 1
    return hebrew > latin
