"""Tests for S5 sender and page-role classification (T10).

test_destination_never_returned                 - no DESTINATION_TOKENS phrase becomes the sender
test_sap_screen_sender_from_goram_mafne         - a letterhead-less SAP screen resolves from the field
test_sap_goram_mafne_wins_over_mevate_ach       - disagreement: referrer wins and is flagged
test_sap_without_goram_mafne_falls_through      - field absent: the normal steps run
test_matches_wolfson_letterhead                 - letterhead region resolves by text
test_alias_shamir_resolves_to_asaf_harofe       - an alias resolves to the canonical name
test_typo_tolerance_klalich                     - one OCR-swapped letter still resolves
test_below_threshold_returns_none               - unknown letterhead: None, flagged, never guessed
test_model_answer_outside_closed_list_is_discarded - the model may pick, never invent
test_role_sap_screen_detected / _blank_ / _continuation_ - page roles
"""

# ruff: noqa: RUF001  (Hebrew letters are not confusable text here)

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from backend.adapters.senders.loader import SenderIndex
from backend.core.errors import ProviderExhausted
from backend.core.models import (
    Box,
    ImageRef,
    NormalizedPage,
    OcrResult,
    OcrWord,
    PageMeasurement,
    PageRaw,
    ProviderHealth,
    ReaderName,
    Region,
    RegionKind,
    VisionRequest,
    VisionResponse,
)
from backend.core.pipeline.s5_classify import (
    DESTINATION_TOKENS,
    NOT_RECOGNISED,
    Classification,
    ClassifyConfig,
    classify,
    suppress_destination,
)
from backend.core.ports.logo import LogoHit

CONFIG = ClassifyConfig()
WORD_W = 0.05
LINE_H = 0.02
FILLER = "טקסט " * 16  # sixteen words: enough that the page is not blank


@pytest.fixture(scope="module")
def index() -> SenderIndex:
    return SenderIndex.load()


def _ref(kind: str = "color") -> ImageRef:
    return ImageRef(
        job_id="job", page_id="p1", kind=kind, path=f"job/p1/{kind}",  # type: ignore[arg-type]
        width=100, height=100, sha256="0" * 64,
    )  # fmt: skip


def make_page(*, rotation: int = 0, corrections: list[str] | None = None) -> NormalizedPage:
    measurement = PageMeasurement(
        rotation_deg=rotation,  # type: ignore[arg-type]
        rotation_votes={}, skew_deg=0.0, blur_score=100.0, illumination_cv=0.1,
        noise_score=0.0, is_photo=False, needs=[],
    )  # fmt: skip
    raw = PageRaw(page_id="p1", source_file="f.pdf", page_index=0, sha256="0" * 64, image=_ref())
    return NormalizedPage(
        raw=raw, measurement=measurement, corrections_applied=corrections or [],
        color=_ref(), gray_bin=_ref("gray_bin"), thumb=_ref("thumb"),
    )  # fmt: skip


def make_ocr(lines: list[tuple[float, str]]) -> OcrResult:
    """Recorded-style OCR: each (y, text) line, first word rightmost (right-to-left)."""
    words: list[OcrWord] = []
    for y, text in lines:
        for n, part in enumerate(text.split()):
            x_max = 0.95 - n * WORD_W
            box: Box = (y, x_max - WORD_W, y + LINE_H, x_max)
            words.append(OcrWord(text=part, box=box, confidence=0.9))
    return OcrResult(
        reader=ReaderName.tesseract,
        words=words,
        text=" ".join(w.text for w in words),
    )


def region(kind: RegionKind, box: Box = (0.0, 0.0, 0.2, 1.0)) -> Region:
    return Region(kind=kind, box=box, detector="test", score=0.9)


@dataclass
class ScriptedVision:
    """Fake VisionLLMPort answering a fixed text; records whether it was called."""

    answer: str = ""
    fail: bool = False
    name: str = "scripted"
    calls: int = 0

    def extract(self, request: VisionRequest) -> VisionResponse:
        self.calls += 1
        if self.fail:
            raise ProviderExhausted("all providers failed")
        return VisionResponse(
            raw_text=self.answer, candidates=[], provider=ReaderName.gemini,
            model_id="fake", prompt_version=request.prompt_version, duration_ms=0,
        )  # fmt: skip

    def health(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, credentials=1, state="ok")


@dataclass
class ScriptedLogo:
    """Fake LogoMatcherPort returning a fixed hit."""

    hit: LogoHit | None

    def match(self, page: NormalizedPage, *, box: Box | None = None) -> LogoHit | None:
        return self.hit


def crop_stub(image: ImageRef, box: Box) -> ImageRef:
    return image.model_copy(update={"kind": "crop"})


def run(
    index: SenderIndex,
    lines: list[tuple[float, str]],
    regions: list[Region] | None = None,
    **kwargs: object,
) -> Classification:
    return classify(
        make_page(),
        make_ocr(lines),
        regions or [],
        index,
        config=CONFIG,
        **kwargs,  # type: ignore[arg-type]
    )


