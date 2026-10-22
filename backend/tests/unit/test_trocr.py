"""Tests for the TrOCR digit reader (T13). The ONNX sessions are replaced by plain fakes that
script the decoder's next-token scores, so beam search, the whitelist, lazy loading and the
size budget are exercised without the model files (git-ignored) or onnxruntime."""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
import pytest

from backend.adapters.fakes import FakeStorage
from backend.adapters.ocr.trocr_digits import TrOcrDigits
from backend.core.models import ImageRef, ReaderName
from backend.settings import Settings

# A tiny SentencePiece-style vocabulary: specials first, as in TrOCR (start = end = 2).
TOKENS = ["<s>", "<pad>", "</s>", "<unk>", "\u2581", *"0123456789", "39", "158", "a", "\u2581hi"]
ID = {t: i for i, t in enumerate(TOKENS)}
EOS = 2


def _vocab(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "tokens": TOKENS,
                "decoder_start_token_id": EOS,
                "eos_token_id": EOS,
                "pad_token_id": 1,
                "special_ids": [0, 1, 2, 3],
                "image_height": 8,
                "image_width": 16,
                "image_mean": [0.5, 0.5, 0.5],
                "image_std": [0.5, 0.5, 0.5],
                "rescale_factor": 1 / 255,
            }
        ),
        encoding="utf-8",
    )


@dataclass
class FakeEncoder:
    def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> list[Any]:
        assert input_feed["pixel_values"].shape == (1, 3, 8, 16)
        return [np.zeros((1, 4, 8), dtype=np.float32)]


@dataclass
class FakeDecoder:
    """Prefers the path 9 · 39 · 158 (= '939158'), with a runner-up digit at each step.
    At the first step the letter 'a' scores highest of all - only a whitelist removes it."""

    path: tuple[str, ...] = ("9", "39", "158")
    runner_up: tuple[str, ...] = ("4", "8", "3")

    def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> list[Any]:
        ids = input_feed["input_ids"]
        batch, steps = ids.shape
        logits = np.full((batch, steps, len(TOKENS)), -8.0, dtype=np.float32)
        for b in range(batch):
            prefix = [TOKENS[i] for i in ids[b, 1:]]
            t = len(prefix)
            row = logits[b, -1]
            if t < len(self.path):
                row[ID[self.path[t]]] = 4.0
                row[ID[self.runner_up[t]]] = 2.5
                if t == 0:
                    row[ID["a"]] = 6.0
            else:
                row[EOS] = 5.0
        return [logits]


@dataclass
class CountingFactory:
    calls: list[str] = field(default_factory=list)

    def __call__(self, path: Path) -> Any:
        self.calls.append(path.name)
        return FakeEncoder() if "encoder" in path.name else FakeDecoder()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, models_dir=str(tmp_path), data_dir=str(tmp_path))  # type: ignore[call-arg]


@pytest.fixture
def installed(settings: Settings) -> Settings:
    """Model files in place: two small placeholder graphs and the vocabulary."""
    root = Path(settings.models_dir)
    (root / settings.trocr_vision_file).write_bytes(b"\0" * 1024)
    (root / settings.trocr_text_file).write_bytes(b"\0" * 1024)
    _vocab(root / settings.trocr_vocab_file)
    return settings


@pytest.fixture
def crop() -> tuple[FakeStorage, ImageRef]:
    storage = FakeStorage()
    buffer = io.BytesIO()
    Image.new("RGB", (60, 20), "white").save(buffer, format="PNG")
    return storage, storage.put_page_image("job", "p1", buffer.getvalue(), "crop")


def test_returns_multiple_candidates(
    installed: Settings, crop: tuple[FakeStorage, ImageRef]
) -> None:
    storage, ref = crop
    reader = TrOcrDigits(settings=installed, storage=storage, session_factory=CountingFactory())
    result = reader.read_crop(ref, whitelist="0123456789")
    assert result.reader is ReaderName.trocr
    assert result.candidates[0] == "939158" == result.text
    assert 2 <= len(result.candidates) <= installed.trocr_candidates
    assert len(set(result.candidates)) == len(result.candidates)  # distinct readings
    assert all(c.isdigit() for c in result.candidates)
    assert len(result.words) == 1 and 0.0 < result.words[0].confidence <= 1.0


def test_lazy_load(installed: Settings, crop: tuple[FakeStorage, ImageRef]) -> None:
    """Nothing loads until a crop is read; the sessions are then cached."""
    storage, ref = crop
    factory = CountingFactory()
    reader = TrOcrDigits(settings=installed, storage=storage, session_factory=factory)
    assert factory.calls == [] and not reader.loaded
    reader.read_crop(ref, whitelist="0123456789")
    reader.read_crop(ref, whitelist="0123456789")
    assert sorted(factory.calls) == sorted([installed.trocr_vision_file, installed.trocr_text_file])
    assert reader.loaded


def test_model_size_under_budget() -> None:
    """The exported model (when installed in ./models) fits the 80 MB budget."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    reader = TrOcrDigits(settings=settings, storage=FakeStorage())
    if not all(p.is_file() for p in reader.model_paths()):
        pytest.skip("TrOCR not installed; run `python -m tools.export_trocr` (models/README.md)")
    assert reader.model_size_mb() < settings.trocr_max_model_mb


def test_over_budget_model_refused(installed: Settings, crop: tuple[FakeStorage, ImageRef]) -> None:
    storage, ref = crop
    tight = installed.model_copy(update={"trocr_max_model_mb": 0.001})
    factory = CountingFactory()
    result = TrOcrDigits(settings=tight, storage=storage, session_factory=factory).read_crop(ref)
    assert result.text == "" and result.candidates == [] and factory.calls == []


def test_missing_model_returns_empty(
    settings: Settings, crop: tuple[FakeStorage, ImageRef]
) -> None:
    storage, ref = crop
    factory = CountingFactory()
    result = TrOcrDigits(settings=settings, storage=storage, session_factory=factory).read_crop(ref)
    assert result.words == [] and result.candidates == [] and factory.calls == []


def test_whitelist_restricts_tokens(
    installed: Settings, crop: tuple[FakeStorage, ImageRef]
) -> None:
    """Unrestricted, the letter wins the first step; with a digits whitelist it never appears."""
    storage, ref = crop
    reader = TrOcrDigits(settings=installed, storage=storage, session_factory=CountingFactory())
    assert reader.read_crop(ref).text.startswith("a")
    assert reader.read_crop(ref, whitelist="0123456789").text == "939158"


def test_unreadable_crop_returns_empty(installed: Settings) -> None:
    storage = FakeStorage()
    ref = storage.put_page_image("job", "p1", b"not an image", "crop")
    reader = TrOcrDigits(settings=installed, storage=storage, session_factory=CountingFactory())
    assert reader.read_crop(ref).candidates == []
