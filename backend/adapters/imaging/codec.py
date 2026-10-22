"""PNG encode/decode shared by the imaging adapters. Images travel as PNG bytes."""

from __future__ import annotations

from typing import Any, Final

import cv2
import numpy as np
from numpy.typing import NDArray

from backend.core.errors import RenderFailed
from backend.core.ports.document import EncodedImage

# zlib level for PNG output. PNG is lossless at every level; 3 trades a little size for
# the speed that keeps a 300 DPI page well under a second to encode.
PNG_COMPRESSION: Final = 3

Pixels = NDArray[np.uint8]


def decode_bgr(data: bytes) -> Pixels:
    """Decode image bytes into a BGR uint8 array. Raises RenderFailed if they are not an image."""
    try:
        image: Any = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise RenderFailed("image could not be decoded") from exc
    if image is None or image.size == 0:
        raise RenderFailed("image could not be decoded")
    return np.asarray(image, dtype=np.uint8)


def encode_png(image: Pixels) -> EncodedImage:
    """Encode a grey (H, W) or BGR (H, W, 3) array as PNG. Raises RenderFailed on failure."""
    try:
        ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, PNG_COMPRESSION])
    except cv2.error as exc:
        raise RenderFailed("image could not be encoded") from exc
    if not ok:
        raise RenderFailed("image could not be encoded")
    height, width = image.shape[:2]
    return EncodedImage(data=encoded.tobytes(), width=int(width), height=int(height))


def encode_jpeg(image: Pixels, quality: int) -> EncodedImage:
    """Encode as JPEG at `quality` (1-100). Used for the page image sent to a vision model, where
    it is a third of the PNG size at no loss a model can see. Raises RenderFailed on failure."""
    try:
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    except cv2.error as exc:
        raise RenderFailed("image could not be encoded") from exc
    if not ok:
        raise RenderFailed("image could not be encoded")
    height, width = image.shape[:2]
    return EncodedImage(data=encoded.tobytes(), width=int(width), height=int(height))
