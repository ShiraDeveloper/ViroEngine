"""T25 — the five evaluation degradations (eval/augment.py).

test_nohl_removes_yellow                       — no highlighter pixel survives
test_nohl_preserves_text_under_highlight       — ink drawn over the highlight stays dark
test_rot_is_multiple_of_90_plus_small_skew     — turn is a quarter-turn, skew within 3 degrees
test_seeded_runs_are_identical                 — same seed, same bytes; other seed differs
test_all_five_ops_produce_valid_images         — every op returns a non-empty uint8 BGR image
test_augment_set_produces_expected_count       — 48 sources x 5 ops = 240 files
test_augment_page_rejects_unknown_op           — an unknown op is a named error, not a guess
test_augment_png_round_trips_for_stress_mode   — the (bytes, op, seed) callable Stress Mode uses
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.adapters.imaging.codec import decode_bgr, encode_png
from eval.augment import (
    OPS,
    ROT_MAX_SKEW_DEG,
    augment_page,
    augment_png,
    augment_set,
)
from eval.make_gold import yellow_mask
from eval.run_eval import EvalInputError

YELLOW_BGR = (0, 255, 255)
SIZE = (200, 300)  # height, width


def page_with_highlight() -> np.ndarray:
    """White page, a yellow band, and black ink strokes drawn over the band."""
    img = np.full((*SIZE, 3), 255, np.uint8)
    cv2.rectangle(img, (40, 60), (260, 110), YELLOW_BGR, thickness=-1)
    for x in range(60, 240, 20):
        cv2.line(img, (x, 70), (x, 100), (0, 0, 0), thickness=3)
    return img


def rng(seed: int = 1) -> np.random.Generator:
    return np.random.default_rng(seed)


def test_nohl_removes_yellow() -> None:
    out = augment_page(page_with_highlight(), "nohl", rng=rng())

    assert int(np.count_nonzero(yellow_mask(out))) == 0


def test_nohl_preserves_text_under_highlight() -> None:
    src = page_with_highlight()
    ink = src.max(axis=2) < 60  # the black strokes
    assert ink.sum() > 0

    out = augment_page(src, "nohl", rng=rng())

    assert (out.max(axis=2)[ink] < 60).mean() > 0.95
    assert out[65, 45].tolist() == [255, 255, 255]  # highlight became paper, not ink


@pytest.mark.parametrize("seed", range(8))
def test_rot_is_multiple_of_90_plus_small_skew(seed: int) -> None:
    src = np.full((120, 120, 3), 255, np.uint8)
    cv2.rectangle(src, (10, 10), (50, 30), (0, 0, 0), thickness=-1)

    out = augment_page(src, "rot", rng=rng(seed))

    # A square page keeps its shape under any quarter-turn; only the skew may differ.
    assert out.shape == src.shape
    ys, xs = np.nonzero(out.max(axis=2) < 128)
    assert len(xs) > 0
    expected_centres = [(30, 20), (99, 30), (90, 99), (20, 90)]  # (x, y) per quarter turn
    cx, cy = float(xs.mean()), float(ys.mean())
    reach = 60 * np.sin(np.radians(ROT_MAX_SKEW_DEG)) * 2 + 6
    assert any(abs(cx - ex) <= reach and abs(cy - ey) <= reach for ex, ey in expected_centres)


def test_seeded_runs_are_identical() -> None:
    src = page_with_highlight()
    a = [augment_png(encode_png(src).data, op, 20261018) for op in OPS]
    b = [augment_png(encode_png(src).data, op, 20261018) for op in OPS]
    other = augment_png(encode_png(src).data, "fax", 7)

    assert a == b
    assert other != a[OPS.index("fax")]


@pytest.mark.parametrize("op", OPS)
def test_all_five_ops_produce_valid_images(op: str) -> None:
    src = page_with_highlight()
    before = src.copy()

    out = augment_page(src, op, rng=rng())

    assert out.dtype == np.uint8 and out.ndim == 3 and out.shape[2] == 3 and out.size > 0
    assert np.array_equal(src, before)  # the input is never modified
    decode_bgr(encode_png(out).data)  # it survives a PNG round trip


def test_augment_set_produces_expected_count(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    pages = []
    for i in range(48):
        path = src_dir / f"p{i:02d}.png"
        path.write_bytes(encode_png(np.full((24, 32, 3), 255, np.uint8)).data)
        pages.append(path)

    stats = augment_set(pages, tmp_path / "out", seed=20261018)

    assert stats.written == 240
    assert stats.per_op == dict.fromkeys(OPS, 48)
    assert len(list((tmp_path / "out").glob("*.png"))) == 240


def test_augment_set_skips_unreadable_page(tmp_path: Path) -> None:
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")

    stats = augment_set([bad], tmp_path / "out")

    assert stats.written == 0


def test_augment_page_rejects_unknown_op() -> None:
    with pytest.raises(EvalInputError):
        augment_page(page_with_highlight(), "sepia", rng=rng())


def test_augment_png_round_trips_for_stress_mode() -> None:
    out = augment_png(encode_png(page_with_highlight()).data, "jpeg", 20261018)

    assert decode_bgr(out).shape == page_with_highlight().shape
