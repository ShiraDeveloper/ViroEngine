"""BarcodePort — contract for all barcode decoders.

Any symbology, any payload length. MUST NOT filter by length (§2.1, §3 rule).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.core.models import BarcodeHit, ImageRef


@runtime_checkable
class BarcodePort(Protocol):
    """Decode barcodes from an image at multiple orientations.

    name: recorded in the reading trail.
    """

    name: str

    def sweep(
        self,
        image: ImageRef,
        orientations: tuple[int, ...] = (0, 90, 180, 270),
    ) -> list[BarcodeHit]:
        """Decode all barcodes visible at any of the given orientations (degrees clockwise).

        BarcodeHit.orientation_deg: the clockwise rotation of the page that makes the code upright.
        Returns every hit found. Any symbology, any payload length — never filtered.
        Returns an empty list when no barcode is found. Never raises.
        """
        ...
