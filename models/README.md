# `models/` — ONNX weights (git-ignored)

Weights are **not committed** — they are large binaries that would bloat a public repository. This
file is their provenance: what each model is, where it came from, and how to regenerate it.

Regeneration procedures: [`.kiro/skills/train-yolo/SKILL.md`](../.kiro/skills/train-yolo/SKILL.md)
(covers both the YOLO detector and the TrOCR export).

## Expected files

| File | Purpose | Size budget | Used by |
|---|---|---|---|
| `trocr_small_encoder_int8.onnx` + `trocr_small_decoder_int8.onnx` + `trocr_small_vocab.json` | Handwritten **digit** reader (S7) | whole model < 80 MB (enforced: a larger set is refused at load) | `adapters/ocr/trocr_digits.py` |
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
| Source model | `microsoft/trocr-small-handwritten` (Hugging Face, MIT), revision `b4648cfa171985a6745f37ddd637e98c0da958ac` |
| Source weights | `pytorch_model.bin`, 245,933,041 bytes, sha256 `1b83102cbc1520dee1c3937ac334da83da18cf4683d46ebd1bd4e93ebe584dd7` (from the Hub's own file metadata) |
| Export | `python -m tools.export_trocr --out models --revision b4648cfa171985a6745f37ddd637e98c0da958ac` — `torch.onnx.export` (opset 17, decoder without KV cache), then `onnxruntime.quantization.quantize_dynamic` QUInt8 on both graphs. Needs `torch`, `transformers`, `sentencepiece`, `onnx` (export-time only, not runtime dependencies) |
| Exported on | 2026-10-21, locally (torch 2.12, transformers 5.12, onnxruntime 1.20.1) |
| File size | encoder 23.0 MB · decoder 40.2 MB · vocabulary 0.8 MB — **64.0 MB total** (budget 80) |
| SHA-256 | encoder `7663c04eedadcab03969d17421bbfd7d28b3cf938133fc3bde70ccc1ebbfbcf8` · decoder `9accf3c260a6b3102647f4aab42ae4e60dff91d66b1dce6cc5470aab5144b3f7` · vocabulary `de3a2e969ab3d836f777701d8bfddb54332537f560db4849263e3e23487247de` |
| Fidelity (int8 vs fp32) | identical unconstrained text on 9 of 15 rendered strings (3 fonts × 5 values). Whitelisted top-5: the rendered 6-digit number in **9 of 9** (8 in first place); the rendered dates in **0 of 6** (`/` read as `1`, `.` dropped) |
| Measured on real handwriting (T13, 2026-10-21) | **0 of 7** hand-cropped sample values in the top-5 (A2 `939158` → `954158`, `932158`, `934158` …; dates' slashes read as `1`) — see PLAN T13. Bake-off (T17) still to confirm |

The network filter on the development machine (NetFree) blocks the Hub's file CDN with `418`;
the export above ran from an unfiltered network. The Hub's own file metadata gives the source
weights' sha256, so a copy from anywhere can be verified before use.

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
| Status (2026-10-22) | **Not trained.** No model file exists; every row below is a placeholder. Dataset builder and adapter are implemented and unit-tested only |
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
python -m pytest backend/tests/unit/test_trocr.py::test_model_size_under_budget   # < 80 MB
python -c "import onnxruntime as ort; s=ort.InferenceSession('models/yolov8n_regions_int8.onnx'); \
print([i.name for i in s.get_inputs()], [o.name for o in s.get_outputs()])"
```
