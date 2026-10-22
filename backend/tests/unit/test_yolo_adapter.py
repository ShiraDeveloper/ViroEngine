"""Tests for the YOLO region detector (T16): the ONNX adapter and the dataset builder.

The ONNX session is a plain fake that returns a scripted YOLOv8 head, so decoding, NMS, the
letterbox mapping, lazy loading and the fallbacks are exercised without a model file (git-ignored)
or onnxruntime. Images and boxes are synthetic: no reference/ data."""

from __future__ import annotations

import ast
import csv
from dataclasses import dataclass, field
import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
import pytest

from backend.adapters.fakes import FakeStorage
from backend.adapters.regions.yolo_onnx import (
    CLASSES,
    YoloPreferred,
    YoloRegionDetector,
    decode_output,
)
from backend.core.models import (
    Box,
    FieldKey,
    NormalizedPage,
    OcrResult,
    PageMeasurement,
    PageRaw,
    ReaderName,
    Region,
    RegionKind,
)
from backend.core.ports.region_detector import RegionDetectorPort
from backend.settings import Settings
from eval.build_yolo_dataset import build_dataset, read_boxes, split_pages, yolo_line
from eval.make_gold import draft_region_boxes
from eval.run_eval import EvalInputError

REPO_ROOT = Path(__file__).resolve().parents[3]
PAGE_W, PAGE_H = 128, 64  # a 2:1 page; at imgsz 64 the letterbox scale is 0.5, pad_y 16
SIDE = 64


@dataclass
class _Input:
    name: str = "images"


@dataclass
class FakeSession:
    """Returns a (1, 8, 4) head: a sticker (twice, overlapping), a weak stamp, a patient box."""

    calls: list[tuple[int, ...]] = field(default_factory=list)

    def get_inputs(self) -> list[_Input]:
        return [_Input()]

    def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> list[Any]:
        tensor = input_feed["images"]
        self.calls.append(tuple(tensor.shape))
        assert (
            tensor.dtype == np.float32 and 0.0 <= float(tensor.min()) <= float(tensor.max()) <= 1.0
        )
        # columns: cx, cy, w, h (model pixels), then the four class scores
        anchors = [
            (32, 32, 16, 8, 0.90, 0.0, 0.0, 0.0),  # central_sticker
            (33, 32, 16, 8, 0.80, 0.0, 0.0, 0.0),  # same sticker again: removed by NMS
            (10, 32, 8, 8, 0.0, 0.10, 0.0, 0.0),  # receipt_stamp below the threshold
            (50, 24, 10, 6, 0.0, 0.0, 0.70, 0.0),  # patient_box
        ]
        return [np.array(anchors, dtype=np.float32).T[None]]


@dataclass
class CountingFactory:
    session: FakeSession = field(default_factory=FakeSession)
    paths: list[str] = field(default_factory=list)

    def __call__(self, path: Path) -> Any:
        self.paths.append(path.name)
        return self.session


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None, models_dir=str(tmp_path), data_dir=str(tmp_path), yolo_imgsz=SIDE
    )


@pytest.fixture
def installed(settings: Settings) -> Settings:
    """A placeholder model file in place (the fake factory never reads it)."""
    (Path(settings.models_dir) / settings.yolo_model_file).write_bytes(b"\0" * 1024)
    return settings


def _png(width: int = PAGE_W, height: int = PAGE_H) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _page(storage: FakeStorage) -> NormalizedPage:
    ref = storage.put_page_image("job", "p1", _png(), "color")
    raw = PageRaw(page_id="p1", source_file="f.pdf", page_index=0, sha256="0" * 64, image=ref)
    measurement = PageMeasurement(
        rotation_deg=0, rotation_votes={}, skew_deg=0.0, blur_score=100.0,
        illumination_cv=0.1, noise_score=0.0, is_photo=False, needs=[],
    )  # fmt: skip
    return NormalizedPage(
        raw=raw, measurement=measurement, corrections_applied=[], color=ref, gray_bin=ref, thumb=ref
    )


def _ocr() -> OcrResult:
    return OcrResult(reader=ReaderName.tesseract, words=[], text="")


@dataclass
class FixedDetector:
    """A rule-based stand-in returning the same regions every time."""

    regions: list[Region]
    name: str = "rule-based"

    def detect(
        self, page: NormalizedPage, ocr: OcrResult, barcodes: Any, *, commitment_labels: Any = ()
    ) -> list[Region]:
        return list(self.regions)


def _region(kind: RegionKind, detector: str = "rule-based") -> Region:
    return Region(kind=kind, box=(0.0, 0.0, 0.1, 0.1), detector=detector, score=0.5)


