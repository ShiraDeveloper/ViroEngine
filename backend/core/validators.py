"""Hard validations and soft scoring features (T8).

Rule: ONLY genuine national standards are hard rules (Israeli ID check digit, calendar dates).
Everything else returns a score that nudges confidence and never rejects a value.
Pure functions, standard library only, no I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from itertools import combinations, product
import re

# Structural: an Israeli national ID is exactly 9 digits including its check digit.
ID_LENGTH = 9

# Structural: the check digit weights alternate 1, 2 from the left (national standard).
_ID_WEIGHTS = (1, 2)

# Structural: the receipt stamp and forms print day-month-year with one of these separators.
_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[-./](\d{1,2})[-./](\d{4}|\d{2})(?!\d)")

# Structural: the six digit pairs that handwriting and fax OCR swap most often (PLAN §5.4).
CONFUSABLE: dict[str, tuple[str, ...]] = {
    "1": ("7",),
    "7": ("1", "2"),
    "4": ("9",),
    "9": ("4",),
    "0": ("6",),
    "6": ("0", "5"),
    "5": ("6",),
    "3": ("8",),
    "8": ("3",),
    "2": ("7",),
}

# Soft-score levels for pattern_fit. They are ordinal signal values fed to S8, which owns the
# weights; they are not pass/fail thresholds, so they stay here rather than in Settings.
FIT_NEUTRAL = 0.5  # no hints: an unknown institution is not penalised
FIT_REGEX = 1.0  # a hint regex matches the whole value
FIT_SHAPE = 0.75  # only the abstract shape matches a hint
FIT_MISS = 0.25  # hints exist and none match


# --- HARD RULE 1: Israeli national ID check digit ---------------------------------------------


def strip_surplus_leading_zeros(digits: str) -> str:
    """Remove leading zeros only while the digit string is longer than 9. Never raises.

    The samples appear to contain '0011223344': ten digits for a nine-digit ID, where the
    surplus is padding, not data (VERIFY against the source, T5/T7). A plain `len > 9 -> reject`
    would discard a valid, correctly-read ID. Stops at 9 so a genuine leading zero is preserved.
    """
    while len(digits) > ID_LENGTH and digits.startswith("0"):
        digits = digits[1:]
    return digits


def israeli_id_ok(raw: str) -> bool:
    """True when the ID passes the check digit after de-padding and left-padding to 9 digits.

    Length alone never rejects an ID: '11223344' and '1122334' lost a leading zero, and
    '0011223344' gained one. A value still longer than 9 after de-padding is genuinely not an ID.
    Never raises; empty or non-numeric input is False.
    """
    digits = strip_surplus_leading_zeros(re.sub(r"\D", "", raw or ""))
    if not digits or len(digits) > ID_LENGTH:
        return False  # surplus is real digits, not padding
    digits = digits.zfill(ID_LENGTH)
    total = 0
    for i, ch in enumerate(digits):
        v = int(ch) * _ID_WEIGHTS[i % 2]
        total += v - 9 if v > 9 else v
    return total % 10 == 0


def pad_id(raw: str) -> str | None:
    """Normalize to exactly 9 digits, or None when that is impossible. Never raises."""
    digits = strip_surplus_leading_zeros(re.sub(r"\D", "", raw or ""))
    return digits.zfill(ID_LENGTH) if digits and len(digits) <= ID_LENGTH else None


# --- HARD RULE 2: the date exists and is not in the future ------------------------------------


def parse_il_date(raw: str, *, pivot_year: int = 2000) -> date | None:
    """Parse a DD/MM/YYYY date (calendar days, months, years); None when it is not a real date.

    Whitespace is collapsed FIRST because the receipt stamp prints gapped digits:
    '1 8 -06- 2026' -> '18-06-2026'. Two-digit years map to pivot_year + yy. Never raises.
    """
    if not raw:
        return None
    m = _DATE_RE.search(re.sub(r"\s+", "", raw))
    if not m:
        return None
    day, month, year = int(m[1]), int(m[2]), int(m[3])
    if year < 100:
        year += pivot_year
    try:
        return date(year, month, day)
    except ValueError:  # 31/02 and similar
        return None


def date_not_future(d: date, *, now: date) -> bool:
    """True when `d` is on or before `now`. `now` comes from ClockPort so tests are deterministic."""
    return d <= now


# --- Confusable-digit repair: accepted ONLY when unambiguous ----------------------------------


def repair_confusable(
    read: str, ok: Callable[[str], bool], *, max_swaps: int = 1
) -> tuple[str | None, str]:
    """Return (value, status), status in {'as_read','repaired_1','ambiguous','unrepairable'}.

    Accepts a repair ONLY when exactly one variant validates; several means we cannot know which
    is right, so a human decides. Capped at one swap: two-swap repair produced more wrong
    corrections than right ones in simulation (PLAN §5.4). The caller caps the level at C.
    Never raises.

    >>> repair_confusable("011223394", israeli_id_ok)
    ('011223344', 'repaired_1')
    """
    if ok(read):
        return read, "as_read"
    for k in range(1, max_swaps + 1):
        found: set[str] = set()
        for positions in combinations(range(len(read)), k):
            if not all(read[p] in CONFUSABLE for p in positions):
                continue
            for choice in product(*(CONFUSABLE[read[p]] for p in positions)):
                cand = list(read)
                for p, ch in zip(positions, choice, strict=True):
                    cand[p] = ch
                joined = "".join(cand)
                if ok(joined):
                    found.add(joined)
        if len(found) == 1:
            return found.pop(), f"repaired_{k}"
        if len(found) > 1:
            return None, "ambiguous"
    return None, "unrepairable"


# --- SOFT features: these only move the score -------------------------------------------------


def shape_of(value: str) -> str:
    """Abstract a value to its shape: digits -> '#', letters -> 'A', other characters kept.

    Digits are discarded here, which is what lets the learning store persist a pattern without
    a value. Never raises.

    >>> shape_of("X132767"), shape_of("011223344")
    ('A######', '#########')
    """
    return "".join("#" if c.isdigit() else ("A" if c.isalpha() else c) for c in value)


def pattern_fit(value: str, hints: Sequence[str]) -> float:
    """Soft signal in 0..1. NEVER rejects; a bad hint regex is skipped. Never raises.

    FIT_NEUTRAL with no hints, FIT_REGEX on a whole-value regex hit, FIT_SHAPE when only the
    abstract shape matches a hint, FIT_MISS when hints exist and none match.
    """
    if not hints:
        return FIT_NEUTRAL
    for h in hints:
        try:
            if re.fullmatch(h, value):
                return FIT_REGEX
        except re.error:
            continue
    shape = shape_of(value)
    for h in hints:
        probe = re.sub(r"\\d", "#", h).strip("^$")
        if probe and probe == shape:
            return FIT_SHAPE
    return FIT_MISS
