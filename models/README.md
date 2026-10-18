# `models/` — ONNX weights (git-ignored)

Weights are **not committed** — they are large binaries that would bloat a public repository. This
file is their provenance: what each model is, where it came from, and how to regenerate it.

Regeneration procedures: [`.kiro/skills/train-yolo/SKILL.md`](../.kiro/skills/train-yolo/SKILL.md)
(covers both the YOLO detector and the TrOCR export).

## Expected files

| File | Purpose | Size budget | Used by |
|---|---|---|---|
| `trocr_small_decoder_int8.onnx` + encoder | Handwritten **digit** reader (S7) | whole model < 80 MB | `adapters/ocr/trocr_digits.py` |
| `yolov8n_regions_int8.onnx` | Region detector (S4), **only if it won the bake-off** | < 15 MB | `adapters/regions/yolo_onnx.py` |

Both are loaded lazily and cached, so a batch with no handwriting never pays the load cost. A
missing file degrades gracefully: the region detector falls back to `rule_based`, and a missing
TrOCR model means handwritten digits escalate instead of being read locally. Neither crashes the
pipeline — there are tests for both.

Selection is in `config/models.yaml`, never hard-coded.

## Provenance — fill in when a model is installed

### TrOCR-small (handwritten digits)

| | |
|---|---|
| Source model | `microsoft/trocr-small-handwritten` (Hugging Face, MIT) |
| Export | `optimum.onnxruntime.ORTModelForVision2Seq`, then int8 dynamic quantization |
| Exported on | _date_ |
| File size | _MB_ |
| SHA-256 | _checksum_ |
| Bake-off result (T17) | _handwritten-digit accuracy, top-5 candidate recall, p50 latency_ |

**Assumption on record:** trained on Latin handwriting; digit glyphs are largely shared with
Hebrew handwriting, so it should transfer for numbers and not for letters. Measured in T17; the
reader is dropped per the cut list if it loses to escalating.

### YOLOv8n (region detector)

| | |
|---|---|
| Training framework | `ultralytics` 8.3.x — **AGPL-3.0, offline only** |
| Dataset | `eval/build_yolo_dataset.py` from gold boxes; 48 pages × 5 variants, **split by page** |
| Classes | `central_sticker`, `receipt_stamp`, `patient_box`, `commitment_label` |
| Training command | `imgsz=1024`, 100 epochs, patience 20, `degrees=10`, `fliplr=0.0` |
| Validation mAP50 | _value_ |
| Export | ONNX opset 17 + int8 dynamic quantization |
| File size | _MB_ |
| SHA-256 | _checksum_ |
| Held-out comparison (T16) | _IoU vs rule-based, added latency, added memory, effect on silent error_ |
| Decision | _see `docs/decisions/ADR-016-region-detector.md`_ |

> **Licence.** `ultralytics` is AGPL-3.0 and is used **only** on Colab/Kaggle to train and export.
> The runtime image imports `onnxruntime` and never `ultralytics`; a test asserts this. The AGPL
> implication for any future closed-source deployment is disclosed in
> [`docs/limitations.md`](../docs/limitations.md).

## Verifying an installed model

```bash
ls -lh models/
sha256sum models/*.onnx          # compare against the table above
python -c "import onnxruntime as ort; s=ort.InferenceSession('models/yolov8n_regions_int8.onnx'); \
print([i.name for i in s.get_inputs()], [o.name for o in s.get_outputs()])"
```