# --------------------------------------------------------------------------- adapter


def test_detect_returns_normalized_boxes(installed: Settings) -> None:
    storage = FakeStorage()
    detector = YoloRegionDetector(
        settings=installed, storage=storage, session_factory=CountingFactory()
    )
    regions = detector.detect(_page(storage), _ocr(), [])
    by_kind = {r.kind: r for r in regions}
    # The weak stamp is dropped (0.10 < 0.35); the duplicated sticker is merged by NMS.
    assert sorted(k.value for k in by_kind) == ["central_sticker", "patient_box"]
    assert len(regions) == 2
    sticker = by_kind[RegionKind.central_sticker]
    assert sticker.detector == "yolov8n-onnx" and sticker.score == pytest.approx(0.90)
    # cx=32 w=16 -> x 24..40 px; cy=32 h=8 minus pad_y 16 -> y 12..20 px; scale 0.5.
    assert sticker.box == pytest.approx((24 / 64, 48 / 128, 40 / 64, 80 / 128))
    for region in regions:
        y0, x0, y1, x1 = region.box
        assert 0.0 <= y0 < y1 <= 1.0 and 0.0 <= x0 < x1 <= 1.0


def test_detect_clamps_box_that_leaves_the_page() -> None:
    letterbox = _letterbox_for(PAGE_W, PAGE_H)
    head = np.array([[32, 32, 200, 200, 0.9, 0, 0, 0]], dtype=np.float32).T[None]
    (region,) = decode_output(head, letterbox, conf=0.35, iou=0.45)
    assert region.box == (0.0, 0.0, 1.0, 1.0)


def _letterbox_for(width: int, height: int) -> Any:
    from backend.adapters.regions.yolo_onnx import _letterbox

    return _letterbox(np.full((height, width, 3), 255, np.uint8), SIDE)[1]


