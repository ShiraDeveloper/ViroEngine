"""Fuzzy comparison of OCR tokens with printed vocabulary (labels, captions, stamp words).

Standard library only, so pipeline stages in the core can match labels without an adapter.
The similarity is the Indel ratio on normalized text, 0..100 - the same scale as the
``*_fuzz`` thresholds in Settings.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re

# Structural: anything that is not a letter, a digit or whitespace is punctuation for matching
# purposes, so 'ת"ז', 'ת.ז' and 'ת ז' compare equal.
_PUNCT_RE = re.compile(r"[^\w\s]|_")
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lower-cased text with punctuation turned into single spaces, trimmed."""
    return _SPACE_RE.sub(" ", _PUNCT_RE.sub(" ", text.lower())).strip()


def similarity(token: str, phrase: str) -> float:
    """Similarity (0..100) of `token` to `phrase` after normalization; 0 when either is empty."""
    a, b = normalize(token), normalize(phrase)
    if not a or not b:
        return 0.0
    return 100.0 * SequenceMatcher(None, a, b, autojunk=False).ratio()


def best_similarity(token: str, phrases: tuple[str, ...]) -> float:
    """Highest similarity of `token` to any of `phrases`; 0 when there are none."""
    return max((similarity(token, p) for p in phrases), default=0.0)
