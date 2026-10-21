"""ORB logo matcher: the letterhead fallback when the printed name does not match (T10).

Logos are cropped from the sample pages into config/logos/ (git-ignored, they are sample data);
config/logos/manifest.json is committed and names the source page of each so they can be
regenerated. Without logo files the matcher simply finds nothing. Runs fully offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from backend.core.models import Box, NormalizedPage
from backend.core.ports.logo import LogoHit
from backend.core.ports.storage import StoragePort

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "config" / "logos"

# Structural: ORB feature budget. Letterhead crops are small; more features only add noise.
ORB_FEATURES = 1000
# Structural: Lowe's ratio test (D. Lowe, 2004) - the standard value.
LOWE_RATIO = 0.75
# Structural: a homography needs 4 point pairs; twice that keeps RANSAC from fitting noise.
MIN_GOOD_MATCHES = 8
# Structural: RANSAC reprojection tolerance in pixels at the 300 dpi render (PLAN §3).
RANSAC_PX = 5.0


class OrbLogoMatcher:
    """Match known institution logos against a page region. Implements LogoMatcherPort."""

    def __init__(self, storage: StoragePort, logos_dir: Path | None = None) -> None:
        self._storage = storage
        self._orb = cv2.ORB_create(nfeatures=ORB_FEATURES)  # type: ignore[attr-defined]
        self._bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        self._logos: list[tuple[str, Any, Any]] = self._load(logos_dir or DEFAULT_DIR)

    @property
    def logo_count(self) -> int:
        return len(self._logos)

    def _load(self, directory: Path) -> list[tuple[str, Any, Any]]:
        """(canonical, keypoints, descriptors) per logo file present; absent files are skipped."""
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            return []
        entries = json.loads(manifest.read_text(encoding="utf-8")).get("logos", [])
        loaded: list[tuple[str, Any, Any]] = []
        for entry in entries:
            path = directory / str(entry["file"])
            if not path.is_file():
                continue
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            keypoints, descriptors = self._orb.detectAndCompute(image, None)
            if descriptors is not None and len(keypoints) >= MIN_GOOD_MATCHES:
                loaded.append((str(entry["canonical"]), keypoints, descriptors))
        return loaded

    def match(self, page: NormalizedPage, *, box: Box | None = None) -> LogoHit | None:
        """Best logo inside `box` (whole page when None) by RANSAC inlier ratio, or None.
        Never raises; unreadable images, no logos and too few features all give None."""
        if not self._logos:
            return None
        raw = self._storage.get(page.color)
        if not raw:
            return None
        try:
            image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                return None
            if box is not None:
                height, width = image.shape[:2]
                y0, x0 = int(box[0] * height), int(box[1] * width)
                image = image[
                    y0 : max(int(box[2] * height), y0 + 1), x0 : max(int(box[3] * width), x0 + 1)
                ]
            page_kp, page_des = self._orb.detectAndCompute(image, None)
            if page_des is None or len(page_kp) < MIN_GOOD_MATCHES:
                return None
            best: LogoHit | None = None
            for canonical, logo_kp, logo_des in self._logos:
                hit = self._score(canonical, logo_kp, logo_des, page_kp, page_des)
                if hit is not None and (
                    best is None
                    or (hit.inlier_ratio, hit.inliers) > (best.inlier_ratio, best.inliers)
                ):
                    best = hit
            return best
        except cv2.error:
            return None

    def _score(
        self, canonical: str, logo_kp: Any, logo_des: Any, page_kp: Any, page_des: Any
    ) -> LogoHit | None:
        pairs = self._bf.knnMatch(logo_des, page_des, k=2)
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < LOWE_RATIO * p[1].distance]
        if len(good) < MIN_GOOD_MATCHES:
            return None
        src = np.asarray([logo_kp[m.queryIdx].pt for m in good], dtype=np.float32).reshape(-1, 1, 2)
        dst = np.asarray([page_kp[m.trainIdx].pt for m in good], dtype=np.float32).reshape(-1, 1, 2)
        _, mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_PX)
        if mask is None:
            return None
        inliers = int(mask.sum())
        return LogoHit(canonical=canonical, inlier_ratio=inliers / len(good), inliers=inliers)
