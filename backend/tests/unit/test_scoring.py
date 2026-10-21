from __future__ import annotations

from itertools import product
from pathlib import Path

from pydantic import ValidationError
import pytest
import yaml  # type: ignore[import-untyped]

from backend.adapters.fakes import FixedClock
from backend.core.models import (
    REASON_TEXT,
    Candidate,
    ConfidenceLevel,
    FieldKey,
    FieldResult,
    ReaderName,
    ReadingTrailStep,
    RegionKind,
    SourceKind,
)
from backend.core.pipeline import s7_second_read
from backend.core.pipeline.s8_score import evaluate_field, score_field
from backend.core.scoring import (
    CRITICAL_FIELDS,
    DEFAULT_WEIGHTS,
    SECOND_READ_NOTE,
    SENDER_NOT_LISTED,
    Evidence,
    ScoringConfig,
    ScoringWeights,
    Signals,
    assign_level,
    combined_score,
    display_tone,
    pick_winner,
    reason_text,
    scoring_from_mapping,
)
from eval.calibrate import HeldOutRefused, calibrate

MODELS_YAML = Path(__file__).resolve().parents[3] / "config" / "models.yaml"
CLOCK = FixedClock()  # 2026-10-18
VALID_ID = "011223344"
CFG = ScoringConfig()


def _scoring_section() -> dict[str, object]:
    section: dict[str, object] = yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8"))["scoring"]
    return section


LEVEL_A_ENABLED = scoring_from_mapping(_scoring_section())[0].level_a_enabled


def cand(
    value: str,
    reader: ReaderName = ReaderName.tesseract,
    source: SourceKind = SourceKind.printed,
    *,
    conf: float = 0.9,
    label_distance: float | None = 0.05,
    notes: list[str] | None = None,
) -> Candidate:
    return Candidate(
        value=value,
        reader=reader,
        source=source,
        box=(0.1, 0.1, 0.2, 0.3),
        reader_confidence=conf,
        label_distance=label_distance,
        region=RegionKind.patient_box,
        notes=notes or [],
    )


def score(key: FieldKey, cands: list[Candidate], **kw: object) -> FieldResult:
    return score_field(key, cands, spec=None, clock=CLOCK, weights=DEFAULT_WEIGHTS, **kw)  # type: ignore[arg-type]


def evidence(**kw: object) -> Evidence:
    base: dict[str, object] = {
        "hard_passed": True,
        "winner_readers": 1,
        "source": SourceKind.printed,
    }
    return Evidence.model_validate({**base, **kw})


# --- the 13 tests required by PLAN T14 -----------------------------------------------------


def test_hard_fail_forces_level_d() -> None:
    perfect = [
        cand("011228844", ReaderName.tesseract, conf=1.0, label_distance=0.0),
        cand("011228844", ReaderName.trocr, conf=1.0, label_distance=0.0),
    ]
    result = score(FieldKey.national_id, perfect)
    assert result.level is ConfidenceLevel.D
    assert result.score == 0.0
    assert result.value is None
    assert result.reason == REASON_TEXT["failed_id_check"]


@pytest.mark.skipif(not LEVEL_A_ENABLED, reason="ADR-015 disabled level A")
def test_barcode_print_agreement_is_level_a() -> None:
    result = score(
        FieldKey.central_number,
        [
            cand("4417528", ReaderName.zxing, SourceKind.barcode, conf=0.95),
            cand("4417528", ReaderName.tesseract, SourceKind.printed),
        ],
    )
    assert result.level is ConfidenceLevel.A
    assert result.value == "4417528"


def test_two_readers_agree_is_level_b() -> None:
    result = score(
        FieldKey.national_id,
        [cand(VALID_ID, ReaderName.tesseract), cand(VALID_ID, ReaderName.trocr)],
    )
    assert result.level is ConfidenceLevel.B
    assert result.value == VALID_ID
    assert result.reason is None


def test_single_reader_printed_above_threshold_is_level_c() -> None:
    cfg = ScoringConfig(escalation_score_threshold=0.60)
    result = score(
        FieldKey.commitment_number,
        [cand("X132767", conf=1.0, label_distance=0.0)],
        config=cfg,
    )
    assert result.score >= 0.60
    assert result.level is ConfidenceLevel.C
    assert result.value == "X132767"


def test_disagreement_is_level_d_with_reason() -> None:
    result = score(
        FieldKey.central_number,
        [cand("4417528", ReaderName.tesseract), cand("4417529", ReaderName.trocr)],
    )
    assert result.level is ConfidenceLevel.D
    assert result.reason == REASON_TEXT["readers_disagree"]
    assert result.value is None  # never guess between two readings
    assert len(result.candidates) == 2  # but the reviewer still sees both


