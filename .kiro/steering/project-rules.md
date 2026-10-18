---
inclusion: always
---

# Safe Line — Project Rules

Non-negotiable rules for this repository. The full specification is `docs/PLAN.md`.

## 1. Task ownership — binding

**Each developer executes only her own tasks.** Task ownership is listed in `docs/PLAN.md` §13.

- Ahuba does not implement a Shira task and vice versa, even when idle or when it looks faster.
- Need something from the other's area? Ask her or open an issue. Do not implement it.
- Blocked by an unmerged dependency? Work against the fake adapter (`backend/adapters/fakes.py`)
  or the mock client (`frontend/src/api/mockClient.ts`) — both exist for exactly this.
- Reassignment requires both to agree, an edit to the owner row in §13 **in the same PR**, and a
  note in the daily sync. Never silently.
- **Reviewing is not implementing.** A reviewer requests changes; she does not push fixes.

Reason: the submission asks each developer personally what she built. Ownership must be real.

When asked to work on a task, state the owner from §13 first. If the current session is acting for
the other developer, say so and stop.

## 2. Never guess

- An empty, flagged field is always better than a guessed value. This is an explicit laboratory
  requirement.
- `FieldResult.value = None` means "not found", never "error". The distinction is carried by
  `level` and `reason`.
- Every level-D field carries a `reason` from the closed set in §5.2. Never free text, never a
  string built at the call site.
- The sender is chosen from the closed list or reported as `"לא ברשימה"`. It is never invented.

## 3. No fixed formats

- **No field is identified by the length or shape of its value.** Identification is by label
  proximity and context.
- The barcode sweep accepts **any** symbology and **any** payload length. No `len()` filter, no
  `{6}` regex on a payload.
- Per-sender patterns are **hints** that adjust the score. They never reject or create a value.
- Observed counter-examples that make this a rule: a commitment number containing a letter
  (`X132767`), commitment lengths from 4 to 9, central numbers with no barcode at all, and a
  private laboratory with no commitment number.

## 4. Yellow and blue are hints only

- The yellow highlighter mask lives in `eval/` and is used **only** to build the evaluation gold
  set. It must never be imported from `backend/core/` and never influence a runtime decision.
  Highlighting is inconsistent across the samples and absent on real forms.
- The blue-ink mask may tighten a handwritten crop when ink is present. Its absence must change
  nothing.

## 5. Local-first cascade

Order: deterministic code → local OCR → local handwritten digits → cloud vision → human.

- A field escalates only when its **combined score** is below threshold or a hard validation
  failed. Never on OCR self-confidence alone — Tesseract reports high confidence for handwriting
  it invented.
- Only **crops** leave the machine, never a full page. One call per page.
- `RegionKind.clinical` areas are never sent to a provider. Enforced by assertion.
- `LOCAL_ONLY=true` must produce zero network calls.

## 6. Only real standards are hard validations

Hard rules, which can reject a value:
- Israeli national ID: 9 digits with the check digit.
- Dates: must exist on the calendar and must not be in the future.

Everything else only adjusts the confidence score.

**Forbidden:** any plausibility rule relating the central number to the receipt date. It was
removed from the design (§5.3) and must not reappear.

## 7. Privacy

- No PII in logs. Redaction is a `structlog` processor so it cannot be bypassed at a call site.
- The learning store holds **locations and abstracted shapes only**. Names, IDs, dates, form
  values and images are rejected by `_assert_pii_free`.
- All job artefacts are purged after `JOB_TTL_MINUTES` (default 30).
- Never commit a key, a sample form, or `eval/gold.csv`.

## 8. Architecture

- `backend/core/` must not import FastAPI, Tesseract, vendor SDKs, `cv2`, `sqlite3`, `openpyxl`,
  or anything under `backend/adapters/`. Enforced by `scripts/check_layering.py` in CI.
- `backend/container.py` is the only module permitted to construct adapters.
- Pipeline stages are pure functions: one Pydantic model in, one out.
- Every model response is validated against the strict schema before it can become a field value.
  The model never assigns its own confidence — S8 does.

## 9. Configuration, not constants

No literal threshold in business code. Every one comes from `Settings` or `config/models.yaml` so
`eval/calibrate.py` can sweep it. Model ids live in config because free model lists change weekly.

## 10. Honest reporting

- A target is never written as an achievement.
- Every number shown carries its data split and its date.
- Final numbers come from the 15 held-out pages, which are never used for calibration, rule
  writing, prompt tuning or reader selection.
- A missed target is reported with its real value and an explanation in `docs/limitations.md`.

## 11. Definition of Done

See `docs/PLAN.md` §0.5. In short: lint, types and tests green; a named test for new behaviour; no
secret or sample data added; contract changes regenerate the fixture and the TS types;
accuracy-affecting changes include before/after numbers in the PR; the other developer approved.
