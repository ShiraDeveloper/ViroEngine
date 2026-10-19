# ADR-015 — Barcode / Print Agreement

**Date:** 2026-10-19 (D2)
**Status:** Accepted
**Author:** Ahuba

## Context

U-1: Does the "מרכזי" sticker barcode payload equal the digits printed beside it?

Agreement would constitute **evidence level A** — the strongest, cheapest signal in the
pipeline (§5.2): no model inference, just a match between two independent sources on the
same physical sticker. If they disagree, level A must be removed from `scoring.py` and
Shira must be notified the same day (T14 depends on it).

## Method

```
python -m tools.inventory \
    --pdfs reference/samples_part1.pdf reference/samples_part2.pdf \
    --out eval/results/inventory.csv
```

- Render at 300 DPI with pypdfium2
- Barcodes swept at 0/90/180/270° with zxing-cpp 2.3.0 (any symbology, any length)
- Full-page Tesseract `heb+eng` for OCR text (see caveat below)
- Agreement: numeric tokens within 6% of page diagonal checked for exact / substring / none
- "מרכזי" caption detected at rapidfuzz ratio ≥ 85 (printed sideways → exact matching undercounts)

## Results

| Metric | Count |
|--------|-------|
| Total pages | 48 |
| Rotated pages (barcode orientation) | 0 |
| Barcodes found | 95 |
| "מרכזי" captions detected | 0 (see caveat) |
| Exact agreements | 0 (see caveat) |
| Substring agreements | 0 (see caveat) |
| No agreements | 95 (see caveat) |

### ⚠ Caveat — Tesseract not available on Windows dev machine

Tesseract is not installed on the local Windows development machine.
All OCR calls fell back gracefully to empty `OcrResult` (as designed — the tool never raises).
This means:

- **Caption detection** (`_detect_merkazi_caption`) returned `False` for all pages — OCR text was empty.
- **Agreement verdicts** are `"none"` for all 95 barcodes — not because of actual disagreement,
  but because there were no OCR tokens to compare against.
- **Institution keywords** and **page-role guesses** that rely on OCR text are empty/`"unknown"`.

**The barcodes themselves are real**: 95 barcodes found across 48 pages, with payloads recorded
correctly in `eval/results/inventory.csv`.

Tesseract IS available inside the Docker image (installed in `Dockerfile`). The definitive
agreement answer will come from running the tool in CI or Docker:

```
docker compose run --rm backend python -m tools.inventory \
    --pdfs reference/samples_part1.pdf reference/samples_part2.pdf \
    --out eval/results/inventory.csv
```

## Decision

**PROVISIONAL — Level A is RETAINED pending a Docker/CI run.**

Criterion: if exact + substring > 0, level A is supported.

We cannot confirm or deny agreement without OCR text. However:

1. **Barcodes exist** (95 found) — the physical signal for level A is present on the forms.
2. **No evidence of disagreement** — the "none" verdicts mean "no text to compare", not "mismatch".
3. The architecture already handles a graceful level-A absence: when no barcode is found or no
   nearby numeric token matches, the scoring ladder moves to level B.

**Action:** Run the tool in Docker as part of T9 setup to obtain the definitive agreement numbers.
If exact + substring > 0 → level A confirmed. If not → remove level A from `scoring.py` and
notify Shira (T14 impact).

## Barcode sample payloads (from inventory.csv)

Page A1 (4 barcodes): `939376`, `0046025783`, `730578626`, `218683563`
Page A9 (10 barcodes — highest count): see `eval/results/inventory.csv` row A9

These payloads are numeric, 6–10 digits in length — consistent with the commitment number
and central number formats described in §2.

## Consequences

- `eval/results/inventory.csv` — 48 rows, all barcodes catalogued with payloads.
- Level A ladder entry is retained in `scoring.py` (T14).
- **TODO (T9):** Re-run `tools.inventory` inside Docker with Tesseract and fill in the
  definitive agreement numbers. Update this ADR with the confirmed verdict.
- Rotation detection via barcode orientation shows 0 rotated pages — zxing-cpp reads
  multi-orientation barcodes without manual rotation. Physical page rotation (for the ~10
  expected rotated pages from §2.8) will be detected by S1 (T9) using image analysis, not
  barcode orientation.