def test_handwriting_without_second_reader_is_level_d() -> None:
    result = score(
        FieldKey.central_number,
        [cand("4417528", ReaderName.trocr, SourceKind.handwritten, conf=0.99)],
    )
    assert result.level is ConfidenceLevel.D
    assert result.reason == REASON_TEXT["handwriting_unverified"]
    assert result.value == "4417528"  # shown, flagged, editable


def test_critical_field_at_c_is_not_green() -> None:
    assert CRITICAL_FIELDS == {
        FieldKey.national_id,
        FieldKey.central_number,
        FieldKey.receipt_date,
        FieldKey.commitment_number,
    }
    assert display_tone(ConfidenceLevel.C) != "green"
    assert display_tone(ConfidenceLevel.B) == "green"
    assert display_tone(ConfidenceLevel.D) == "orange"


@pytest.mark.parametrize(
    ("key", "code"),
    [(FieldKey.central_number, "not_found"), (FieldKey.sender, "sender_not_in_list")],
)
def test_no_candidates_is_level_d_not_found(key: FieldKey, code: str) -> None:
    result = score(key, [])
    assert result.level is ConfidenceLevel.D
    assert result.value is None
    assert result.reason == REASON_TEXT[code]
    assert result.display_value == (SENDER_NOT_LISTED if key is FieldKey.sender else None)


def test_margin_lowers_score_when_two_close_candidates() -> None:
    alone = score(FieldKey.central_number, [cand("4417528", conf=0.90)])
    crowded = score(
        FieldKey.central_number, [cand("4417528", conf=0.90), cand("5528331", conf=0.89)]
    )
    assert crowded.score < alone.score


def test_weights_sum_to_one() -> None:
    assert sum(DEFAULT_WEIGHTS.as_tuple()) == pytest.approx(1.0)
    with pytest.raises(ValidationError):
        ScoringWeights(agree=0.5, source=0.5, label=0.1, margin=0, pattern=0, conf=0, image=0)


def test_boundary_exactly_at_threshold() -> None:
    ev = evidence()
    assert assign_level(ev, 0.80, CFG)[0] is ConfidenceLevel.C
    level, reason = assign_level(ev, 0.7999, CFG)
    assert level is ConfidenceLevel.D
    assert reason in REASON_TEXT


def test_every_level_d_has_a_reason_from_the_closed_set() -> None:
    hard_reasons = [None, "failed_id_check", "ambiguous_repair", "date_impossible", "date_future"]
    for hard, readers, rival, other, stamp, bp, src, rep, groups, sc in product(
        hard_reasons,
        (1, 2),
        (0, 1, 2),
        (False, True),
        (False, True),
        (False, True),
        list(SourceKind),
        (False, True),
        (1, 3),
        (0.0, 0.5, 0.8, 1.0),
    ):
        ev = Evidence(
            hard_passed=hard is None,
            hard_reason=hard,
            winner_readers=readers,
            rival_readers=rival,
            rival_other_reader=other,
            stamp_hand_conflict=stamp,
            barcode_print_agree=bp,
            source=src,
            repaired=rep,
            group_count=groups,
        )
        level, reason = assign_level(ev, sc, CFG)
        if level is ConfidenceLevel.D:
            assert reason in REASON_TEXT
            assert reason_text(reason) in REASON_TEXT.values()
        else:
            assert reason is None


def test_calibrate_refuses_held_out(tmp_path: Path) -> None:
    with pytest.raises(HeldOutRefused):
        calibrate(tmp_path / "gold.csv", tmp_path / "signals.jsonl", split="held_out")  # type: ignore[arg-type]


# --- behaviour beyond the 13 --------------------------------------------------------------


def test_single_reader_cannot_reach_default_threshold() -> None:
    """With the PLAN §5.1 initial weights s_agree is 0 for one reader, so even perfect remaining
    signals total 0.74. Level C needs calibration (or a lower threshold) to exist at all."""
    best = Signals(
        s_agree=0.0, s_source=1.0, s_label=1.0, s_margin=1.0, s_pattern=1.0,
        s_conf=1.0, s_image=1.0, s_hard=1.0,
    )  # fmt: skip
    assert combined_score(best, DEFAULT_WEIGHTS) == pytest.approx(0.74)
    assert combined_score(best, DEFAULT_WEIGHTS) < CFG.escalation_score_threshold


def test_hard_fail_gate_is_zero_not_a_penalty() -> None:
    perfect = Signals(
        s_agree=1.0, s_source=1.0, s_label=1.0, s_margin=1.0, s_pattern=1.0,
        s_conf=1.0, s_image=1.0, s_hard=0.0,
    )  # fmt: skip
    assert combined_score(perfect, DEFAULT_WEIGHTS) == 0.0


