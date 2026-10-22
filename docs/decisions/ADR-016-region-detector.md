# ADR-016 - Region detector: rule-based or YOLOv8n
- **Status:** open - the experiment is prepared but **not run**; no decision is made
- **Date:** 2026-10-22
- **Deciders:** Ahuba, Shira
- **Task:** T16

## Context

S4 finds the regions a field is read from. The rule-based detector locates them by reading the
labels around them. The T11 measurement on the 48 sample pages shows where that is weak: 1 receipt
stamp in 48 and no handwritten central number on SAP screens (see `docs/limitations.md`). A visual
detector does not need to read a label, so ADR-012 treats YOLOv8n as a measured experiment.

## Decision rule (declared before training, ADR-012)

YOLO replaces the rule-based detector only if **all three** hold on the **held-out** pages:

1. mean region IoU higher than the rule-based baseline;
2. it adds less than 400 ms and 120 MB resident memory per page;
3. end-to-end silent error does not increase.

Per class: if YOLO wins on one class only (most likely `receipt_stamp`), `YOLO_KINDS` lists just that
class and every other kind stays rule-based.

## What exists

| Piece | State |
|---|---|
| `eval/make_gold.py --propose` writes `gold_boxes.csv` | done: **draft** boxes from the highlighter, one per highlight, the patient block merged. A human reviews them. |
| `eval/build_yolo_dataset.py` | done: split by page, held-out excluded, refuses to run without a held-out list. |
| `backend/adapters/regions/yolo_onnx.py` | done: ONNX Runtime only, lazy, size-budgeted, falls back to rule-based, per-class replacement. |
| Selection | `REGION_DETECTOR=rule_based` (default) or `yolo_onnx` in `.env`. |
| Trained model | **none.** |
| Held-out numbers | **none.** |

Everything above is unit-tested with a fake ONNX session. None of it shows that a trained model would
help; it shows only that the machinery works.

## Why no decision

- The boxes are drafts. They come from a yellow highlighter that is inconsistent across the samples
  (project rule §4), so they must be reviewed before they train anything.
- `eval/gold.csv` and `eval/held_out.txt` do not exist yet (T7 labelling is outstanding), so the
  held-out pages cannot be excluded, and the dataset builder refuses to run without them.
- Training needs a GPU session (Colab or Kaggle) and about 48 pages, of which 15 are held out: roughly
  33 for training and validation. That is little data; a weak result would not be surprising.
- Some SAP pages are sideways in the raw render and the highlighter boxes are in that frame, while
  the runtime sees the rotated page. Training on the raw render would teach the wrong geometry for
  those pages; the dataset should be built from the normalised pages.

## Consequence

`config/models.yaml` keeps `rule_based`. Until the experiment is run on the held-out pages and this
record is updated with the numbers, the system makes no claim that YOLO helps. A negative result
will be recorded here as fully as a positive one.

## Steps to finish (see `.kiro/skills/train-yolo/SKILL.md`)

1. Finish T7, review `gold_boxes.csv`, pick the held-out set.
2. `python -m eval.build_yolo_dataset`; verify no page is in both splits and no held-out page is in either.
3. Train and export on Colab or Kaggle; install under `models/`; record size and SHA-256 in `models/README.md`.
4. Compare IoU, latency and memory on the held-out pages; apply the rule; update this file.
