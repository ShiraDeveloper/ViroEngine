"""LogoMatcherPort - contract for the ORB letterhead-logo fallback (PLAN §4 S5 step 6)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.core.models import Box, NormalizedPage, Strict


class LogoHit(Strict):
    """Best logo candidate on a page. `inlier_ratio` is RANSAC inliers / good matches, 0-1.
    The canonical name is only a claim until S5 checks it against the closed list."""

    canonical: str
    inlier_ratio: float
    inliers: int


@runtime_checkable
class LogoMatcherPort(Protocol):
    """Match the known institution logos against a region of a page."""

    def match(self, page: NormalizedPage, *, box: Box | None = None) -> LogoHit | None:
        """Best logo found inside `box` (whole page when None), or None.
        Never raises; unreadable images, missing logos and too few features all give None."""
        ...
