"""Read an explicit page marker ("עמוד 2 מתוך 3", "page 2 of 3") out of a page's text.

An explicit sequence is one of the three signals S10 uses to attach a continuation page to its
form (PLAN §4 S10). It is a statement printed on the page, not an inference, so it is read
verbatim and ignored when it is not plausible (N must be between 1 and M). Pure; never raises.
"""

from __future__ import annotations

import re
from typing import Final

# Structural: the words that introduce a page number on the forms ("עמוד", "דף") and in English,
# then "N", a connector ("מתוך", "of", "/") and "M". Matching is on printed words, not on the
# shape of a value, so it creates and rejects no field value (project rule §3).
_MARKER: Final = re.compile(
    r"(?:עמוד|דף|page|p\.)\s*(\d{1,3})\s*(?:מתוך|מ-|of|/)\s*(\d{1,3})",
    re.IGNORECASE,
)


def find_page_sequence(text: str) -> tuple[int, int] | None:
    """(N, M) of the first plausible "page N of M" marker in `text`, or None. N and M are
    pages (counts, not units); a marker with N < 1, M < 1 or N > M is not a page marker."""
    for match in _MARKER.finditer(text or ""):
        number, total = int(match.group(1)), int(match.group(2))
        if 1 <= number <= total:
            return number, total
    return None
