# ADR-017 - Vision-first engine: read the page with a vision model, verify blind, decide in code

- **Status:** accepted - implemented, default engine (`ENGINE_MODE=vision_first`); the cascade of
  PLAN §4 remains as the automatic fallback and as `ENGINE_MODE=cascade`
- **Date:** 2026-10-22
- **Supersedes:** the escalation design of PLAN §4 S9 and §6.4 (crops only, local first) as the
  *default* path. Every hard rule of PLAN §5 is kept unchanged.

## Context - why the plan did not deliver the result

Measured on the sample pages on 2026-10-22, with the plan's engine as built:

| Symptom | Cause in the design |
|---|---|
| Every first and last name is orange, on every page | S9 merges a cloud read with the source of the *local* candidate. Names are handwritten, so the source is `handwritten`, and §5.2 sends a single `handwritten` reading to level D `handwriting_unverified` **whatever the model reads**. No path could ever make a name green. |
| Any document that is not one of the 48 samples comes back almost all orange | §8.5 gates cloud reads on the sample-hash list, and the web app never sends the consent flag: an unknown form is processed by Tesseract alone, which cannot read Hebrew handwriting. |
| Fields with no local candidate are never sent at all | S9 cuts a crop only where S4 found a region. On an unfamiliar layout Tesseract finds no label, so no region, so nothing to send: `not_found`. |
| ~20 s per page, 30 forms ≈ 10 minutes (target: under 4) | S1 + S3 run Tesseract on a 300 DPI page several times (orientation votes, page OCR, stamp and patient re-reads) before any decision. |
| The run stops getting answers mid-day | The free tier is **20 requests per model per day** (quota id `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, value read from a 429 on 2026-10-22). A 429 for a *daily* quota was treated as a one-minute cooldown, and only one model was configured. |

The free toolbox's own recommendation for this challenge is the opposite of local-first: Gemini
Flash reads the page with a strict JSON schema, and **validation lives in code, not in the model**.

## Decision

Per batch of three pages (`VISION_BATCH_PAGES`):

1. **Barcode sweep** (zxing, local, ~1 s): every decoded payload is an independent reader.
2. **Page read** - one request per page (`VISION_PAGES_PER_CALL=1`): the page as a 2000 px JPEG to
   `gemini-3-flash-preview` (thinking level `minimal`: ~5 s a page; `low` took ~60 s), falling back
   to `gemini-2.5-flash`. Strict schema; the sender is an **enumeration of the closed list**, so the
   model can choose an institution but never invent one. The prompt carries the domain rules of
   PLAN §2 (label synonyms, decoys, the destination, the receipt-stamp rule).
3. **Blind re-read** - ONE request per batch with a zoomed crop around every value no barcode
   confirmed, read by the verify chain (`gemini-2.5-flash` first, so a different model where its
   quota allows). The request carries **no candidate**: the second reader never sees the first answer.
4. **S8 unchanged** decides: ID check digit and calendar are hard gates; barcode + reading agree →
   **A**; two independent readings agree → **B**; anything else → **D** with its reason. A disputed
   *name* is shown flagged (the technician confirms rather than retypes); a disputed *number or
   date* stays empty. New rule: a number claimed by two of ID / central / commitment is a mix-up and
   flags both - a barcode proves the digits, not which field they belong to.
5. **Failure is never fatal**: a daily-quota 429 rests that credential for an hour and the chain
   moves to the next model; a timeout moves on at once instead of retrying a hung model; a page no
   strong model can read goes through the local cascade and is flagged.

## What was measured (sample pages A1, A9, A17; 2026-10-22)

| | Plan engine (cascade) | Vision-first |
|---|---|---|
| Time, 3 pages, end to end | 59.5 s | 25.6 s (incl. 7 s PDF intake) |
| Central number | A1, A9 (×3) correct | A1, A9 (×3), A17 correct, level A (barcode) |
| Commitment | — | A1 `730578626` (A), A9 `50364186` (B), A17 empty (correct: private lab) |
| ID | — | `011223344` level B on all three |
| Receipt date | — | A1 `18/06/2026` B, A9 `01/06/2026` B, A17 flagged |
| Sender | — | Meuhedet, Maccabi, Rosenblatt - all B |
| Names | always D | D where the two readings disagree (the sample names are deliberate scribbles); B where they agree |

A three-page run is **not** an evaluation. The held-out evaluation of PLAN §10 has to be run with
this engine before any accuracy number is published (§10.8).

## Rejected alternatives

- **Several pages in one request.** Saves quota, but measured worse: the model missed a faint stamp
  and repeated one page's name on another. Kept as a knob (`VISION_PAGES_PER_CALL`) only.
- **Flash-Lite as a page reader.** Fast, but it put a commitment barcode into the central-number
  field, which the barcode then "confirmed" - a green wrong value. Removed from the page chain.
- **Trusting the model's own confidence.** Never asked for: the schema has no confidence field.

## Consequences

- **Privacy:** the whole page now goes to the provider (tier 2 of PLAN §6.4 becomes the default).
  Acceptable for the adapted sample forms only - the free tier may use inputs for training. A real
  deployment needs a paid key (no training on inputs) or `LOCAL_ONLY=true`, plus MoH approval.
  `CLOUD_REQUIRES_CONSENT=true` restores the sample-only gate.
- **Quota:** 30 forms = 30 page reads + 10 verifications = 40 requests. Two free keys × two strong
  models ≈ 80 a day. For the final, use keys from each team member's own account, or a key with
  billing enabled (Google's $300 trial credit covers it; Flash costs ≈ $0.002 a page).
- The layering, the ports and every test of the cascade are unchanged; the cascade runs whenever
  the cloud cannot.
