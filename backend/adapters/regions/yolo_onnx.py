"""YoloRegionDetector - RegionDetectorPort from a YOLOv8n ONNX model (PLAN §4 S4, T16).

The model is trained offline (``ultralytics``, AGPL-3.0, see ``.kiro/skills/train-yolo``) and
exported to int8 ONNX. This module imports ``onnxruntime`` only - never ``ultralytics`` - and a
test asserts it.

* **Lazy and cached.** The session is created on the first page, so a deployment that never
  selects ``yolo_onnx`` pays nothing.
* **Graceful absence.** A missing, unreadable, over-budget or failing model makes ``detect``
  return an empty list (logged once); ``YoloPreferred`` then serves the rule-based regions.
* **Per-class replacement.** YOLO may win on one class only (``yolo_kinds``): for those kinds
  its detections replace the rule-based ones; every other kind stays rule-based. A kind YOLO
  found nothing for keeps its rule-based regions - an empty answer never erases evidence.
* **Not a reader.** A detection is a *place*; its score is the detector's own confidence and is
  one evidence signal for S8, never a reason to accept a value.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray
import structlog

from backend.adapters.imaging.codec import decode_bgr
from backend.core.errors import RenderFailed
from backend.core.models import BarcodeHit, Box, NormalizedPage, OcrResult, Region, RegionKind
from backend.core.ports.region_detector import RegionDetectorPort
from backend.core.ports.storage import StoragePort
from backend.settings import Settings

log = structlog.get_logger()

DETECTOR_NAME: Final = "yolov8n-onnx"
# Class index -> region kind: the order the training script writes in data.yaml.
CLASSES: Final = (
    RegionKind.central_sticker,
    RegionKind.receipt_stamp,
    RegionKind.patient_box,
    RegionKind.commitment_label,
)
# Ultralytics pads the resized page with this grey (the value the model was trained with).
_PAD_VALUE: Final = 114
_BYTES_PER_MB: Final = 1e6

Floats = NDArray[np.float32]


class _Input(Protocol):
    name: str


class InferenceSession(Protocol):
    """The part of ``onnxruntime.InferenceSession`` this adapter uses."""

    def get_inputs(self) -> Sequence[_Input]: ...

    def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> list[Any]: ...


SessionFactory = Callable[[Path], InferenceSession]


def _onnx_session(path: Path) -> InferenceSession:
    """A CPU onnxruntime session for `path` (imported here, so tests need no runtime)."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.log_severity_level = 3  # errors only
    session: InferenceSession = ort.InferenceSession(
        str(path), sess_options=options, providers=["CPUExecutionProvider"]
    )
    return session


@dataclass(frozen=True)
class _Letterbox:
    """How a page was fitted into the square model input: scale and the padding (pixels)."""

    scale: float
    pad_x: float
    pad_y: float
    width: int
    height: int


def _letterbox(image: NDArray[np.uint8], side: int) -> tuple[Floats, _Letterbox]:
    """The page resized to fit `side` x `side` keeping its aspect, grey-padded, as a
    1x3xSxS float32 RGB tensor in 0..1."""
    height, width = image.shape[:2]
    scale = side / max(height, width)
    new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.full((side, side, 3), _PAD_VALUE, dtype=np.uint8)
    pad_x, pad_y = (side - new_w) // 2, (side - new_h) // 2
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
    rgb = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1), dtype=np.float32) / 255.0
    return rgb[None], _Letterbox(scale, float(pad_x), float(pad_y), width, height)