# --- destination -------------------------------------------------------------------------------


@pytest.mark.parametrize("token", DESTINATION_TOKENS)
def test_destination_never_returned(index: SenderIndex, token: str) -> None:
    # The addressee after "אל:"/"לכבוד" must go with the marker; other phrases stand alone.
    follow = " מכבי שירותי בריאות" if token in {"אל:", "לכבוד"} else ""
    lines = [(0.05, f"{token}{follow}"), (0.5, FILLER)]
    result = run(index, lines, [region(RegionKind.letterhead)])
    assert result.sender is None
    assert "sender_not_in_list" in result.sender_flags


def test_destination_never_returned_on_full_page_fallback(index: SenderIndex) -> None:
    lines = [(0.5, "אל: מעבדת נגיפים תל השומר שיבא"), (0.6, FILLER)]
    assert run(index, lines).sender is None


def test_suppression_removes_four_tokens_after_marker() -> None:
    words = make_ocr([(0.1, "אל: א ב ג ד ה")]).words
    kept = suppress_destination(words, follow_tokens=4)
    assert [w.text for w in kept] == ["ה"]


# --- SAP screens -------------------------------------------------------------------------------

SAP_CUE = "נתוני ביקור"


def _sap_lines(body: str) -> list[tuple[float, str]]:
    return [(0.05, SAP_CUE), (0.1, body), (0.6, FILLER)]


def test_sap_screen_sender_from_goram_mafne(index: SenderIndex) -> None:
    result = run(index, _sap_lines("גורם מפנה: וולפסון"), [region(RegionKind.table)])
    assert result.page_role == "sap_screen"
    assert result.sender == "וולפסון"
    assert result.sender_method == "text"
    assert result.sender_flags == []


def test_sap_goram_mafne_wins_over_mevate_ach(index: SenderIndex) -> None:
    result = run(index, _sap_lines("גורם מפנה: וולפסון מבטח: כללית"), [region(RegionKind.table)])
    assert result.sender == "וולפסון"
    assert result.sender_flags == ["multiple_candidates"]


def test_sap_agreeing_payer_is_not_flagged(index: SenderIndex) -> None:
    result = run(index, _sap_lines("גורם מפנה: כללית מבטח: כללית"), [region(RegionKind.table)])
    assert result.sender == "כללית"
    assert result.sender_flags == []


def test_sap_unknown_goram_mafne_is_reported_not_guessed(index: SenderIndex) -> None:
    result = run(index, _sap_lines("גורם מפנה: חברה בדויה"), [region(RegionKind.table)])
    assert result.sender is None
    assert result.sender_flags == ["sender_not_in_list"]


def test_sap_without_goram_mafne_falls_through(index: SenderIndex) -> None:
    lines = [(0.05, SAP_CUE), (0.1, "בית החולים אדית וולפסון"), (0.6, FILLER)]
    result = run(index, lines, [region(RegionKind.table)])
    assert result.page_role == "sap_screen"
    assert result.sender == "וולפסון"
    assert result.sender_method == "text"


# --- sender matching ---------------------------------------------------------------------------


def test_matches_wolfson_letterhead(index: SenderIndex) -> None:
    lines = [(0.03, "בית החולים אדית וולפסון"), (0.5, FILLER)]
    result = run(index, lines, [region(RegionKind.letterhead)])
    assert result.sender == "וולפסון"
    assert result.sender_method == "text"
    assert result.sender_score >= index.min_ratio
    assert result.family == "hospital"


def test_alias_shamir_resolves_to_asaf_harofe(index: SenderIndex) -> None:
    lines = [(0.03, "המרכז הרפואי שמיר"), (0.5, FILLER)]
    assert run(index, lines, [region(RegionKind.letterhead)]).sender == "אסף הרופא"


def test_typo_tolerance_klalich(index: SenderIndex) -> None:
    lines = [(0.03, "כלליח"), (0.5, FILLER)]
    assert run(index, lines, [region(RegionKind.letterhead)]).sender == "כללית"


def test_letterhead_band_used_when_no_region_detected(index: SenderIndex) -> None:
    lines = [(0.03, "מכבי שירותי בריאות"), (0.5, FILLER)]
    assert run(index, lines).sender == "מכבי שירותי בריאות"


def test_below_threshold_returns_none(index: SenderIndex) -> None:
    lines = [(0.03, "חברה בדויה בעמ"), (0.5, FILLER)]
    result = run(index, lines, [region(RegionKind.letterhead)])
    assert result.sender is None
    assert result.sender_method == "none"
    assert result.sender_score == 0.0
    assert result.sender_flags == ["sender_not_in_list"]


# --- logo and model ----------------------------------------------------------------------------

UNKNOWN_HEAD = [(0.03, "חברה בדויה בעמ"), (0.5, FILLER)]


