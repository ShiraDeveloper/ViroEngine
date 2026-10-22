# Limitations and third-party notices

> **Status: skeleton.** Completed in **Task 29**, updated with final numbers in **Task 30**.
> The challenge guidelines ask for an honest report. This file is that report, and it is written
> before the results are known so that it cannot be quietly trimmed afterwards.

## What does not work well

### Hebrew handwritten names — the weakest field

In the sample set, patient names are handwritten in blue ink on every page, often partly
illegible. This is the one field that genuinely requires Hebrew handwriting recognition.

- **No free local tool reads Hebrew handwriting acceptably.** Tesseract fails on it. Azure
  Document Intelligence [does not support Hebrew handwriting at all](https://learn.microsoft.com/en-us/answers/questions/1639127/hebrew-handwritten-text-optimal-solution-for-recog).
- Consequently the name fields depend on the cloud vision model, and in `LOCAL_ONLY` mode they are
  simply flagged for the technician.
- _Measured accuracy: to be filled in Task 30._

### Handwritten digits — a local reader of unproven value

TrOCR-small-handwritten is trained on Latin handwriting. Digit glyphs are largely shared with
Hebrew handwriting, so we expect it to transfer for numbers and not for letters. **This is an
assumption measured in the Task 17 bake-off**, and the reader is dropped if it loses to simply
escalating. _Outcome: to be filled._

### Region detection on unseen layouts

The rule-based detector finds regions by reading the labels around them. If the label itself is
unreadable, or the layout is unfamiliar, it has nothing to work from. The YOLOv8n experiment
(Task 16) addresses this, under a decision rule declared in advance. _Outcome: to be filled._

Measured by Task 11 on the 48 sample pages (ground truth counted by eye; dev data, not held-out):

- **Central stickers with a barcode: 36 of 41 found.** The misses are two tilted stickers, one whose
  caption the scan cuts off, and two whose caption the strip read did not return.
- **Handwritten central numbers on SAP screens: none found.** Tesseract cannot read the handwritten
  caption, so there is nothing to anchor a region to. These fields fall to the cloud reader or the
  technician.
- **Receipt stamps: 1 page of 48 gets a correct region.** The stamp's large, tilted lettering is not
  read by the page pass. Target was ≥ 90%.
- Mean region IoU is not reported: there are no gold boxes yet.

### Accessibility

Automated `axe-core` checks run on all six screens in CI and are green. Automated tooling catches
roughly a third of real accessibility problems. **Full WCAG 2.1 AA conformance requires manual
testing with a screen reader (NVDA or VoiceOver) and expert review, which was out of scope for
8.5 days.** We do not claim conformance; we claim the automated checks pass and the design
decisions were made with AA in mind.

### CSV export and leading zeros

The CSV file stores `011223344` correctly, but a CSV carries no cell types. If a technician
double-clicks it, Excel reads the column as a number and drops the leading zero (and may show long
numbers in scientific notation). The `.xlsx` export is not affected: identifier cells are written
as text. For Excel use the workbook; to read a CSV in Excel use Data > From Text/CSV and set the ID,
central and commitment columns to Text. We do not wrap values in `="..."` because that would
corrupt the data for every other consumer.

### The learning layer is not autonomous

It learns only from human confirmations, needs three of them before a hint affects scoring, and
new labels or institutions require explicit approval. The first batch from a new institution gets
no benefit. For a medical laboratory this is a deliberate trade: every behavioural change is
traceable to an approval and revertible by deleting a row.

### Open questions that affect correctness

Six questions were sent to the organizers (`PLAN.md` §17) and remain unanswered at the time of
writing. Each affected field is treated as a hint-level candidate and **flagged** rather than
decided: whether "גורם שולח" is the issuer or the payer · whether the highlighted barcode is the
commitment number at Wolfson, Kaplan, Meir and Hillel Yaffe · whether Rambam's commitment includes
the handwritten `944` prefix · which number is the commitment at the Forensic Medicine Institute
and KGI.

### Scale and scope

- Tested on 48 sample pages plus ~240 augmented variants. That is a small corpus; per-institution
  coverage is a handful of pages each.
- The engine extracts seven administrative fields and deliberately never reads clinical content.
- Free-tier hosting: 512 MB and 0.1 CPU. Throughput targets assume a batch of ~30 forms, not a
  production queue.

## Privacy constraints that shaped the product

- **Free-tier Gemini may use input to improve Google's products.** Therefore only the altered
  sample forms were ever uploaded, a permanent banner states this in the UI, and the deployment
  proposal for the laboratory is a paid/organizational tier or `LOCAL_ONLY` mode.
- Real deployment inside the Central Virology Laboratory would require Ministry of Health
  information-security approval and a data processing agreement with any external provider.
  **This repository is not legal advice.**

## Third-party notices

| Component | Licence | Note |
|---|---|---|
| **ultralytics (YOLOv8)** | **AGPL-3.0** | Used **only offline** on Colab/Kaggle to train and export weights. The runtime image imports `onnxruntime` and never `ultralytics` — a test asserts this. Anyone deploying this engine commercially with the YOLO detector enabled must review the AGPL implications |
| Tesseract OCR | Apache-2.0 | |
| TrOCR (microsoft/trocr-small-handwritten) | MIT | Model weights from Hugging Face |
| zxing-cpp | Apache-2.0 | |
| OpenCV (headless) | Apache-2.0 | |
| pypdfium2 | Apache-2.0 / BSD-3 | |
| FastAPI, Pydantic, openpyxl, rapidfuzz, structlog | MIT / Apache-2.0 | |
| React, Vite, Tailwind, Radix, TanStack | MIT | |
| Our own code | MIT | See [`LICENSE`](../LICENSE) |

## Cloud provider layer (T18)

- **Not exercised against a live provider.** The Gemini, OpenRouter and Groq request shapes are
  written from the providers' public REST documentation and tested against a fake transport only.
  Gemini's `responseJsonSchema` field and `thinkingBudget: 0` have not been confirmed live; a
  provider that rejects either answers 4xx, which the chain treats as `rejected` and skips. Run the
  48 sample pages once with a real key before relying on the escalation numbers.
- **No OpenRouter or Groq model is pinned.** The free model lists change weekly. Set
  `OPENROUTER_VISION_MODEL` (and `GROQ_VISION_MODEL` if Groq lists a vision model that day) or the
  provider is reported unavailable and skipped.
- **S5's letterhead question is a second request.** When text and logo matching both fail, S5 asks
  the model to choose the sender from the closed list (a letterhead crop only). It is a separate
  request from S9's field request, so a page can make two provider calls; both are cached.
- **The cached answer is patient data.** It holds values read from crops, never an image. It expires
  after `JOB_TTL_MINUTES` unless `VISION_CACHE_TTL_MINUTES` is set deliberately.
- **Planning cost only.** `estimated_usd` uses Gemini Flash list prices (an assumption, PLAN §6.5);
  free-tier fallbacks are counted as zero.

## Security hardening (T27)

- **Rate limiting keys on the socket peer.** Behind a reverse proxy every request would share one
  address unless the server runs with proxy headers enabled and a trusted forwarder list; the
  deployment (T28) must set that. Counters live in memory, so they reset on restart and are not
  shared between processes.
- **Open API without `API_KEY`.** An empty key means no authentication (development). The
  application logs `auth_disabled` at start-up; a deployment must set it.
- **The upload is held in memory** (bounded by `MAX_UPLOAD_MB`) until the pipeline takes it,
  instead of being streamed to disk.
- **Dependencies move.** The pins were bumped to clear `pip-audit` on 2026-10-22; new advisories
  appear continuously, so the CI `pip-audit --strict` job can go red later with no code change.
- **`gitleaks` is not run locally.** CI runs it over the full history; locally the history was
  searched for key-shaped strings and private keys with `git log -G`.
- **Sample hashes depend on the renderer.** `config/sample_hashes.json` is the SHA-256 of pages as
  this build's PDF renderer draws them; a different renderer version changes the hashes and the
  sample forms are then treated as unknown (processed locally, safe but less accurate).
  Regenerate with `python -m tools.make_sample_hashes` on the deployment's renderer.

## Learning layer (T24)

- **Learns only from human confirmations, and needs three of them** before a location or shape is
  used. The first batch from a new institution gets no benefit. Every behaviour change is traceable
  to confirmations and reversible by deleting a row.
- **The effect on real forms is unmeasured.** The only measured improvement is on a synthetic page
  (T24 test). Whether the local close rate rises on the 48 samples awaits the gold set, a person
  confirming rows and the augmented set.
- **A hint needs a known sender.** A page with no resolved institution (most real pages at the
  moment, see T20) cannot be keyed, so it neither teaches nor benefits.
- **Label synonyms and sender proposals have no producer yet.** The store and the approval API
  work; S6 does not report the governing label and S5 does not read approved proposals.
- **A typed value teaches nothing,** because its place on the page is unknown; picked candidates
  and untouched fields teach their location.

## Reported results versus targets

_Filled in Task 30 from the held-out run. Where a target was missed, the real value appears here
with an explanation. A target is never published as an achievement._

| Metric | Target | Measured (held-out) | Note |
|---|---|---|---|
| Silent error | ≤ 1% | — | |
| Flag recall | ≥ 95% | — | |
| Printed accuracy | ≥ 98% | — | |
| Handwritten accuracy | ≥ 90% | — | |
| Flag rate | ≤ 15% | — | |
| 30 forms | < 4 min | — | |
