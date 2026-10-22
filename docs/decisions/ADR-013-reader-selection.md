# ADR-013 - Reader selection: primary, second and vision readers, and the region detector
- **Status:** open - the harness is built and tested; the bake-off has **not been run**; no decision is made
- **Date:** 2026-10-22
- **Deciders:** Ahuba, Shira
- **Task:** T17

## Context

PLAN §10.6 replaces opinion with measurement: every reader and region detector is run on 15 hard
**dev-split** pages, one stage at a time, everything else held constant.

## Decision rule (declared before any run)

1. The **lowest silent error** wins its stage.
2. On a tie, the **local** contestant wins; then the **faster** one.
3. A region detector must also add less than 400 ms and 120 MB per page over the rule-based baseline
   (the T16 rule, thresholds in `config/models.yaml: bakeoff`). Mean region IoU is **not measured**:
   there are no reviewed gold boxes yet.
4. A contestant that cannot run is recorded as **skipped with its reason**, never as a zero.

## What exists

| Piece | State |
|---|---|
| `eval/bakeoff.py` | done: one baseline container; each contestant changes only its own stage; `--isolate` runs each in its own process so memory is its own; `--owner ahuba\|shira` splits the work as the plan does |
| Hard-page gate | done: a held-out page, an unknown held-out list or a page without gold stops the run (`HeldOutLeak`) |
| `eval/make_gold.py --pick-held-out` | now never picks a hard page; see "A conflict" below |
| Output | `eval/results/bakeoff.csv` (git-ignored): a row per contestant, losers and skipped included |
| Tests | `backend/tests/unit/test_bakeoff.py` (the four named tests plus decision-rule, region-limit and TrOCR-assumption cases) |

## Contestants and what can run today

| Stage | Contestant | Owner | Can run now? |
|---|---|---|---|
| Printed OCR | Tesseract `heb+eng` (baseline) | Shira | yes |
| Printed OCR | Surya | Shira | **no adapter** |
| Printed OCR | Mistral OCR 4.1 | Shira | **no adapter** |
| Printed OCR | Azure Document Intelligence F0 | Shira | **no adapter** |
| Digit reader | TrOCR-small ONNX (baseline) | Shira | yes, if the model files are in `models/` |
| Digit reader | Tesseract only (TrOCR switched off) | Shira | yes |
| Region | rule-based (baseline) | Ahuba | yes |
| Region | YOLOv8n ONNX | Ahuba | **no trained model** (ADR-016) |
| Vision | none (local only, baseline) | Ahuba | yes |
| Vision | Gemini 2.5 Flash | Ahuba | needs `GEMINI_API_KEYS`; sample pages only (hash gate) |
| Vision | Gemini 2.5 Flash-Lite | Ahuba | as above |

## A conflict found while building it

The hard pages must stay in the development split, but the held-out quotas (PLAN §10.2) need one
`multi_central` page, and every page in that family in `config/gold_families.json` (A9, A14, A24, B12)
is a hard page. `faint_stamp` and `fax_grade` are still empty there. The picker now stops with a message
naming the family. A human must either add a non-hard page to `multi_central` while labelling (T7) or
change the hard-page list. This is not decided here.

## Outcome

**None yet.** No primary reader, second reader, vision model or region detector is named by this record.
The TrOCR assumption of T13 is unresolved: on 7 hand-cropped values it read none correctly in its top
five (PLAN T13), but that is not the bake-off. `config/models.yaml` is unchanged.

## To finish

1. Finish T7 labelling and pick the held-out set (resolve the conflict above).
2. Shira: `python -m eval.bakeoff --owner shira --isolate`; Ahuba: `python -m eval.bakeoff --owner ahuba --isolate`
   (Gemini only if a key is configured; sample pages only).
3. Merge the two CSVs, apply the rule together, and fill in this file with every number, the winners and
   the reason each loser was dropped.