def test_logo_accepted_at_inlier_threshold(index: SenderIndex) -> None:
    logo = ScriptedLogo(LogoHit(canonical="כללית", inlier_ratio=0.35, inliers=20))
    result = run(index, UNKNOWN_HEAD, logo=logo)
    assert (result.sender, result.sender_method) == ("כללית", "logo")


def test_logo_below_inlier_threshold_is_ignored(index: SenderIndex) -> None:
    logo = ScriptedLogo(LogoHit(canonical="כללית", inlier_ratio=0.34, inliers=20))
    assert run(index, UNKNOWN_HEAD, logo=logo).sender is None


def test_logo_outside_closed_list_is_ignored(index: SenderIndex) -> None:
    logo = ScriptedLogo(LogoHit(canonical="חברה בדויה", inlier_ratio=0.9, inliers=50))
    assert run(index, UNKNOWN_HEAD, logo=logo).sender is None


def test_model_answer_in_closed_list_is_accepted(index: SenderIndex) -> None:
    result = run(index, UNKNOWN_HEAD, vision=ScriptedVision("כללית"), crop=crop_stub)
    assert (result.sender, result.sender_method) == ("כללית", "model")
    assert result.sender_score == 0.0  # the model never assigns its own confidence


@pytest.mark.parametrize("answer", ["חברה בדויה", NOT_RECOGNISED, ""])
def test_model_answer_outside_closed_list_is_discarded(index: SenderIndex, answer: str) -> None:
    result = run(index, UNKNOWN_HEAD, vision=ScriptedVision(answer), crop=crop_stub)
    assert result.sender is None
    assert result.sender_method == "none"


def test_model_provider_failure_leaves_sender_unresolved(index: SenderIndex) -> None:
    result = run(index, UNKNOWN_HEAD, vision=ScriptedVision(fail=True), crop=crop_stub)
    assert result.sender is None


def test_model_not_called_without_a_crop_function(index: SenderIndex) -> None:
    vision = ScriptedVision("כללית")
    assert run(index, UNKNOWN_HEAD, vision=vision).sender is None
    assert vision.calls == 0


def test_model_not_called_when_letterhead_overlaps_clinical_region(index: SenderIndex) -> None:
    vision = ScriptedVision("כללית")
    regions = [region(RegionKind.clinical, (0.0, 0.0, 0.5, 1.0))]
    run(index, UNKNOWN_HEAD, regions, vision=vision, crop=crop_stub)
    assert vision.calls == 0


def test_model_not_called_when_text_already_resolved(index: SenderIndex) -> None:
    vision = ScriptedVision("כללית")
    run(index, [(0.03, "מכבי שירותי בריאות"), (0.5, FILLER)], vision=vision, crop=crop_stub)
    assert vision.calls == 0


# --- page role ---------------------------------------------------------------------------------


def test_role_sap_screen_detected(index: SenderIndex) -> None:
    result = run(index, _sap_lines("גורם מפנה: וולפסון"), [region(RegionKind.table)])
    assert result.page_role == "sap_screen"


def test_role_sap_screen_detected_from_rotation_alone(index: SenderIndex) -> None:
    page = make_page(rotation=90, corrections=["rotate"])
    result = classify(
        page, make_ocr([(0.5, FILLER)]), [region(RegionKind.table)], index, config=CONFIG
    )
    assert result.page_role == "sap_screen"


def test_role_blank_detected(index: SenderIndex) -> None:
    result = run(index, [(0.5, "מסמך קצר")])
    assert result.page_role == "blank"
    assert result.sender is None
    assert result.sender_flags == []  # a blank page is not an unlisted sender


def test_role_continuation_detected(index: SenderIndex) -> None:
    result = run(index, [(0.5, FILLER), (0.7, "עמוד 2 מתוך 3")])
    assert result.page_role == "continuation"


def test_first_page_of_sequence_is_not_continuation(index: SenderIndex) -> None:
    result = run(index, [(0.5, FILLER), (0.7, "עמוד 1 מתוך 3")])
    assert result.page_role == "unknown"


def test_role_lab_report_detected(index: SenderIndex) -> None:
    assert run(index, [(0.5, FILLER), (0.7, "רשימת עבודה")]).page_role == "lab_report"


def test_role_primary_form_from_patient_box(index: SenderIndex) -> None:
    result = run(index, [(0.5, FILLER)], [region(RegionKind.patient_box)])
    assert result.page_role == "primary_form"


# --- contract and config -----------------------------------------------------------------------


def test_classification_rejects_reason_outside_closed_set() -> None:
    with pytest.raises(ValueError, match="closed reason set"):
        Classification(
            sender=None, sender_score=0.0, sender_method="none", family=None,
            page_role="unknown", role_score=0.0, sender_flags=["free text"],
        )  # fmt: skip


def test_classify_config_defaults_equal_models_yaml() -> None:
    path = Path(__file__).resolve().parents[3] / "config" / "models.yaml"
    section = yaml.safe_load(path.read_text(encoding="utf-8"))["classify"]
    assert ClassifyConfig.model_validate(section) == ClassifyConfig()