@pytest.mark.parametrize("shape", [(1, 7, 4), (2, 8, 4), (8, 4)])
def test_decode_rejects_unexpected_output_shape(shape: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        decode_output(
            np.zeros(shape, np.float32), _letterbox_for(PAGE_W, PAGE_H), conf=0.3, iou=0.4
        )


def test_decode_accepts_transposed_head() -> None:
    head = np.array([[32, 32, 16, 8, 0.9, 0, 0, 0]], dtype=np.float32)[None]  # (1, N, 8)
    (region,) = decode_output(head, _letterbox_for(PAGE_W, PAGE_H), conf=0.35, iou=0.45)
    assert region.kind is RegionKind.central_sticker


def test_adapter_satisfies_port(installed: Settings) -> None:
    storage = FakeStorage()
    yolo = YoloRegionDetector(settings=installed, storage=storage)
    preferred = YoloPreferred(yolo=yolo, fallback=FixedDetector([]), kinds=["receipt_stamp"])
    assert isinstance(yolo, RegionDetectorPort) and yolo.name == "yolov8n-onnx"
    assert isinstance(preferred, RegionDetectorPort)


def test_classes_match_dataset_order() -> None:
    from eval.build_yolo_dataset import CLASSES as TRAINING_CLASSES

    assert CLASSES == TRAINING_CLASSES


def test_runtime_does_not_import_ultralytics() -> None:
    """AGPL-3.0: ultralytics trains and exports offline; nothing under backend/ may import it."""
    offenders: list[str] = []
    for path in (REPO_ROOT / "backend").rglob("*.py"):
        if path == Path(__file__):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(n == "ultralytics" or n.startswith("ultralytics.") for n in names):
                offenders.append(path.name)
    assert offenders == []
    for listing in ("requirements.txt", "pyproject.toml"):
        assert "ultralytics" not in (REPO_ROOT / listing).read_text(encoding="utf-8").lower()


def test_missing_model_file_degrades_gracefully(settings: Settings) -> None:
    """No model file: the adapter finds nothing and the composite serves the rule-based regions."""
    storage = FakeStorage()
    base = [_region(RegionKind.receipt_stamp), _region(RegionKind.clinical)]
    yolo = YoloRegionDetector(settings=settings, storage=storage, session_factory=CountingFactory())
    preferred = YoloPreferred(yolo=yolo, fallback=FixedDetector(base), kinds=["receipt_stamp"])
    page = _page(storage)
    assert yolo.detect(page, _ocr(), []) == []
    assert not yolo.available
    assert preferred.detect(page, _ocr(), []) == base


def test_over_budget_model_is_refused(settings: Settings) -> None:
    tight = settings.model_copy(update={"yolo_max_model_mb": 0.0005})
    (Path(tight.models_dir) / tight.yolo_model_file).write_bytes(b"\0" * 1024)
    factory = CountingFactory()
    detector = YoloRegionDetector(settings=tight, storage=FakeStorage(), session_factory=factory)
    assert not detector.available
    assert factory.paths == []  # never loaded


def test_failing_session_degrades_to_empty(installed: Settings) -> None:
    @dataclass
    class Broken(FakeSession):
        def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> list[Any]:
            raise RuntimeError("inference failed")

    storage = FakeStorage()
    detector = YoloRegionDetector(
        settings=installed, storage=storage, session_factory=CountingFactory(session=Broken())
    )
    assert detector.detect(_page(storage), _ocr(), []) == []


def test_model_loads_lazily_and_once(installed: Settings) -> None:
    storage = FakeStorage()
    factory = CountingFactory()
    detector = YoloRegionDetector(settings=installed, storage=storage, session_factory=factory)
    assert factory.paths == []
    page = _page(storage)
    detector.detect(page, _ocr(), [])
    detector.detect(page, _ocr(), [])
    assert factory.paths == [installed.yolo_model_file]
    assert factory.session.calls == [(1, 3, SIDE, SIDE)] * 2


def test_preferred_replaces_only_configured_kinds(installed: Settings) -> None:
    storage = FakeStorage()
    base = [
        _region(RegionKind.central_sticker),
        _region(RegionKind.patient_box),
        _region(RegionKind.clinical),
    ]
    yolo = YoloRegionDetector(
        settings=installed, storage=storage, session_factory=CountingFactory()
    )
    preferred = YoloPreferred(yolo=yolo, fallback=FixedDetector(base), kinds=["central_sticker"])
    regions = preferred.detect(_page(storage), _ocr(), [])
    kinds = sorted((r.kind.value, r.detector) for r in regions)
    assert kinds == [
        ("central_sticker", "yolov8n-onnx"),  # replaced
        ("clinical", "rule-based"),  # not a YOLO class: untouched
        ("patient_box", "rule-based"),  # YOLO found one, but the kind is not configured
    ]


def test_preferred_keeps_rule_based_kind_yolo_did_not_find(installed: Settings) -> None:
    storage = FakeStorage()
    base = [_region(RegionKind.receipt_stamp)]
    yolo = YoloRegionDetector(
        settings=installed, storage=storage, session_factory=CountingFactory()
    )
    preferred = YoloPreferred(yolo=yolo, fallback=FixedDetector(base), kinds=["receipt_stamp"])
    assert preferred.detect(_page(storage), _ocr(), []) == base  # the weak stamp was dropped


def test_onnx_model_under_size_budget() -> None:
    """The exported model (when installed in ./models) fits the 15 MB budget."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    model = Path(settings.models_dir) / settings.yolo_model_file
    if not model.is_file():
        pytest.skip("YOLO model not installed (git-ignored; see models/README.md)")
    assert model.stat().st_size / 1e6 < settings.yolo_max_model_mb


def test_default_detector_is_rule_based() -> None:
    assert Settings(_env_file=None).region_detector == "rule_based"  # type: ignore[call-arg]


# --------------------------------------------------------------------------- dataset


def _boxes_csv(path: Path, labels: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["page_label", "kind", "y_min", "x_min", "y_max", "x_max", "source"])
        for label in labels:
            writer.writerow([label, "central_sticker", 0.1, 0.2, 0.3, 0.6, "highlight"])
            writer.writerow([label, "receipt_stamp", 0.5, 0.5, 0.6, 0.9, "highlight"])


def _workspace(tmp_path: Path, labels: list[str], held: list[str]) -> tuple[Path, Path, Path]:
    pages = tmp_path / "pages"
    pages.mkdir()
    for label in labels:
        (pages / f"{label}.png").write_bytes(_png())
    boxes = tmp_path / "gold_boxes.csv"
    _boxes_csv(boxes, labels)
    held_out = tmp_path / "held_out.txt"
    held_out.write_text("\n".join(held) + "\n", encoding="utf-8")
    return boxes, pages, held_out


LABELS = [f"A{i}" for i in range(1, 21)]


def test_dataset_split_is_by_page(tmp_path: Path) -> None:
    boxes, pages, held = _workspace(tmp_path, LABELS, ["A1", "A2", "A3"])
    out = tmp_path / "yolo"
    summary = build_dataset(boxes, pages, out, held_out=held)
    train = {p.stem for p in (out / "images" / "train").glob("*.png")}
    val = {p.stem for p in (out / "images" / "val").glob("*.png")}
    assert train and val and not train & val
    assert train == set(summary.train_pages) and val == set(summary.val_pages)
    # Every page's label file sits beside its image, with all of its boxes, in one split only.
    for split, labels in (("train", train), ("val", val)):
        for label in labels:
            lines = (
                (out / "labels" / split / f"{label}.txt").read_text(encoding="utf-8").split("\n")
            )
            assert len([ln for ln in lines if ln]) == 2
            assert not (
                out / "labels" / ("val" if split == "train" else "train") / f"{label}.txt"
            ).exists()


def test_held_out_excluded_from_training_set(tmp_path: Path) -> None:
    held_labels = ["A1", "A2", "A3"]
    boxes, pages, held = _workspace(tmp_path, LABELS, held_labels)
    out = tmp_path / "yolo"
    summary = build_dataset(boxes, pages, out, held_out=held)
    written = {p.stem for p in out.rglob("*") if p.suffix in {".png", ".txt"}}
    assert not written & set(held_labels)
    assert summary.skipped_held_out == held_labels
    assert len(summary.train_pages) + len(summary.val_pages) == len(LABELS) - len(held_labels)


def test_dataset_refuses_without_a_held_out_list(tmp_path: Path) -> None:
    boxes, pages, _ = _workspace(tmp_path, LABELS, [])
    with pytest.raises(EvalInputError):
        build_dataset(boxes, pages, tmp_path / "yolo", held_out=tmp_path / "missing.txt")


def test_dataset_split_is_deterministic() -> None:
    assert split_pages(LABELS) == split_pages(list(reversed(LABELS)))
    train, val = split_pages(LABELS, val_share=0.25)
    assert len(val) == 5 and sorted(train + val) == sorted(LABELS)


def test_data_yaml_lists_the_four_classes(tmp_path: Path) -> None:
    boxes, pages, held = _workspace(tmp_path, LABELS, ["A1"])
    out = tmp_path / "yolo"
    build_dataset(boxes, pages, out, held_out=held)
    text = (out / "data.yaml").read_text(encoding="utf-8")
    for index, kind in enumerate(CLASSES):
        assert f"  {index}: {kind.value}" in text


def test_yolo_line_is_normalized_centre_format() -> None:
    line = yolo_line(RegionKind.receipt_stamp, (0.5, 0.5, 0.6, 0.9))
    cls, cx, cy, w, h = line.split()
    assert cls == "1"
    assert (float(cx), float(cy)) == pytest.approx((0.7, 0.55))
    assert (float(w), float(h)) == pytest.approx((0.4, 0.1))


@pytest.mark.parametrize(
    "row",
    [
        ["A1", "table", "0.1", "0.1", "0.2", "0.2"],  # not a YOLO class
        ["A1", "central_sticker", "0.3", "0.1", "0.2", "0.2"],  # inverted
        ["A1", "central_sticker", "0.1", "0.1", "1.2", "0.2"],  # off the page
        ["A1", "central_sticker", "x", "0.1", "0.2", "0.2"],  # not a number
    ],
)
def test_read_boxes_rejects_bad_rows(tmp_path: Path, row: list[str]) -> None:
    path = tmp_path / "boxes.csv"
    path.write_text(
        "page_label,kind,y_min,x_min,y_max,x_max\n" + ",".join(row) + "\n", encoding="utf-8"
    )
    with pytest.raises(EvalInputError):
        read_boxes(path)


def test_draft_boxes_merge_the_patient_block() -> None:
    placed: list[tuple[FieldKey, Box]] = [
        (FieldKey.first_name, (0.10, 0.50, 0.12, 0.60)),
        (FieldKey.last_name, (0.10, 0.20, 0.12, 0.40)),
        (FieldKey.national_id, (0.14, 0.30, 0.16, 0.55)),
        (FieldKey.central_number, (0.50, 0.10, 0.55, 0.30)),
        (FieldKey.sender, (0.01, 0.01, 0.05, 0.30)),  # no region class: no row
    ]
    rows = draft_region_boxes("A7", placed)
    assert sorted(r["kind"] for r in rows) == ["central_sticker", "patient_box"]
    patient = next(r for r in rows if r["kind"] == "patient_box")
    assert (patient["y_min"], patient["x_min"], patient["y_max"], patient["x_max"]) == (
        "0.100000",
        "0.200000",
        "0.160000",
        "0.600000",
    )
    assert all(r["page_label"] == "A7" and r["source"] == "highlight" for r in rows)
