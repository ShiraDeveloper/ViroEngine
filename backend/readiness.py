"""Is the engine able to read a form at all? (PLAN §3.5: `OCR_UNAVAILABLE`.)

Without the Hebrew language pack Tesseract still runs and returns nothing useful: every field is
then "not found" and nothing says why. So readiness is checked as a fact: the configured packs
(`OCR_LANGS`, default `heb+eng`) must be installed. `/v1/health` reports it (503 when not ready) and
uploads are refused with 503 rather than accepted and silently wasted.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.container import Container
from backend.core.ports.ocr import OcrProbePort


@dataclass(frozen=True)
class OcrReadiness:
    """What the OCR engine offers against what the product needs."""

    ready: bool
    installed: list[str]
    missing: list[str]


def required_languages(container: Container) -> list[str]:
    """Language packs `OCR_LANGS` asks for ("heb+eng" -> ["heb", "eng"])."""
    return [lang for lang in container.settings.ocr_langs.split("+") if lang]


def ocr_readiness(container: Container) -> OcrReadiness:
    """Probe the printed-text reader. A reader that cannot list its packs is not ready; one with
    no probe (a test double) is assumed ready. Never raises."""
    reader = container.ocr_printed
    if not isinstance(reader, OcrProbePort):
        return OcrReadiness(ready=True, installed=[], missing=[])
    installed = reader.languages()
    if installed is None:
        return OcrReadiness(ready=False, installed=[], missing=required_languages(container))
    missing = [lang for lang in required_languages(container) if lang not in installed]
    return OcrReadiness(ready=not missing, installed=installed, missing=missing)