def _nms(boxes: Floats, scores: Floats, iou_max: float) -> list[int]:
    """Indices kept by greedy non-maximum suppression; `boxes` are (x1, y1, x2, y2)."""
    order = np.argsort(-scores)
    kept: list[int] = []
    while order.size:
        best = int(order[0])
        kept.append(best)
        if order.size == 1:
            break
        rest = order[1:]
        x1 = np.maximum(boxes[best, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[best, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[best, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[best, 3], boxes[rest, 3])
        inter = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        area_best = (boxes[best, 2] - boxes[best, 0]) * (boxes[best, 3] - boxes[best, 1])
        area_rest = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        union = area_best + area_rest - inter
        overlap = np.where(union > 0.0, inter / np.maximum(union, 1e-9), 0.0)
        order = rest[overlap <= iou_max]
    return kept


def decode_output(
    raw: NDArray[np.float32], box: _Letterbox, *, conf: float, iou: float
) -> list[Region]:
    """Regions from a YOLOv8 head output of shape (1, 4+C, N) or (1, N, 4+C), rows being
    (cx, cy, w, h, class scores...) in model-input pixels. Boxes are mapped back through the
    letterbox, clamped and normalised to (y_min, x_min, y_max, x_max). Raises ValueError for an
    output that is not a YOLOv8 head over the known classes."""
    out = np.asarray(raw, dtype=np.float32)
    if out.ndim != 3 or out.shape[0] != 1:
        raise ValueError("unexpected YOLO output rank")
    width = 4 + len(CLASSES)
    if out.shape[1] == width:
        rows = out[0].T
    elif out.shape[2] == width:
        rows = out[0]
    else:
        raise ValueError("unexpected YOLO output shape")
    class_scores = rows[:, 4:]
    best_class = np.argmax(class_scores, axis=1)
    best_score = class_scores[np.arange(rows.shape[0]), best_class]
    keep = best_score >= conf
    rows, best_class, best_score = rows[keep], best_class[keep], best_score[keep]
    regions: list[Region] = []
    for index, kind in enumerate(CLASSES):
        mask = best_class == index
        if not mask.any():
            continue
        cx, cy, w, h = (rows[mask, i] for i in range(4))
        xyxy = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
        scores = best_score[mask]
        for i in _nms(xyxy, scores, iou):
            x1, y1, x2, y2 = (float(v) for v in xyxy[i])
            x_min = (x1 - box.pad_x) / box.scale / box.width
            x_max = (x2 - box.pad_x) / box.scale / box.width
            y_min = (y1 - box.pad_y) / box.scale / box.height
            y_max = (y2 - box.pad_y) / box.scale / box.height
            region_box: Box = (
                min(max(y_min, 0.0), 1.0),
                min(max(x_min, 0.0), 1.0),
                min(max(y_max, 0.0), 1.0),
                min(max(x_max, 0.0), 1.0),
            )
            if region_box[2] <= region_box[0] or region_box[3] <= region_box[1]:
                continue
            regions.append(
                Region(
                    kind=kind,
                    box=region_box,
                    detector=DETECTOR_NAME,
                    score=min(max(float(scores[i]), 0.0), 1.0),
                )
            )
    regions.sort(key=lambda r: (r.kind.value, -r.score))
    return regions


class YoloRegionDetector:
    """Detect the four YOLO classes with the ONNX model; never raises."""

    name = DETECTOR_NAME

    def __init__(
        self,
        *,
        settings: Settings,
        storage: StoragePort,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._factory: SessionFactory = session_factory or _onnx_session
        self._session: InferenceSession | None = None
        self._input_name = ""
        self._unavailable = False

    @property
    def model_path(self) -> Path:
        """Where the model file is expected."""
        return Path(self._settings.models_dir) / self._settings.yolo_model_file

    @property
    def available(self) -> bool:
        """True once the model is loaded or can be: the file exists, fits the budget, loads."""
        return self._ensure_session() is not None

    def detect(
        self,
        page: NormalizedPage,
        ocr: OcrResult,
        barcodes: Sequence[BarcodeHit],
        *,
        commitment_labels: Sequence[str] = (),
    ) -> list[Region]:
        """Regions the model finds on the page's colour image, normalised. Empty when the model
        is unavailable, the image unreadable or inference fails. OCR text is not used."""
        session = self._ensure_session()
        if session is None:
            return []
        data = self._storage.get(page.color)
        if not data:
            return []
        try:
            image = decode_bgr(data)
        except RenderFailed:
            return []
        s = self._settings
        try:
            tensor, box = _letterbox(image, s.yolo_imgsz)
            outputs = session.run(None, {self._input_name: tensor})
            return decode_output(outputs[0], box, conf=s.yolo_conf, iou=s.yolo_iou)
        except (ValueError, IndexError, RuntimeError, cv2.error) as exc:
            log.warning("yolo_inference_failed", error_type=type(exc).__name__)
            return []

    # ------------------------------------------------------------------ internals

    def _ensure_session(self) -> InferenceSession | None:
        """The cached session, loading it on first use; None (remembered) when it cannot be."""
        if self._session is not None:
            return self._session
        if self._unavailable:
            return None
        path = self.model_path
        try:
            if not path.is_file():
                self._give_up("yolo_model_missing")
                return None
            if path.stat().st_size / _BYTES_PER_MB > self._settings.yolo_max_model_mb:
                self._give_up("yolo_model_over_budget")
                return None
            session = self._factory(path)
            self._input_name = session.get_inputs()[0].name
        except (OSError, RuntimeError, ValueError, IndexError, ImportError) as exc:
            self._give_up("yolo_model_unreadable", error_type=type(exc).__name__)
            return None
        self._session = session
        return session

    def _give_up(self, event: str, **fields: str) -> None:
        """Remember that the model cannot be used and log why, once."""
        self._unavailable = True
        log.warning(event, **fields)


class YoloPreferred:
    """YOLO where it is configured to win, the rule-based detector everywhere else and
    whenever the model is unavailable. Satisfies RegionDetectorPort."""

    name = DETECTOR_NAME

    def __init__(
        self, *, yolo: YoloRegionDetector, fallback: RegionDetectorPort, kinds: Sequence[str]
    ) -> None:
        self._yolo = yolo
        self._fallback = fallback
        self._kinds = frozenset(kinds)

    def detect(
        self,
        page: NormalizedPage,
        ocr: OcrResult,
        barcodes: Sequence[BarcodeHit],
        *,
        commitment_labels: Sequence[str] = (),
    ) -> list[Region]:
        """The rule-based regions, with each configured kind replaced by YOLO's when it found
        at least one. Never raises."""
        base = self._fallback.detect(page, ocr, barcodes, commitment_labels=commitment_labels)
        found = [r for r in self._yolo.detect(page, ocr, barcodes) if r.kind.value in self._kinds]
        replaced = {r.kind for r in found}
        return [r for r in base if r.kind not in replaced] + found
