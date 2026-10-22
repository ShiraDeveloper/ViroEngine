"""Build the YOLO region-detection dataset from reviewed gold boxes (PLAN T16).

    python -m eval.make_gold --propose            # writes eval/results/gold_boxes.csv (draft)
    # review gold_boxes.csv by hand, then:
    python -m eval.build_yolo_dataset --boxes eval/results/gold_boxes.csv --out eval/results/yolo

Layout written: ``images/{train,val}/<label>.png``, ``labels/{train,val}/<label>.txt`` (one
``class cx cy w h`` line per box, normalised) and ``data.yaml``.

Two rules, enforced and tested:

* **Split by page.** A page - with every box on it - lands wholly in train or wholly in val.
* **Held-out pages are excluded entirely.** The run refuses to start when no held-out list is
  known: it could not tell the dev pages from the held-out ones (project rule §10).

EVALUATION/TRAINING TOOLING. ``backend/core`` never imports this module. Output (stdout, logs)
holds counts and page labels only. The images are pages of the altered sample forms; keep the
folder private and out of git (``eval/results/`` is git-ignored).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
import shutil
import sys
from typing import Final

from backend.core.models import Box, RegionKind, Strict
from eval.calibrate import read_held_out
from eval.run_eval import DEFAULT_HELD_OUT, DEFAULT_RESULTS, EvalInputError

# Class index = line position; the runtime adapter (YoloRegionDetector.CLASSES) uses this order.
CLASSES: Final = (
    RegionKind.central_sticker,
    RegionKind.receipt_stamp,
    RegionKind.patient_box,
    RegionKind.commitment_label,
)
DEFAULT_BOXES: Final = DEFAULT_RESULTS / "gold_boxes.csv"
DEFAULT_PAGES: Final = DEFAULT_RESULTS / "pages"
DEFAULT_OUT: Final = DEFAULT_RESULTS / "yolo"
DEFAULT_VAL_SHARE: Final = 0.2
DEFAULT_SEED: Final = 20261022
REQUIRED_COLUMNS: Final = ("page_label", "kind", "y_min", "x_min", "y_max", "x_max")


class DatasetSummary(Strict):
    """What `build_dataset` wrote: pages per split and boxes per class."""

    train_pages: list[str]
    val_pages: list[str]
    skipped_held_out: list[str]
    skipped_no_image: list[str]
    boxes_per_class: dict[str, int]

    def render(self) -> str:
        counts = ", ".join(f"{k}={v}" for k, v in self.boxes_per_class.items())
        return (
            f"train {len(self.train_pages)} pages, val {len(self.val_pages)} pages, "
            f"held-out excluded {len(self.skipped_held_out)}, no image {len(self.skipped_no_image)}"
            f"; boxes: {counts}"
        )


def read_boxes(path: Path) -> dict[str, list[tuple[RegionKind, Box]]]:
    """Gold boxes by page label. Raises EvalInputError for a missing file or column, an unknown
    class or a box that is empty or outside the page."""
    if not path.is_file():
        raise EvalInputError(f"boxes file not found: {path.name}")
    pages: dict[str, list[tuple[RegionKind, Box]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = list(reader.fieldnames or [])
        for column in REQUIRED_COLUMNS:
            if column not in names:
                raise EvalInputError(f"{path.name} has no '{column}' column")
        for line, row in enumerate(reader, start=2):
            label = row["page_label"].strip()
            try:
                kind = RegionKind(row["kind"].strip())
                box = (
                    float(row["y_min"]),
                    float(row["x_min"]),
                    float(row["y_max"]),
                    float(row["x_max"]),
                )
            except ValueError as exc:
                raise EvalInputError(f"{path.name} line {line}: unusable kind or number") from exc
            if kind not in CLASSES:
                raise EvalInputError(f"{path.name} line {line}: '{kind.value}' is not a YOLO class")
            if not (0.0 <= box[0] < box[2] <= 1.0 and 0.0 <= box[1] < box[3] <= 1.0) or not label:
                raise EvalInputError(f"{path.name} line {line}: empty or out-of-page box")
            pages.setdefault(label, []).append((kind, box))
    return pages


def _rank(label: str, seed: int) -> str:
    """Portable pseudo-random order: a hash of seed and label, identical on every machine."""
    return hashlib.sha256(f"{seed}:{label}".encode()).hexdigest()


def split_pages(
    labels: list[str], *, val_share: float = DEFAULT_VAL_SHARE, seed: int = DEFAULT_SEED
) -> tuple[list[str], list[str]]:
    """(train labels, val labels): whole pages, ranked by a seeded hash; at least one page in
    each split when there are two or more. Raises EvalInputError for a share outside (0, 1)."""
    if not 0.0 < val_share < 1.0:
        raise EvalInputError("val share must be between 0 and 1")
    ranked = sorted(labels, key=lambda label: _rank(label, seed))
    n_val = min(max(1, round(len(ranked) * val_share)), len(ranked) - 1) if len(ranked) > 1 else 0
    return sorted(ranked[n_val:]), sorted(ranked[:n_val])


def yolo_line(kind: RegionKind, box: Box) -> str:
    """One label line: class index and the box as normalised centre x, centre y, width, height."""
    y_min, x_min, y_max, x_max = box
    cx, cy = (x_min + x_max) / 2.0, (y_min + y_max) / 2.0
    return f"{CLASSES.index(kind)} {cx:.6f} {cy:.6f} {x_max - x_min:.6f} {y_max - y_min:.6f}"


def build_dataset(
    boxes: Path,
    pages_dir: Path,
    out: Path,
    *,
    held_out: Path | None = DEFAULT_HELD_OUT,
    val_share: float = DEFAULT_VAL_SHARE,
    seed: int = DEFAULT_SEED,
) -> DatasetSummary:
    """Write the dataset under `out` (replacing earlier `images`, `labels` and `data.yaml`).
    Raises EvalInputError when the boxes file is unusable, no held-out list is known, or no page
    has both a box and an image."""
    held = read_held_out(held_out)
    if not held:
        raise EvalInputError(
            "no held-out list (eval/held_out.txt): cannot tell the dev pages from the held-out ones"
        )
    by_page = read_boxes(boxes)
    skipped_held = sorted(label for label in by_page if label in held)
    no_image = sorted(
        label
        for label in by_page
        if label not in held and not (pages_dir / f"{label}.png").is_file()
    )
    usable = sorted(label for label in by_page if label not in held and label not in no_image)
    if not usable:
        raise EvalInputError("no dev page has both a box and a rendered image")
    train, val = split_pages(usable, val_share=val_share, seed=seed)

    for sub in ("images", "labels"):
        shutil.rmtree(out / sub, ignore_errors=True)
    per_class = {kind.value: 0 for kind in CLASSES}
    for split, labels in (("train", train), ("val", val)):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        for label in labels:
            shutil.copyfile(pages_dir / f"{label}.png", out / "images" / split / f"{label}.png")
            lines = [yolo_line(kind, box) for kind, box in by_page[label]]
            for kind, _ in by_page[label]:
                per_class[kind.value] += 1
            (out / "labels" / split / f"{label}.txt").write_text(
                "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
            )
    names = "\n".join(f"  {i}: {kind.value}" for i, kind in enumerate(CLASSES))
    (out / "data.yaml").write_text(
        f"path: .\ntrain: images/train\nval: images/val\nnames:\n{names}\n",
        encoding="utf-8",
        newline="\n",
    )
    return DatasetSummary(
        train_pages=train,
        val_pages=val,
        skipped_held_out=skipped_held,
        skipped_no_image=no_image,
        boxes_per_class=per_class,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Prints counts only. Exit 2 on unusable input."""
    p = argparse.ArgumentParser(description="Build the YOLO region dataset from gold boxes.")
    p.add_argument("--boxes", "--gold", type=Path, default=DEFAULT_BOXES, dest="boxes")
    p.add_argument("--pages", type=Path, default=DEFAULT_PAGES)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--held-out", type=Path, default=DEFAULT_HELD_OUT)
    p.add_argument("--val-share", type=float, default=DEFAULT_VAL_SHARE)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = p.parse_args(argv)
    try:
        summary = build_dataset(
            args.boxes,
            args.pages,
            args.out,
            held_out=args.held_out,
            val_share=args.val_share,
            seed=args.seed,
        )
    except EvalInputError as exc:
        sys.stderr.write(f"{exc.code}: {exc}\n")
        return 2
    sys.stdout.write(summary.render() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
