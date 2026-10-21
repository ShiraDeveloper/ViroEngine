"""Tests for the ORB logo matcher (T10). Images are synthetic so CI needs no reference/ data.

test_logo_found_inside_letterhead_band      - a logo pasted into a noisy page is matched
test_unrelated_page_gives_no_confident_hit  - a different page does not reach the 0.35 threshold
test_no_logo_files_gives_none               - an empty manifest finds nothing
test_unreadable_image_gives_none            - undecodable bytes never raise
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from backend.adapters.fakes import FakeStorage
from backend.adapters.senders.logo_match import OrbLogoMatcher
from backend.core.models import NormalizedPage, PageMeasurement, PageRaw

LOGO_THRESHOLD = 0.35  # config/models.yaml: classify.logo_min_inlier


def _blobs(seed: int, shape: tuple[int, int]) -> npt.NDArray[np.uint8]:
    """Smooth random blobs with corners: stable ORB features, deterministic per seed."""
    rng = np.random.default_rng(seed)
    image = np.full(shape, 255, dtype=np.uint8)
    for _ in range(60):
        centre = (int(rng.integers(0, shape[1])), int(rng.integers(0, shape[0])))
        axes = (int(rng.integers(4, 18)), int(rng.integers(4, 18)))
        shade = (float(rng.integers(0, 200)),)
        cv2.ellipse(image, centre, axes, int(rng.integers(0, 180)), 0, 360, shade, -1)
        top_left = (int(rng.integers(0, shape[1] - 20)), int(rng.integers(0, shape[0] - 20)))
        cv2.rectangle(image, top_left, (top_left[0] + 14, top_left[1] + 9), shade, 2)
    return image


def _png(image: npt.NDArray[np.uint8]) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return bytes(buffer)


def _page(storage: FakeStorage, data: bytes) -> NormalizedPage:
    ref = storage.put_page_image("job", "p1", data, "color")
    raw = PageRaw(page_id="p1", source_file="f.pdf", page_index=0, sha256="0" * 64, image=ref)
    measurement = PageMeasurement(
        rotation_deg=0, rotation_votes={}, skew_deg=0.0, blur_score=100.0,
        illumination_cv=0.1, noise_score=0.0, is_photo=False, needs=[],
    )  # fmt: skip
    return NormalizedPage(
        raw=raw, measurement=measurement, corrections_applied=[], color=ref, gray_bin=ref, thumb=ref
    )


def _logos_dir(tmp_path: Path, logo: npt.NDArray[np.uint8] | None) -> Path:
    entries = []
    if logo is not None:
        cv2.imwrite(str(tmp_path / "klalit.png"), logo)
        entries.append({"canonical": "כללית", "file": "klalit.png", "source_page": "A1"})
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": 1, "logos": entries}, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def test_logo_found_inside_letterhead_band(tmp_path: Path) -> None:
    logo = _blobs(1, (120, 220))
    page = _blobs(2, (600, 500))
    page[20:140, 100:320] = logo
    storage = FakeStorage()
    matcher = OrbLogoMatcher(storage, _logos_dir(tmp_path, logo))
    hit = matcher.match(_page(storage, _png(page)), box=(0.0, 0.0, 0.3, 1.0))
    assert matcher.logo_count == 1
    assert hit is not None
    assert hit.canonical == "כללית"
    assert hit.inlier_ratio >= LOGO_THRESHOLD


def test_unrelated_page_gives_no_confident_hit(tmp_path: Path) -> None:
    storage = FakeStorage()
    matcher = OrbLogoMatcher(storage, _logos_dir(tmp_path, _blobs(1, (120, 220))))
    hit = matcher.match(_page(storage, _png(_blobs(3, (600, 500)))))
    assert hit is None or hit.inlier_ratio < LOGO_THRESHOLD


def test_no_logo_files_gives_none(tmp_path: Path) -> None:
    storage = FakeStorage()
    matcher = OrbLogoMatcher(storage, _logos_dir(tmp_path, None))
    assert matcher.logo_count == 0
    assert matcher.match(_page(storage, _png(_blobs(2, (600, 500))))) is None


def test_unreadable_image_gives_none(tmp_path: Path) -> None:
    storage = FakeStorage()
    matcher = OrbLogoMatcher(storage, _logos_dir(tmp_path, _blobs(1, (120, 220))))
    assert matcher.match(_page(storage, b"not an image")) is None


def test_missing_manifest_gives_none(tmp_path: Path) -> None:
    assert OrbLogoMatcher(FakeStorage(), tmp_path).logo_count == 0
