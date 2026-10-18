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

### Accessibility

Automated `axe-core` checks run on all six screens in CI and are green. Automated tooling catches
roughly a third of real accessibility problems. **Full WCAG 2.1 AA conformance requires manual
testing with a screen reader (NVDA or VoiceOver) and expert review, which was out of scope for
8.5 days.** We do not claim conformance; we claim the automated checks pass and the design
decisions were made with AA in mind.

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
