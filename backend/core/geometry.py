"""Box geometry on normalized pages (PLAN §3.3: y_min, x_min, y_max, x_max in 0..1).

Normalized space is anisotropic - one unit is a page width horizontally and a page height
vertically - so every distance here is measured in pixels via the page size. A page whose
size is unknown (0) is treated as square, which keeps the arithmetic defined.
"""

from __future__ import annotations

from collections.abc import Iterable
import math

from backend.core.models import Box, NormalizedPage

Size = tuple[int, int]  # (width, height) in pixels


def page_size(page: NormalizedPage) -> Size:
    """Pixel size of the page frame; (1, 1) when the stored image reports none."""
    width, height = page.color.width, page.color.height
    return (width, height) if width > 0 and height > 0 else (1, 1)


def diagonal(size: Size) -> float:
    """Page diagonal in pixels."""
    return math.hypot(size[0], size[1])


def center(box: Box) -> tuple[float, float]:
    """(y, x) centre of a box."""
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def area(box: Box) -> float:
    """Normalized area; 0 for a degenerate box."""
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def union(boxes: Iterable[Box]) -> Box:
    """Smallest box containing all of `boxes`. Raises ValueError when there are none."""
    items = list(boxes)
    if not items:
        raise ValueError("union of no boxes")
    return (
        min(b[0] for b in items),
        min(b[1] for b in items),
        max(b[2] for b in items),
        max(b[3] for b in items),
    )


def clamp(box: Box) -> Box:
    """The box limited to the unit square."""
    return (
        min(max(box[0], 0.0), 1.0),
        min(max(box[1], 0.0), 1.0),
        min(max(box[2], 0.0), 1.0),
        min(max(box[3], 0.0), 1.0),
    )


def grow(
    box: Box, *, top: float = 0.0, left: float = 0.0, bottom: float = 0.0, right: float = 0.0
) -> Box:
    """The box pushed outward by the given normalized margins, limited to the page."""
    return clamp((box[0] - top, box[1] - left, box[2] + bottom, box[3] + right))


def contains(outer: Box, inner: Box) -> bool:
    """True when `inner` lies entirely inside `outer`."""
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def iou(a: Box, b: Box) -> float:
    """Intersection over union, 0 when the boxes do not overlap."""
    inter = area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))
    total = area(a) + area(b) - inter
    return inter / total if total > 0.0 else 0.0


def unrotate_box(box: Box, rotate_deg: int) -> Box:
    """Map a box of an image that was turned `rotate_deg` clockwise (0/90/180/270) back to
    the frame of the unrotated image. Both frames are normalized, so no pixel size is needed."""
    y0, x0, y1, x1 = box
    if rotate_deg == 90:  # point (u, v) of the rotated image was (v, 1 - u) before turning
        return (min(1 - x1, 1 - x0), min(y0, y1), max(1 - x1, 1 - x0), max(y0, y1))
    if rotate_deg == 180:
        return (1 - y1, 1 - x1, 1 - y0, 1 - x0)
    if rotate_deg == 270:  # point (u, v) of the rotated image was (1 - v, u) before turning
        return (min(x0, x1), min(1 - y1, 1 - y0), max(x0, x1), max(1 - y1, 1 - y0))
    return box


def from_crop(box: Box, crop: Box) -> Box:
    """Map a box given in the frame of a crop onto the page frame the crop was cut from."""
    height, width = crop[2] - crop[0], crop[3] - crop[1]
    return (
        crop[0] + box[0] * height,
        crop[1] + box[1] * width,
        crop[0] + box[2] * height,
        crop[1] + box[3] * width,
    )


def readable_line_box(box: Box, orientation_deg: int, *, reach: float, margin: float) -> Box:
    """Where a barcode's human-readable number is printed: the band just below the bars once
    the code is upright, on the page frame.

    `orientation_deg` is the clockwise turn that makes the code upright (BarcodeHit). The band
    runs `reach` bar-heights past the bars, overlaps them by `margin` bar-heights (a decoder's
    box is often a little short of the real bars) and is `margin` bar-lengths longer at each
    end. Limited to the page.
    """
    y0, x0, y1, x1 = box
    h, w = y1 - y0, x1 - x0
    if orientation_deg == 180:  # upside down: "below" is above on the page
        return clamp((y0 - reach * h, x0 - margin * w, y0 + margin * h, x1 + margin * w))
    if orientation_deg == 90:  # turning the page 90 clockwise maps page-right to down
        return clamp((y0 - margin * h, x1 - margin * w, y1 + margin * h, x1 + reach * w))
    if orientation_deg == 270:  # turning it 90 counter-clockwise maps page-left to down
        return clamp((y0 - margin * h, x0 - reach * w, y1 + margin * h, x0 + margin * w))
    return clamp((y1 - margin * h, x0 - margin * w, y1 + reach * h, x1 + margin * w))


def distance_to_box(point: tuple[float, float], box: Box, size: Size) -> float:
    """Pixel distance from a (y, x) point to a box; 0 when the point lies inside it."""
    dy = max(box[0] - point[0], 0.0, point[0] - box[2]) * size[1]
    dx = max(box[1] - point[1], 0.0, point[1] - box[3]) * size[0]
    return math.hypot(dx, dy)
