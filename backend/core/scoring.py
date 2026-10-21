"""S8 scoring primitives (PLAN §5, T14): signals, weights, combined score, evidence ladder.

Pure functions over small models; standard library plus pydantic only. Nothing here reads a
file or a clock. The reader's own confidence is one weak signal among seven (§5.1), never the
trigger for escalation, because Tesseract reports high confidence for handwriting it invented.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import Field, model_validator

from backend.core.models import REASON_TEXT, ConfidenceLevel, FieldKey, SourceKind, Strict
from backend.core.validators import CONFUSABLE

# Structural: s_agree takes exactly these three values (PLAN §5.1 table).
AGREE_IDENTICAL = 1.0
AGREE_CONFUSABLE = 0.5
AGREE_NONE = 0.0

# Structural: float noise tolerance. Scores are rounded so "exactly 0.80" is reproducible.
SCORE_DECIMALS = 6
WEIGHT_SUM_TOLERANCE = 1e-6

# Structural: the note S7 puts on a candidate from the independent second local read. It is what
# makes a second Tesseract pass at another psm count as a distinct reader. Equal to S7's note.
SECOND_READ_NOTE = "second_read"

# Structural: shown instead of an invented sender (project rule §2).
SENDER_NOT_LISTED = "לא ברשימה"

# Structural: PLAN §5.2 - these fields are green only at level A or B.
CRITICAL_FIELDS = frozenset(
    {
        FieldKey.national_id,
        FieldKey.central_number,
        FieldKey.receipt_date,
        FieldKey.commitment_number,
    }
)

# Structural: PLAN §5.2 level C needs a printed, screen or barcode source.
_C_SOURCES = frozenset({SourceKind.printed, SourceKind.screen, SourceKind.barcode})

# Reasons whose D result still shows the reading (flagged, editable) instead of an empty cell.
VALUE_KEPT_REASONS = frozenset({"handwriting_unverified", "low_quality_region"})


class ScoringWeights(Strict):
    """Weights of the seven soft signals. Must sum to 1.00 (PLAN §5.1)."""

    agree: float = Field(ge=0.0, le=1.0)
    source: float = Field(ge=0.0, le=1.0)
    label: float = Field(ge=0.0, le=1.0)
    margin: float = Field(ge=0.0, le=1.0)
    pattern: float = Field(ge=0.0, le=1.0)
    conf: float = Field(ge=0.0, le=1.0)
    image: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _sums_to_one(self) -> ScoringWeights:
        total = sum(self.as_tuple())
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"scoring weights must sum to 1.00, got {total:.6f}")
        return self

    def as_tuple(self) -> tuple[float, float, float, float, float, float, float]:
        return (
            self.agree,
            self.source,
            self.label,
            self.margin,
            self.pattern,
            self.conf,
            self.image,
        )


# Initial values from PLAN §5.1. eval/calibrate.py tunes them on the dev split and writes
# config/models.yaml, which is authoritative; a test keeps this equal to the file until then.
DEFAULT_WEIGHTS = ScoringWeights(
    agree=0.26, source=0.18, label=0.16, margin=0.14, pattern=0.10, conf=0.08, image=0.08
)


class Signals(Strict):
    """The eight signals for one candidate value, each in 0..1 (`s_hard` is 0 or 1)."""

    s_agree: float = Field(ge=0.0, le=1.0)
    s_source: float = Field(ge=0.0, le=1.0)
    s_label: float = Field(ge=0.0, le=1.0)
    s_margin: float = Field(ge=0.0, le=1.0)
    s_pattern: float = Field(ge=0.0, le=1.0)
    s_conf: float = Field(ge=0.0, le=1.0)
    s_image: float = Field(ge=0.0, le=1.0)
    s_hard: float = Field(ge=0.0, le=1.0)


class SourceScores(Strict):
    """s_source per source kind (PLAN §5.1)."""

    barcode: float = Field(default=1.0, ge=0.0, le=1.0)
    printed: float = Field(default=0.85, ge=0.0, le=1.0)
    screen: float = Field(default=0.80, ge=0.0, le=1.0)
    stamp: float = Field(default=0.60, ge=0.0, le=1.0)
    handwritten: float = Field(default=0.45, ge=0.0, le=1.0)
    unknown: float = Field(default=0.30, ge=0.0, le=1.0)

    def of(self, kind: SourceKind) -> float:
        return float(getattr(self, kind.value))


class ScoringConfig(Strict):
    """S8 knobs. `config/models.yaml: scoring` is authoritative; a test keeps these defaults
    equal to it. Every value here can be swept by eval/calibrate.py."""

    escalation_score_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    # ADR-015 is PROVISIONAL (Tesseract was absent when it was written): level A stays on until
    # the Docker/CI inventory run disproves barcode/print agreement. Then set this to false.
    level_a_enabled: bool = True
    source_scores: SourceScores = Field(default_factory=SourceScores)
    # s_label for a candidate with no governing label (a bare barcode, a stamp).
    label_unknown: float = Field(default=0.5, ge=0.0, le=1.0)
    # Combined-score gap to the runner-up at which s_margin reaches 1.0.
    margin_full_gap: float = Field(default=0.25, gt=0.0, le=1.0)
    # s_image when no PageMeasurement is supplied: neither rewards nor punishes strongly.
    image_unknown: float = Field(default=0.8, ge=0.0, le=1.0)
    # Laplacian variance at or above which a page counts as fully sharp. ASSUMPTION: uncalibrated.
    blur_sharp: float = Field(default=100.0, gt=0.0)


def scoring_from_mapping(
    section: Mapping[str, object],
) -> tuple[ScoringConfig, ScoringWeights]:
    """Build config and weights from the parsed `scoring:` block of models.yaml. A missing
    `weights` key yields DEFAULT_WEIGHTS; the `calibration` provenance key is ignored.
    Raises pydantic.ValidationError on an unknown key or an out-of-range value."""
    data = dict(section)
    raw_weights = data.pop("weights", None)
    data.pop("calibration", None)
    config = ScoringConfig.model_validate(data)
    weights = DEFAULT_WEIGHTS if raw_weights is None else ScoringWeights.model_validate(raw_weights)
    return config, weights


def combined_score(f: Signals, w: ScoringWeights) -> float:
    """Weighted score in 0..1. A failed hard rule returns 0.0: a national-standard failure is
    disqualifying, not a penalty. Never raises."""
    if f.s_hard == 0.0:
        return 0.0
    raw = (
        w.agree * f.s_agree
        + w.source * f.s_source
        + w.label * f.s_label
        + w.margin * f.s_margin
        + w.pattern * f.s_pattern
        + w.conf * f.s_conf
        + w.image * f.s_image
    )
    return round(min(1.0, max(0.0, raw)), SCORE_DECIMALS)


def differs_by_confusable_pair(a: str, b: str) -> bool:
    """True when `a` and `b` differ in exactly one position and that pair is confusable."""
    if len(a) != len(b):
        return False
    diffs = [(x, y) for x, y in zip(a, b, strict=True) if x != y]
    return len(diffs) == 1 and diffs[0][1] in CONFUSABLE.get(diffs[0][0], ())


def pick_winner(scores: Sequence[float], hard_ok: Sequence[bool]) -> int | None:
    """Index of the highest score among groups whose hard rules passed; if none passed, the
    highest score overall (reported, never accepted). Ties go to the earlier index so the choice
    is deterministic. None for an empty input."""
    if not scores:
        return None
    pool = [i for i, ok in enumerate(hard_ok) if ok] or list(range(len(scores)))
    return max(pool, key=lambda i: (scores[i], -i))


class Evidence(Strict):
    """Facts about the winning value that the ladder needs (PLAN §5.2)."""

    hard_passed: bool
    hard_reason: str | None = None  # REASON_TEXT key; required when hard_passed is False
    winner_readers: int = Field(ge=1)  # independent readers that produced the winning value
    rival_readers: int = Field(default=0, ge=0)  # most readers behind any passing rival value
    rival_other_reader: bool = False  # a passing rival came from a reader the winner lacks
    stamp_hand_conflict: bool = False  # the rival is stamp vs handwritten (receipt date)
    barcode_print_agree: bool = False  # a barcode and an independent printed read agree
    source: SourceKind
    repaired: bool = False  # a confusable-digit repair produced the value
    group_count: int = Field(default=1, ge=1)  # distinct passing values found

    @model_validator(mode="after")
    def _hard_failure_has_reason(self) -> Evidence:
        if not self.hard_passed and self.hard_reason not in REASON_TEXT:
            raise ValueError("a failed hard rule needs a reason from the closed set")
        return self


class Decision(Strict):
    """Ladder outcome. `gate == "fixed"`: level and reason do not depend on the score.
    `gate == "score"`: level C if the score reaches the threshold, else D with `low_reason`."""

    gate: Literal["fixed", "score"]
    level: ConfidenceLevel | None = None
    reason: str | None = None
    low_reason: str | None = None


def _fixed(level: ConfidenceLevel, reason: str | None = None) -> Decision:
    return Decision(gate="fixed", level=level, reason=reason)


def decide(ev: Evidence, cfg: ScoringConfig) -> Decision:
    """Walk the §5.2 ladder for everything that does not depend on the score. Never raises."""
    if not ev.hard_passed:
        return _fixed(ConfidenceLevel.D, ev.hard_reason)
    if ev.winner_readers >= 2:
        if ev.rival_readers >= 2:
            return _fixed(ConfidenceLevel.D, "multiple_candidates")
        agreed = (
            ConfidenceLevel.A
            if cfg.level_a_enabled and ev.barcode_print_agree
            else ConfidenceLevel.B
        )
        return _fixed(ConfidenceLevel.C if ev.repaired else agreed)
    if ev.rival_other_reader:
        reason = "stamp_and_handwritten_differ" if ev.stamp_hand_conflict else "readers_disagree"
        return _fixed(ConfidenceLevel.D, reason)
    if ev.source not in _C_SOURCES:
        return _fixed(ConfidenceLevel.D, "handwriting_unverified")
    low = "multiple_candidates" if ev.group_count > 1 else "low_quality_region"
    return Decision(gate="score", low_reason=low)


def assign_level(
    ev: Evidence, score: float, cfg: ScoringConfig
) -> tuple[ConfidenceLevel, str | None]:
    """(level, reason code). The reason is a REASON_TEXT key for level D and None otherwise.
    A score exactly at the threshold is C, not D. Never raises."""
    d = decide(ev, cfg)
    if d.gate == "fixed":
        assert d.level is not None
        return d.level, d.reason
    if score >= cfg.escalation_score_threshold:
        return ConfidenceLevel.C, None
    return ConfidenceLevel.D, d.low_reason


def reason_for(ev: Evidence, score: float, cfg: ScoringConfig) -> str | None:
    """REASON_TEXT key explaining a level-D result, None when the level is not D."""
    return assign_level(ev, score, cfg)[1]


def reason_text(code: str) -> str:
    """Hebrew text of a closed-set reason. Raises KeyError for a code outside the set, so a
    free-text reason can never be produced."""
    return REASON_TEXT[code]


def display_tone(level: ConfidenceLevel) -> Literal["green", "amber", "orange"]:
    """Colour family of a level (PLAN §5.2). Green only at A or B, so a critical field read once
    (level C) is amber. Colour is never the only channel; the UI adds icon and text."""
    if level in (ConfidenceLevel.A, ConfidenceLevel.B):
        return "green"
    if level is ConfidenceLevel.C:
        return "amber"
    return "orange"
