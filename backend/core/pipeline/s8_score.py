"""S8 - score every candidate value of one field and decide its evidence level (PLAN §5, T14).

Candidates are grouped by normalized value. Each group gets the seven soft signals and the hard
rules (Israeli ID check digit, calendar validity, not in the future); the best group wins, the
runner-up lowers the winner's `s_margin`, and the §5.2 ladder assigns A-D. A failed hard rule is
a gate, not a penalty: the field is level D with score 0.0 and no value.

Pure: the clock is injected, nothing is read or written, and the model never sets its own
confidence - this stage does. `score_field` is also what S9 re-runs after merging a cloud read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import re

from backend.core.models import (
    REASON_TEXT,
    Candidate,
    ConfidenceLevel,
    FieldKey,
    FieldResult,
    PageMeasurement,
    ReadingTrailStep,
    SenderSpec,
    SourceKind,
    Strict,
    ValidationOutcome,
)
from backend.core.normalize import (
    format_date,
    normalize_central,
    normalize_commitment,
    normalize_name,
)
from backend.core.ports.clock import ClockPort
from backend.core.scoring import (
    AGREE_CONFUSABLE,
    AGREE_IDENTICAL,
    AGREE_NONE,
    SECOND_READ_NOTE,
    SENDER_NOT_LISTED,
    THIRD_READ_NOTE,
    VALUE_KEPT_REASONS,
    Evidence,
    ScoringConfig,
    ScoringWeights,
    Signals,
    assign_level,
    combined_score,
    differs_by_confusable_pair,
    pick_winner,
    reason_text,
)
from backend.core.validators import (
    FIT_NEUTRAL,
    date_not_future,
    israeli_id_ok,
    pad_id,
    parse_il_date,
    pattern_fit,
    repair_confusable,
    strip_surplus_leading_zeros,
)

# Structural: s_margin is 1.0 when there is no competing value (PLAN §5.1).
_NO_RIVAL_MARGIN = 1.0


class FieldEvaluation(Strict):
    """A decided field plus what eval/calibrate.py needs to re-score it under other weights.
    `signals` and `evidence` are None when no candidate survived normalization."""

    result: FieldResult
    signals: Signals | None
    evidence: Evidence | None


@dataclass
class _Group:
    value: str  # normalized value; the grouping key
    candidates: list[Candidate] = field(default_factory=list)
    leading_zero_stripped: bool = False

    @property
    def sources(self) -> set[SourceKind]:
        return {c.source for c in self.candidates}

    @property
    def reader_keys(self) -> set[str]:
        return {_reader_key(c) for c in self.candidates}


@dataclass(frozen=True)
class _Hard:
    passed: bool
    reason: str | None
    value: str  # value after any confusable repair
    repaired_from: str | None
    outcomes: list[ValidationOutcome]


def _reader_key(c: Candidate) -> str:
    """Identity of an independent reading. A second Tesseract pass at another psm on a tight crop
    counts as its own reader (S7), a repeat of the page read does not."""
    if THIRD_READ_NOTE in c.notes:
        return f"{c.reader.value}:third"
    suffix = "second" if SECOND_READ_NOTE in c.notes else "first"
    return f"{c.reader.value}:{suffix}"


def _normalize(key: FieldKey, raw: str) -> tuple[str | None, bool]:
    """(normalized value or None when nothing is left, leading zero was stripped).
    Representation only: no letter or digit is ever invented."""
    if key is FieldKey.national_id:
        digits = re.sub(r"\D", "", raw)
        if not digits:
            return None, False
        stripped = strip_surplus_leading_zeros(digits)
        return pad_id(digits) or stripped, stripped != digits
    if key is FieldKey.receipt_date:
        parsed = parse_il_date(raw)
        text = format_date(parsed) if parsed is not None else " ".join(raw.split())
        return text or None, False
    if key is FieldKey.central_number:
        return normalize_central(raw), False
    if key is FieldKey.commitment_number:
        return normalize_commitment(raw), False
    if key in (FieldKey.first_name, FieldKey.last_name):
        return normalize_name(raw), False
    return " ".join(raw.split()) or None, False


def _group(key: FieldKey, cands: Sequence[Candidate]) -> list[_Group]:
    groups: dict[str, _Group] = {}
    for c in cands:
        value, stripped = _normalize(key, c.value)
        if value is None:
            continue
        g = groups.setdefault(value, _Group(value=value))
        g.candidates.append(c)
        g.leading_zero_stripped = g.leading_zero_stripped or stripped
    return list(groups.values())


def _hard(key: FieldKey, g: _Group, clock: ClockPort) -> _Hard:
    """Apply the only two national standards (PLAN §5.3). Anything else passes untouched."""
    outcomes: list[ValidationOutcome] = []
    if key is FieldKey.national_id:
        if g.leading_zero_stripped:
            outcomes.append(
                ValidationOutcome(name="leading_zero_stripped", kind="soft", passed=True)
            )
        fixed, status = repair_confusable(g.value, israeli_id_ok)
        if fixed is None:
            reason = "ambiguous_repair" if status == "ambiguous" else "failed_id_check"
            outcomes.append(
                ValidationOutcome(name="israeli_id_check", kind="hard", passed=False, detail=status)
            )
            return _Hard(False, reason, g.value, None, outcomes)
        outcomes.append(ValidationOutcome(name="israeli_id_check", kind="hard", passed=True))
        if fixed != g.value:
            outcomes.append(
                ValidationOutcome(
                    name="confusable_repair",
                    kind="soft",
                    passed=True,
                    repaired_from=g.value,
                )
            )
            return _Hard(True, None, fixed, g.value, outcomes)
        return _Hard(True, None, g.value, None, outcomes)
    if key is FieldKey.receipt_date:
        parsed = parse_il_date(g.value)
        if parsed is None:
            outcomes.append(ValidationOutcome(name="calendar_valid", kind="hard", passed=False))
            return _Hard(False, "date_impossible", g.value, None, outcomes)
        outcomes.append(ValidationOutcome(name="calendar_valid", kind="hard", passed=True))
        if not date_not_future(parsed, now=clock.now().date()):
            outcomes.append(ValidationOutcome(name="not_future", kind="hard", passed=False))
            return _Hard(False, "date_future", g.value, None, outcomes)
        outcomes.append(ValidationOutcome(name="not_future", kind="hard", passed=True))
    return _Hard(True, None, g.value, None, outcomes)


def _clamp(x: float) -> float:
    return min(1.0, max(0.0, x))


def _s_image(m: PageMeasurement | None, cfg: ScoringConfig) -> float:
    """Sharpness (Laplacian variance vs `blur_sharp`) discounted by noise. Neutral when unknown."""
    if m is None:
        return cfg.image_unknown
    return _clamp(min(1.0, m.blur_score / cfg.blur_sharp) * (1.0 - _clamp(m.noise_score)))


def _signals(
    key: FieldKey,
    g: _Group,
    others: Sequence[_Group],
    *,
    hard_passed: bool,
    margin: float,
    spec: SenderSpec | None,
    image: float,
    cfg: ScoringConfig,
) -> Signals:
    keys = g.reader_keys
    if len(keys) >= 2:
        agree = AGREE_IDENTICAL
    elif any(
        (o.reader_keys - keys) and differs_by_confusable_pair(g.value, o.value) for o in others
    ):
        agree = AGREE_CONFUSABLE
    else:
        agree = AGREE_NONE
    label = max(
        (
            cfg.label_unknown if c.label_distance is None else _clamp(1.0 - c.label_distance)
            for c in g.candidates
        ),
        default=cfg.label_unknown,
    )
    hints = spec.pattern_hints if spec is not None and key is FieldKey.commitment_number else []
    return Signals(
        s_agree=agree,
        s_source=max(cfg.source_scores.of(s) for s in g.sources),
        s_label=label,
        s_margin=margin,
        s_pattern=pattern_fit(g.value, hints) if hints else FIT_NEUTRAL,
        s_conf=_clamp(max(c.reader_confidence for c in g.candidates)),
        s_image=image,
        s_hard=1.0 if hard_passed else 0.0,
    )


def _best_source(g: _Group, cfg: ScoringConfig) -> SourceKind:
    return max(g.sources, key=lambda s: (cfg.source_scores.of(s), s.value))


def _best_candidate(g: _Group) -> Candidate:
    boxed = [c for c in g.candidates if c.box is not None] or g.candidates
    return max(boxed, key=lambda c: c.reader_confidence)


def _empty(
    key: FieldKey, cands: Sequence[Candidate], trail: Sequence[ReadingTrailStep]
) -> FieldEvaluation:
    code = "sender_not_in_list" if key is FieldKey.sender else "not_found"
    result = FieldResult(
        field=key,
        value=None,
        display_value=SENDER_NOT_LISTED if key is FieldKey.sender else None,
        level=ConfidenceLevel.D,
        score=0.0,
        source=SourceKind.unknown,
        box=None,
        candidates=list(cands),
        validations=[],
        trail=list(trail),
        reason=reason_text(code),
        escalated=any(s.cloud for s in trail),
    )
    return FieldEvaluation(result=result, signals=None, evidence=None)


def evaluate_field(
    key: FieldKey,
    cands: Sequence[Candidate],
    *,
    spec: SenderSpec | None,
    clock: ClockPort,
    weights: ScoringWeights,
    config: ScoringConfig | None = None,
    measurement: PageMeasurement | None = None,
    trail: Sequence[ReadingTrailStep] = (),
) -> FieldEvaluation:
    """Decide one field and keep the winning signals for calibration. Never raises on any
    candidate list; an empty or unusable one is level D `not_found` (`sender_not_in_list` for the
    sender). `trail` is the accumulated reading history of the field."""
    cfg = config or ScoringConfig()
    groups = _group(key, cands)
    if not groups:
        return _empty(key, cands, trail)

    image = _s_image(measurement, cfg)
    hards = [_hard(key, g, clock) for g in groups]
    prelim = [
        combined_score(
            _signals(
                key,
                g,
                [o for o in groups if o is not g],
                hard_passed=h.passed,
                margin=_NO_RIVAL_MARGIN,
                spec=spec,
                image=image,
                cfg=cfg,
            ),
            weights,
        )
        for g, h in zip(groups, hards, strict=True)
    ]
    idx = pick_winner(prelim, [h.passed for h in hards])
    assert idx is not None
    win, hard = groups[idx], hards[idx]
    rival_idx = [i for i, h in enumerate(hards) if i != idx and h.passed]
    rivals = [groups[i] for i in rival_idx]

    margin = _NO_RIVAL_MARGIN
    if rivals and hard.passed:
        runner_up = max(prelim[i] for i in rival_idx)
        margin = _clamp((prelim[idx] - runner_up) / cfg.margin_full_gap)

    signals = _signals(
        key,
        win,
        [o for o in groups if o is not win],
        hard_passed=hard.passed,
        margin=margin,
        spec=spec,
        image=image,
        cfg=cfg,
    )
    score = combined_score(signals, weights)

    keys = win.reader_keys
    non_barcode = win.sources - {SourceKind.barcode}
    stamp_vs_hand = {SourceKind.stamp, SourceKind.handwritten} <= (
        win.sources.union(*(r.sources for r in rivals))
    )
    evidence = Evidence(
        hard_passed=hard.passed,
        hard_reason=hard.reason,
        winner_readers=len(keys),
        rival_readers=max((len(r.reader_keys) for r in rivals), default=0),
        rival_other_reader=any(r.reader_keys - keys for r in rivals),
        stamp_hand_conflict=key is FieldKey.receipt_date and stamp_vs_hand,
        barcode_print_agree=SourceKind.barcode in win.sources
        and bool(non_barcode & {SourceKind.printed, SourceKind.screen}),
        source=_best_source(win, cfg),
        repaired=hard.repaired_from is not None,
        group_count=1 + len(rivals),
    )
    level, code = assign_level(evidence, score, cfg)

    keep_value = level is not ConfidenceLevel.D or code in VALUE_KEPT_REASONS
    value = hard.value if keep_value else None
    best = _best_candidate(win)
    result = FieldResult(
        field=key,
        value=value,
        display_value=value,
        level=level,
        score=score,
        source=evidence.source,
        box=best.box,
        candidates=list(cands),
        validations=hard.outcomes,
        trail=list(trail),
        reason=REASON_TEXT[code] if code is not None else None,
        escalated=any(s.cloud for s in trail),
    )
    return FieldEvaluation(result=result, signals=signals, evidence=evidence)


def score_field(
    key: FieldKey,
    cands: Sequence[Candidate],
    *,
    spec: SenderSpec | None,
    clock: ClockPort,
    weights: ScoringWeights,
    config: ScoringConfig | None = None,
    measurement: PageMeasurement | None = None,
    trail: Sequence[ReadingTrailStep] = (),
) -> FieldResult:
    """Turn a candidate list into a decided FieldResult (score, level A-D, closed-set reason).
    See `evaluate_field`; this returns only the result."""
    return evaluate_field(
        key,
        cands,
        spec=spec,
        clock=clock,
        weights=weights,
        config=config,
        measurement=measurement,
        trail=trail,
    ).result


__all__ = ["FieldEvaluation", "evaluate_field", "score_field"]
