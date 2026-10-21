"""Per-field value normalization (T8). Pure functions, standard library plus validators only.

Normalization changes representation, never content: spelling is never corrected and no letter
or digit is invented. An input with nothing left after normalization yields None ("not found").
"""

from __future__ import annotations

from datetime import date
import re
import unicodedata

from backend.core.validators import pad_id

# Structural: Hebrew final forms and their medial counterparts (the five "sofit" letters).
_TO_FINAL = {"כ": "ך", "מ": "ם", "נ": "ן", "פ": "ף", "צ": "ץ"}  # noqa: RUF001
_TO_MEDIAL = {v: k for k, v in _TO_FINAL.items()}

# Structural: a maximal run of Hebrew letters; final-form rules apply inside each run.
_HEBREW_RUN_RE = re.compile(r"[\u05d0-\u05ea]+")

# Structural: whitespace plus every dash-like character printed or OCR'd between digit groups.
_SPACE_DASH_RE = re.compile(r"[\s\-\u2010-\u2015\u2212\u05be]+")

# Structural: separators stripped from a central number; letters and digits are never touched.
_SEPARATOR_RE = re.compile(r"[\s\-\u2010-\u2015\u2212\u05be./\\,:;_|]+")


def _fix_run(run: str) -> str:
    """Medial form for every letter except the last, which takes its final form."""
    head = "".join(_TO_MEDIAL.get(c, c) for c in run[:-1])
    last = run[-1]
    return head + _TO_FINAL.get(last, last)


def normalize_id(raw: str) -> str | None:
    """Digits only, surplus leading zeros removed, left-padded to 9; None when impossible."""
    return pad_id(raw)


def normalize_name(raw: str) -> str | None:
    """Trim, collapse whitespace, normalize final letters per word. Spelling is never fixed."""
    text = " ".join(unicodedata.normalize("NFC", raw or "").split())
    if not text:
        return None
    return _HEBREW_RUN_RE.sub(lambda m: _fix_run(m.group()), text)


def normalize_commitment(raw: str) -> str | None:
    """Strip spaces and dashes; letters are preserved ('X 132-767' -> 'X132767')."""
    text = _SPACE_DASH_RE.sub("", raw or "")
    return text or None


def normalize_central(raw: str) -> str | None:
    """Strip separators only; letters and digits are preserved."""
    text = _SEPARATOR_RE.sub("", raw or "")
    return text or None


def format_date(d: date) -> str:
    """Display form DD/MM/YYYY."""
    return d.strftime("%d/%m/%Y")
