"""Tests for S7 second local read (T12).

test_commitment_whitelist_allows_letters         - digits + A-Z reaches the reader; X132767 survives
test_second_read_runs_only_on_risky_candidates   - a safe printed value is skipped
test_crop_padding_prevents_edge_clipping         - 2% of the diagonal, clamped to the page
"""

# ruff: noqa: RUF001  (Hebrew letters are not confusable text here)

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from backend.adapters.fakes import FixedClock
from backend.core.errors import RenderFailed
from backend.core.models import (
    Box,
    Candidate,
    FieldKey,
    ImageRef,
    OcrResult,
    OcrWord,
    ReaderName,
    RegionKind,
    SourceKind,
)
from backend.core.pipeline.s7_second_read import (
    COMMITMENT_WHITELIST,
    DIGITS,
    SecondRead,
    SecondReadConfig,
    is_risky,
    padded_box,
    second_read,
    whitelist_for,
)
from backend.tests.fixtures.layout import image_ref

BOX: Box = (0.40, 0.40, 0.42, 0.60)


def _cand(
    value: str,
    *,
    source: SourceKind = SourceKind.printed,
    conf: float = 0.95,
    box: Box | None = BOX,
) -> Candidate:
    return Candidate(
        value=value,
        reader=ReaderName.tesseract,
        source=source,
        box=box,
        reader_confidence=conf,
        label_distance=0.05,
        region=RegionKind.patient_box,
    )


@dataclass
class RecordingOcr:
    """A plain OcrPort fake that records how it was called and returns a fixed reading."""

    text: str = ""
    alternatives: list[str] = field(default_factory=list)
    reader: ReaderName = ReaderName.tesseract
    name: str = "recording_ocr"
    calls: list[tuple[str | None, int]] = field(default_factory=list)

    def read_page(self, image: ImageRef, langs: str = "heb+eng") -> OcrResult:
        return OcrResult(reader=self.reader, words=[], text="")

    def read_crop(
        self, image: ImageRef, *, whitelist: str | None = None, psm: int = 7
    ) -> OcrResult:
        self.calls.append((whitelist, psm))
        words = [OcrWord(text=self.text, box=BOX, confidence=0.8)] if self.text else []
        return OcrResult(
            reader=self.reader, words=words, text=self.text, candidates=self.alternatives
        )


@dataclass
class RecordingCrop:
    calls: list[tuple[Box, float]] = field(default_factory=list)

    def __call__(self, image: ImageRef, box: Box, scale: float) -> ImageRef:
        self.calls.append((box, scale))
        return image


@dataclass
class FailingCrop:
    def __call__(self, image: ImageRef, box: Box, scale: float) -> ImageRef:
        raise RenderFailed("crop failed")


def _run(
    candidates: list[Candidate],
    ocr: RecordingOcr,
    crop: RecordingCrop | None = None,
    *,
    field: FieldKey = FieldKey.commitment_number,
    digit_ocr: RecordingOcr | None = None,
) -> SecondRead:
    return second_read(
        candidates,
        field=field,
        image=image_ref(),
        ocr=ocr,
        crop=crop or RecordingCrop(),
        clock=FixedClock(),
        digit_ocr=digit_ocr,
    )


def test_commitment_whitelist_allows_letters() -> None:
    assert set("X") <= set(COMMITMENT_WHITELIST)
    assert set(DIGITS) <= set(COMMITMENT_WHITELIST)
    ocr = RecordingOcr(text="X132767")
    result = _run([_cand("X132767", source=SourceKind.handwritten)], ocr)
    assert ocr.calls[0][0] == COMMITMENT_WHITELIST
    assert [c.value for c in result.reads] == ["X132767"]


def test_id_whitelist_is_digits_only_and_names_have_none() -> None:
    assert whitelist_for(FieldKey.national_id) == DIGITS
    assert whitelist_for(FieldKey.first_name) is None
    assert whitelist_for(FieldKey.last_name) is None


def test_second_read_runs_only_on_risky_candidates() -> None:
    ocr = RecordingOcr(text="דני")
    safe = _cand("דני")  # printed, confident, no confusable digit
    risky = _cand("כהן", source=SourceKind.handwritten)
    result = _run([safe, risky], ocr, field=FieldKey.first_name)
    assert [s.outcome for s in result.trail] == ["skipped", "value"]
    assert len(ocr.calls) == 1


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (_cand("דני"), False),
        (_cand("דני", source=SourceKind.handwritten), True),
        (_cand("דני", source=SourceKind.stamp), True),
        (_cand("דני", conf=0.5), True),
        (_cand("12"), True),
        (_cand("730578626", source=SourceKind.barcode, conf=0.1), False),
    ],
)
def test_riskiness_predicate(candidate: Candidate, expected: bool) -> None:
    assert is_risky(candidate, hints=[], config=SecondReadConfig()) is expected


