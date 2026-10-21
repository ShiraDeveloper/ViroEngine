"""Closed sender list: load config/senders.json and fuzzy-match a letterhead against it (T6).

Reads the committed file only. At runtime this module never touches the network.
"""

# ruff: noqa: RUF001  (Hebrew letters are not confusable text here)

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

from rapidfuzz import fuzz

from backend.core.models import SenderSpec

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "senders.json"

# Hebrew points and cantillation marks (U+0591-U+05C7) carry no identity for matching.
_NIQQUD = re.compile("[\u0591-\u05c7]")
# Quote-like marks are deleted, not spaced, so רמב"ם and רמבם become the same token.
_QUOTES = re.compile("[\"'`\u05f3\u05f4\u2018\u2019\u201c\u201d]")
_NON_WORD = re.compile(r"[^\w\s]|_")
# Structural: final letters fold to their base form, and ח/ת, the pair OCR swaps most often
# on printed Hebrew, fold together. Applied to both sides so it only adds typo tolerance.
_FOLD = str.maketrans(
    {"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ", "ח": "ת"},
)


@dataclass(frozen=True)
class SenderMatch:
    """A letterhead matched to one closed-list entry. `score` is 0-100 (token_set_ratio)."""

    canonical: str
    score: float
    matched: str
    spec: SenderSpec


def normalize_text(text: str) -> str:
    """Lower-case, drop points and quotes, fold final letters, collapse punctuation to spaces.
    Never raises; an empty or non-text input gives an empty string."""
    folded = unicodedata.normalize("NFKC", text).lower()
    folded = _QUOTES.sub("", _NIQQUD.sub("", folded)).translate(_FOLD)
    return " ".join(_NON_WORD.sub(" ", folded).split())


def _remove_phrases(text: str, phrases: list[str]) -> str:
    """Remove whole-token phrases, longest first, from already-normalised text."""
    for phrase in sorted(phrases, key=len, reverse=True):
        text = re.sub(rf"(?<!\S){re.escape(phrase)}(?!\S)", " ", text)
    return " ".join(text.split())


def _remove_tokens(text: str, tokens: frozenset[str]) -> str:
    return " ".join(t for t in text.split() if t not in tokens)


def _score(text: str, variant: str) -> float:
    """token_set_ratio (0-100), except that a text which is only part of a longer variant is
    scored by plain ratio: token_set_ratio gives such a partial name a perfect 100."""
    text_tokens, variant_tokens = set(text.split()), set(variant.split())
    if text_tokens < variant_tokens:
        return float(fuzz.ratio(text, variant))
    return float(fuzz.token_set_ratio(text, variant))


class SenderIndex:
    """Closed list of sending institutions. Build with `load()` or `from_dict()`."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.version: int = int(data["version"])
        self.source_url: str = str(data["source_url"])
        self.fetched_at: str = str(data["fetched_at"])
        self.min_ratio: int = int(data["match"]["min_ratio"])
        self.global_commitment_deny: tuple[str, ...] = tuple(data["global_commitment_deny"])
        self.destination_suppress: tuple[str, ...] = tuple(data["destination_suppress"])
        self._destination = [p for p in (normalize_text(s) for s in self.destination_suppress) if p]
        self._generic = frozenset(normalize_text(g) for g in data["generic_tokens"])
        self.sources: dict[str, str] = {}
        specs: list[SenderSpec] = []
        for raw in data["senders"]:
            entry = dict(raw)
            self.sources[str(entry["canonical"])] = str(entry.pop("source"))
            specs.append(SenderSpec.model_validate(entry))
        self.specs: tuple[SenderSpec, ...] = tuple(specs)
        self._variants: list[list[str]] = [self._variants_of(s) for s in self.specs]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SenderIndex:
        return cls(data)

    @classmethod
    def load(cls, path: Path | None = None) -> SenderIndex:
        """Read the committed JSON. Raises FileNotFoundError / ValueError when it is unusable."""
        target = path or DEFAULT_PATH
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{target} is not a senders document")
        return cls(data)

    def _variants_of(self, spec: SenderSpec) -> list[str]:
        out: list[str] = []
        for name in (spec.canonical, *spec.aliases, *spec.keywords):
            cleaned = _remove_tokens(normalize_text(name), self._generic)
            if cleaned and cleaned not in out:
                out.append(cleaned)
        return out

    def clean(self, text: str) -> str:
        """Letterhead text with destination phrases and generic words removed."""
        return _remove_tokens(
            _remove_phrases(normalize_text(text), self._destination), self._generic
        )

    def get(self, canonical: str) -> SenderSpec | None:
        return next((s for s in self.specs if s.canonical == canonical), None)

    def match(self, text: str, *, min_ratio: int | None = None) -> SenderMatch | None:
        """Fuzzy-match a letterhead (score 0-100) after removing destination phrases.
        Returns None below min_ratio or on an exact tie between institutions: never guess."""
        threshold = self.min_ratio if min_ratio is None else min_ratio
        cleaned = self.clean(text)
        if not cleaned:
            return None
        best: list[tuple[float, int, int, str]] = []  # score, variant length, index, variant
        for index, variants in enumerate(self._variants):
            top: tuple[float, int, str] | None = None
            for variant in variants:
                score = _score(cleaned, variant)
                if top is None or (score, len(variant)) > (top[0], top[1]):
                    top = (score, len(variant), variant)
            if top is not None and top[0] >= threshold:
                best.append((top[0], top[1], index, top[2]))
        if not best:
            return None
        best.sort(key=lambda b: (b[0], b[1]), reverse=True)
        if len(best) > 1 and best[0][:2] == best[1][:2]:
            return None
        score, _, index, variant = best[0]
        spec = self.specs[index]
        return SenderMatch(canonical=spec.canonical, score=score, matched=variant, spec=spec)
