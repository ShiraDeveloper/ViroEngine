"""TesseractAdapter - OcrPort implemented with the local Tesseract engine (PLAN §4 S3).

Words come back from Tesseract's TSV output with their confidence, normalized to 0..1 of the
image they were read from. Every word is returned, including low-confidence ones: they are
positional evidence for S4, and the decision to drop them from the text stream is S3's.
Nothing here ever raises; an unreadable image or a failing engine yields an empty OcrResult.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
import shutil
from typing import Final

import cv2
from PIL import Image
import pytesseract
from pytesseract import Output
import structlog

from backend.adapters.imaging.codec import Pixels, decode_bgr
from backend.core.errors import RenderFailed
from backend.core.models import Box, ImageRef, OcrResult, OcrWord, ReaderName
from backend.core.ports.storage import StoragePort
from backend.settings import Settings

log = structlog.get_logger()

# Tesseract inserts Unicode direction marks around right-to-left words. They are invisible,
# have no place in a token, and would break every comparison with a plain string.
_BIDI_MARKS: Final = dict.fromkeys(
    [0x200E, 0x200F, 0x061C, *range(0x202A, 0x202F), *range(0x2066, 0x206A)]
)

# TSV columns (Tesseract `image_to_data`): level page block par line word left top width
# height conf text. Only level 5 rows are words.
_WORD_LEVEL: Final = "5"
_COL_LEFT, _COL_TOP, _COL_WIDTH, _COL_HEIGHT, _COL_CONF, _COL_TEXT = 6, 7, 8, 9, 10, 11

_ROTATIONS: Final = (0, 90, 180, 270)
# Languages of a digits-only crop: the whitelist already pins the alphabet, and Hebrew data
# would only add confusions.
_CROP_WHITELIST_LANGS: Final = "eng"

_ENGINE_ERRORS: Final = (
    pytesseract.TesseractError,
    pytesseract.TesseractNotFoundError,
    RuntimeError,
    OSError,
)


def clean_token(text: str) -> str:
    """The token without direction marks or surrounding whitespace."""
    return text.translate(_BIDI_MARKS).strip()


def parse_tsv(tsv: str, width: int, height: int) -> list[OcrWord]:
    """Word boxes from Tesseract TSV, normalized to 0..1 of a `width` x `height` image.

    Confidence is Tesseract's 0..100 divided by 100. Rows that are not words, have no text or
    have an empty box are skipped. Never raises; malformed rows are skipped.
    """
    if width <= 0 or height <= 0:
        return []
    words: list[OcrWord] = []
    reader = csv.reader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        if len(row) <= _COL_TEXT or row[0] != _WORD_LEVEL:
            continue
        text = clean_token(row[_COL_TEXT])
        try:
            left, top = int(row[_COL_LEFT]), int(row[_COL_TOP])
            box_w, box_h = int(row[_COL_WIDTH]), int(row[_COL_HEIGHT])
            conf = float(row[_COL_CONF])
        except ValueError:
            continue
        if not text or conf < 0.0 or box_w <= 0 or box_h <= 0:
            continue
        box: Box = (
            min(max(top / height, 0.0), 1.0),
            min(max(left / width, 0.0), 1.0),
            min(max((top + box_h) / height, 0.0), 1.0),
            min(max((left + box_w) / width, 0.0), 1.0),
        )
        words.append(OcrWord(text=text, box=box, confidence=min(conf / 100.0, 1.0)))
    return words


class TesseractAdapter:
    """Read pages and crops with Tesseract."""

    name = "tesseract"

    def __init__(self, *, settings: Settings, storage: StoragePort) -> None:
        self._settings = settings
        self._storage = storage
        self._languages: list[str] | None = None
        configured = settings.tesseract_cmd
        # A container path configured for another OS must not disable OCR on a dev machine.
        pytesseract.pytesseract.tesseract_cmd = (
            configured if Path(configured).exists() else (shutil.which("tesseract") or configured)
        )

    # ------------------------------------------------------------------ OcrProbePort

    def languages(self) -> list[str] | None:
        """Language packs Tesseract can load, or None when the binary cannot be run. The answer is
        kept once it is known: packs do not appear while the process runs."""
        if self._languages is not None:
            return list(self._languages)
        try:
            found = sorted(pytesseract.get_languages(config=""))
        except (*_ENGINE_ERRORS, OSError):
            return None
        self._languages = found
        return list(found)

    # ------------------------------------------------------------------ OcrPort

    def read_page(self, image: ImageRef, langs: str = "heb+eng") -> OcrResult:
        """Read a full page with the configured oem/psm; empty result on any failure."""
        config = f"--oem {self._settings.tesseract_oem} --psm {self._settings.tesseract_psm}"
        return self._read(image, langs, config)

    def read_crop(
        self,
        image: ImageRef,
        *,
        whitelist: str | None = None,
        psm: int = 7,
        langs: str | None = None,
    ) -> OcrResult:
        """Read a field crop (single line by default), optionally limited to `whitelist`."""
        config = f"--oem {self._settings.tesseract_oem} --psm {psm}"
        if whitelist:
            config += f" -c tessedit_char_whitelist={whitelist}"
        chosen = langs or (_CROP_WHITELIST_LANGS if whitelist else self._settings.ocr_langs)
        result = self._read(image, chosen, config)
        return result.model_copy(update={"candidates": [result.text] if result.text else []})

    def detect_orientation(self, image: ImageRef) -> int | None:
        """Clockwise rotation that makes the text upright, or None when OSD cannot tell."""
        pixels = self._load(image)
        if pixels is None:
            return None
        rotation = self._osd(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB))
        if rotation is None:
            # A blurred or washed-out scan: OSD often abstains on the page as it is but reads an
            # adaptively binarized copy (strokes regain contrast). Sharpening was tried and
            # rejected: on heavy blur it made OSD confidently wrong.
            grey = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
            binary = cv2.adaptiveThreshold(
                grey, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 12
            )
            rotation = self._osd(binary)
        return rotation

    def _osd(self, pixels: Pixels) -> int | None:
        """OSD's clockwise rotation for an RGB or grey image, or None when it is not sure."""
        try:
            osd = pytesseract.image_to_osd(
                Image.fromarray(pixels),
                config="--psm 0",
                output_type=Output.DICT,
                timeout=self._settings.ocr_timeout_seconds,
            )
            rotation = int(osd["rotate"])
            confidence = float(osd["orientation_conf"])
        except (*_ENGINE_ERRORS, KeyError, ValueError) as exc:
            # "Too few characters" on a sparse page is the common, expected case.
            log.debug("osd_abstained", error_type=type(exc).__name__)
            return None
        if confidence < self._settings.osd_min_confidence or rotation not in _ROTATIONS:
            return None
        return rotation

    # ------------------------------------------------------------------ internals

    def _load(self, image: ImageRef) -> Pixels | None:
        """Decoded BGR pixels, or None when the artefact is missing or not an image."""
        data = self._storage.get(image)
        if not data:
            return None
        try:
            return decode_bgr(data)
        except RenderFailed:
            return None

    def _read(self, image: ImageRef, langs: str, config: str) -> OcrResult:
        """Run Tesseract and parse its TSV; an empty OcrResult when anything fails."""
        empty = OcrResult(reader=ReaderName.tesseract, words=[], text="", candidates=[])
        pixels = self._load(image)
        if pixels is None:
            return empty
        height, width = pixels.shape[:2]
        try:
            tsv = pytesseract.image_to_data(
                Image.fromarray(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)),
                lang=langs,
                config=config,
                output_type=Output.STRING,
                timeout=self._settings.ocr_timeout_seconds,
            )
        except _ENGINE_ERRORS as exc:
            log.warning("ocr_failed", page_id=image.page_id, error_type=type(exc).__name__)
            return empty
        words = parse_tsv(tsv, width, height)
        return OcrResult(
            reader=ReaderName.tesseract,
            words=words,
            text=" ".join(w.text for w in words),
            candidates=[],
        )