def test_failed_soft_hint_makes_a_candidate_risky() -> None:
    plain = _cand("דני")
    assert not is_risky(plain, hints=[], config=SecondReadConfig())
    assert is_risky(plain, hints=[r"\d{9}"], config=SecondReadConfig())


def test_candidate_without_a_box_is_skipped() -> None:
    result = _run([_cand("12", box=None)], RecordingOcr(text="12"))
    assert [s.outcome for s in result.trail] == ["skipped"]
    assert result.reads == []


def test_crop_padding_prevents_edge_clipping() -> None:
    cfg = SecondReadConfig()
    image = image_ref(1000, 1000)
    padded = padded_box(BOX, image, cfg)
    pad = 0.02 * (2 * 1000**2) ** 0.5 / 1000
    assert padded[0] == pytest.approx(BOX[0] - pad)
    assert padded[3] == pytest.approx(BOX[3] + pad)
    assert padded[0] < BOX[0] and padded[1] < BOX[1] and padded[2] > BOX[2] and padded[3] > BOX[3]


def test_crop_padding_is_clamped_to_the_page() -> None:
    edge: Box = (0.0, 0.0, 1.0, 1.0)
    assert padded_box(edge, image_ref(), SecondReadConfig()) == edge


def test_padding_is_equal_in_pixels_on_both_axes() -> None:
    image = image_ref(1000, 2000)
    padded = padded_box(BOX, image, SecondReadConfig())
    pad_x_px = (BOX[1] - padded[1]) * image.width
    pad_y_px = (BOX[0] - padded[0]) * image.height
    assert pad_x_px == pytest.approx(pad_y_px)


def test_crop_receives_padded_box_and_upscale() -> None:
    crop = RecordingCrop()
    _run([_cand("X1", source=SourceKind.handwritten)], RecordingOcr(text="X1"), crop)
    box, scale = crop.calls[0]
    assert box == padded_box(BOX, image_ref(), SecondReadConfig())
    assert scale == 2.5


def test_reader_uses_single_line_mode() -> None:
    ocr = RecordingOcr(text="1")
    _run([_cand("1", source=SourceKind.handwritten)], ocr)
    assert ocr.calls[0][1] == 7


def test_empty_reading_is_no_value_and_adds_no_candidate() -> None:
    result = _run([_cand("1", source=SourceKind.handwritten)], RecordingOcr(text=""))
    assert [s.outcome for s in result.trail] == ["no_value"]
    assert result.reads == []


def test_digit_reader_runs_only_for_handwritten_numbers() -> None:
    tess = RecordingOcr(text="011223344")
    trocr = RecordingOcr(
        text="011223344", alternatives=["011223844", "011223314"], reader=ReaderName.trocr
    )
    handwritten = _cand("011223344", source=SourceKind.handwritten)
    _run([handwritten], tess, field=FieldKey.national_id, digit_ocr=trocr)
    assert len(trocr.calls) == 1
    printed = _cand("011223344", conf=0.5)
    trocr.calls.clear()
    _run([printed], tess, field=FieldKey.national_id, digit_ocr=trocr)
    assert trocr.calls == []


def test_digit_reader_alternatives_are_capped() -> None:
    trocr = RecordingOcr(
        text="1", alternatives=[str(n) for n in range(2, 12)], reader=ReaderName.trocr
    )
    result = _run(
        [_cand("1", source=SourceKind.handwritten)],
        RecordingOcr(text="1"),
        field=FieldKey.national_id,
        digit_ocr=trocr,
    )
    digit_reads = [c for c in result.reads if c.reader is ReaderName.trocr]
    assert len(digit_reads) == SecondReadConfig().digit_candidates


def test_second_read_is_local_and_keeps_provenance() -> None:
    result = _run([_cand("12", source=SourceKind.handwritten)], RecordingOcr(text="12"))
    assert all(not s.cloud for s in result.trail)
    assert result.reads[0].source is SourceKind.handwritten
    assert result.reads[0].box == BOX
    assert "second_read" in result.reads[0].notes


def test_recoverable_crop_failure_skips_the_candidate_and_continues() -> None:
    result = second_read(
        [_cand("12", source=SourceKind.handwritten), _cand("34", source=SourceKind.handwritten)],
        field=FieldKey.national_id,
        image=image_ref(),
        ocr=RecordingOcr(text="1"),
        crop=FailingCrop(),
        clock=FixedClock(),
    )
    assert [s.outcome for s in result.trail] == ["skipped", "skipped"]


def test_second_read_config_defaults_equal_models_yaml() -> None:
    path = Path(__file__).resolve().parents[3] / "config" / "models.yaml"
    section = yaml.safe_load(path.read_text(encoding="utf-8"))["second_read"]
    assert SecondReadConfig.model_validate(section) == SecondReadConfig()