def test_repair_is_accepted_when_unique_and_capped_at_c() -> None:
    result = score(
        FieldKey.national_id,
        [cand("011223394", ReaderName.tesseract), cand("011223394", ReaderName.trocr)],
    )
    assert result.level is ConfidenceLevel.C
    assert result.value == "011223344"
    repair = next(v for v in result.validations if v.name == "confusable_repair")
    assert repair.repaired_from == "011223394"


def test_ambiguous_repair_is_level_d() -> None:
    result = score(
        FieldKey.national_id,
        [cand("611178002", ReaderName.tesseract), cand("611178002", ReaderName.trocr)],
    )
    assert result.level is ConfidenceLevel.D
    assert result.reason == REASON_TEXT["ambiguous_repair"]
    assert result.value is None


def test_valid_id_beats_failing_decoy() -> None:
    result = score(
        FieldKey.national_id,
        [cand("011228844", conf=0.99), cand(VALID_ID, ReaderName.trocr, conf=0.5)],
    )
    assert result.value == VALID_ID  # the failed check is a gate, not an outvoted opinion
    assert result.score > 0.0


def test_leading_zero_is_stripped_and_recorded() -> None:
    result = score(
        FieldKey.national_id,
        [cand("0011223344", ReaderName.tesseract), cand("0011223344", ReaderName.trocr)],
    )
    assert result.value == VALID_ID
    assert result.level is ConfidenceLevel.B
    assert "leading_zero_stripped" in {v.name for v in result.validations}


@pytest.mark.parametrize(
    ("raw", "code"),
    [("31/02/2026", "date_impossible"), ("19/10/2026", "date_future")],
)
def test_bad_receipt_date_is_level_d(raw: str, code: str) -> None:
    result = score(
        FieldKey.receipt_date,
        [cand(raw, ReaderName.tesseract, SourceKind.stamp), cand(raw, ReaderName.trocr)],
    )
    assert result.level is ConfidenceLevel.D
    assert result.reason == REASON_TEXT[code]


def test_valid_receipt_date_is_normalized() -> None:
    result = score(
        FieldKey.receipt_date,
        [cand("1 8 -06- 2026", ReaderName.tesseract), cand("18/06/26", ReaderName.trocr)],
    )
    assert result.value == "18/06/2026"
    assert result.level is ConfidenceLevel.B


def test_stamp_versus_handwritten_date_has_its_own_reason() -> None:
    result = score(
        FieldKey.receipt_date,
        [
            cand("18/06/2026", ReaderName.tesseract, SourceKind.stamp),
            cand("19/06/2026", ReaderName.trocr, SourceKind.handwritten),
        ],
    )
    assert result.level is ConfidenceLevel.D
    assert result.reason == REASON_TEXT["stamp_and_handwritten_differ"]


def test_second_local_read_counts_as_an_independent_reader() -> None:
    result = score(
        FieldKey.commitment_number,
        [
            cand("X132767"),
            cand("X132767", notes=[SECOND_READ_NOTE]),
        ],
    )
    assert result.level is ConfidenceLevel.B


def test_second_read_note_equals_stage_s7() -> None:
    assert s7_second_read._SECOND_READ_NOTE == SECOND_READ_NOTE


def test_confusable_disagreement_scores_half_agreement() -> None:
    evaluation = evaluate_field(
        FieldKey.central_number,
        [cand("4417528", ReaderName.tesseract), cand("4417578", ReaderName.trocr)],
        spec=None,
        clock=CLOCK,
        weights=DEFAULT_WEIGHTS,
    )
    assert evaluation.signals is not None
    assert evaluation.signals.s_agree == 0.5
    assert evaluation.result.level is ConfidenceLevel.D


def test_pick_winner_prefers_passing_group_and_is_deterministic() -> None:
    assert pick_winner([], []) is None
    assert pick_winner([0.9, 0.5], [False, True]) == 1
    assert pick_winner([0.7, 0.7], [True, True]) == 0
    assert pick_winner([0.9, 0.5], [False, False]) == 0


def test_escalated_flag_follows_the_trail() -> None:
    step = ReadingTrailStep(
        stage="S9",
        reader=ReaderName.gemini,
        outcome="value",
        value="4417528",
        reader_confidence=None,
        duration_ms=10,
        cloud=True,
        tier=1,
    )
    result = score(FieldKey.central_number, [cand("4417528")], trail=[step])
    assert result.escalated
    assert result.trail == [step]


def test_models_yaml_matches_defaults_apart_from_calibrated_block() -> None:
    config, weights = scoring_from_mapping(_scoring_section())
    uncalibrated = config.model_copy(
        update={"escalation_score_threshold": CFG.escalation_score_threshold}
    )
    assert uncalibrated == CFG
    assert sum(weights.as_tuple()) == pytest.approx(1.0)
