---
name: train-yolo
description: Build the region-detection dataset from Safe Line gold boxes, train YOLOv8n on free Colab or Kaggle GPU, export and quantize to ONNX, and compare it against the rule-based detector. Also covers exporting TrOCR-small to ONNX. Use for Task 16 or when retraining the region detector.
---

# Train and export the region detector

The rule-based detector finds regions by reading labels. A visual detector does not depend on
reading the label, which is where the "works on an unseen layout" claim is weakest. This is a
**measured experiment with a pre-declared decision rule**, not a commitment.

> **Licence.** `ultralytics` is **AGPL-3.0**. It is used **only** here, offline, to train and
> export. The runtime image imports `onnxruntime` and never `ultralytics`. A test asserts this.
> The AGPL implication for any future closed-source deployment is disclosed in
> `docs/limitations.md`.

## Decision rule — declared before training

YOLO replaces the rule-based detector **only if all three** hold on the **held-out** pages:

1. Mean region IoU higher than the rule-based baseline recorded in Task 11.
2. Adds `< 400 ms` and `< 120 MB` resident memory per page.
3. End-to-end silent error does **not** increase.

Otherwise `config/models.yaml` keeps `rule_based` and YOLO moves to the cut list. A negative
result is written up in `docs/decisions/ADR-016-region-detector.md`, not hidden.

Per-class configuration is supported. If YOLO wins only on `receipt_stamp` — likely, it has a
consistent visual shape — use it for that class alone. This is the most probable good outcome.

## Step 1 — Build the dataset locally

`eval/gold.csv` holds values only, not boxes. Boxes come from `eval/results/gold_boxes.csv`, which
`python -m eval.make_gold --propose` writes next to the draft (one box per highlight, the patient
block merged). **It is a draft: review every box by hand before training** - the highlighter is
inconsistent and is only a proposal.

```bash
python -m eval.build_yolo_dataset --boxes eval/results/gold_boxes.csv --out eval/results/yolo
```

It refuses to run without `eval/held_out.txt`. It builds one image per page (no augmented
variants; ultralytics augments during training). Pages are the raw renders in
`eval/results/pages/`; some SAP pages are sideways there, so prefer normalised pages if you have them.

Four classes: `central_sticker`, `receipt_stamp`, `patient_box`, `commitment_label`.

Produces `eval/results/yolo/` with `images/{train,val}`, `labels/{train,val}` and `data.yaml`.

**Two rules the script enforces — verify them before training:**
- **Split by page, never by variant.** Augmented variants of one page in both train and val leak
  and produce a meaningless score.
- **Held-out pages are excluded entirely** from both splits.

```bash
python -c "
import pathlib
tr={p.stem.split('_')[0] for p in pathlib.Path('eval/results/yolo/images/train').glob('*')}
va={p.stem.split('_')[0] for p in pathlib.Path('eval/results/yolo/images/val').glob('*')}
ho=set(open('eval/held_out.txt').read().split())
print('overlap train/val:', tr & va)          # must be empty
print('held-out leaked:', (tr | va) & ho)     # must be empty
"
```

Upload `eval/results/yolo/` to Colab or Kaggle. It contains no personal data — only images of the
altered sample forms and box coordinates — but keep the notebook private anyway.

## Step 2 — Train on free GPU

Colab or Kaggle. Both give enough free GPU time for this.

```python
!pip -q install ultralytics==8.3.*

from ultralytics import YOLO
model = YOLO("yolov8n.pt")
model.train(
    data="/content/yolo/data.yaml",
    imgsz=1024,        # stickers are small relative to an A4 page; 640 loses them
    epochs=100,
    patience=20,       # early stopping on val mAP50
    batch=8,
    degrees=10,        # rotation IS real in this data, unlike most document sets
    translate=0.1, scale=0.3, fliplr=0.0,   # never flip: text would mirror
    project="safeline", name="regions",
)
print(model.val().box.map50)
```

Expect to watch `val/mAP50` per class. `receipt_stamp` and `central_sticker` should learn fastest;
`commitment_label` is the hardest because it is defined by text, not shape.

## Step 3 — Export and quantize

```python
model.export(format="onnx", opset=17, simplify=True, imgsz=1024)
```

```python
from onnxruntime.quantization import quantize_dynamic, QuantType
quantize_dynamic("safeline/regions/weights/best.onnx",
                 "yolov8n_regions_int8.onnx", weight_type=QuantType.QUInt8)
```

**Target: under 15 MB.** `yolov8n` is ~6 MB at fp32, so this is comfortable. Check before
downloading:

```python
import os; print(os.path.getsize("yolov8n_regions_int8.onnx") / 1e6, "MB")
```

## Step 4 — Install locally

```bash
mv ~/Downloads/yolov8n_regions_int8.onnx models/
sha256sum models/yolov8n_regions_int8.onnx   # record this in models/README.md
```

Record in `models/README.md`: the source model, the dataset commit, the training command, the
validation mAP50, the file size and the checksum. Weights are git-ignored, so provenance must live
in the repository.

Enable in `.env` (the runtime reads `Settings`; `config/models.yaml` mirrors the values):
```
REGION_DETECTOR=yolo_onnx        # or: rule_based (default)
YOLO_MODEL_FILE=yolov8n_regions_int8.onnx
YOLO_CONF=0.35
YOLO_IOU=0.45
YOLO_KINDS=receipt_stamp         # classes where YOLO replaces the rule-based regions
```
A missing or oversize model file falls back to `rule_based`.

## Step 5 — Compare and decide

```bash
# region IoU, latency and memory, on the HELD-OUT pages
python -m tools.demo regions --detector yolo_onnx --compare rule_based --split heldout

# end-to-end effect on silent error
python -m eval.run_eval --gold eval/gold.csv --split heldout --detector yolo_onnx
python -m eval.run_eval --gold eval/gold.csv --split heldout --detector rule_based
```

Apply the three-part rule. Write `docs/decisions/ADR-016-region-detector.md` with the numbers
either way, then set `config/models.yaml` accordingly.

## Appendix — Exporting TrOCR-small to ONNX (Task 13)

No training — export only, with the repository's own script so the file names, the graph inputs
and the vocabulary file match what `adapters/ocr/trocr_digits.py` loads. Any machine whose
network reaches the Hugging Face file CDN works (the development network blocks it: `418 Blocked
by NetFree`). On Colab/Kaggle, upload or clone the repository, then:

```python
!pip -q install torch transformers sentencepiece protobuf onnx==1.17.0 onnxruntime==1.20.1
!python -m tools.export_trocr --out models --revision b4648cfa171985a6745f37ddd637e98c0da958ac
```

It exports the encoder and the decoder (opset 17, decoder without a key/value cache — a digit
field is a dozen tokens), int8-quantizes both, writes `trocr_small_vocab.json`, and prints each
file's size and SHA-256. **It fails when the set reaches 80 MB**, the budget that fits the 512 MB
host alongside OpenCV and Tesseract (the adapter refuses a larger set at load, too).

Download the three files into `models/` and record the date, sizes and checksums in
`models/README.md`. `pytest backend/tests/unit/test_trocr.py::test_model_size_under_budget`
then runs instead of skipping.

**ASSUMPTION to test, not to trust.** TrOCR-small-handwritten is trained on Latin handwriting.
Digit glyphs are largely shared with Hebrew handwriting, so it should transfer for **numbers** and
not for Hebrew letters. This is measured in the Task 17 bake-off; if it loses to simply escalating
to the vision model, the reader is dropped per the cut list.
