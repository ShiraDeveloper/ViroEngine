# Safe Line · שורה בטוחה

Turns Israeli medical commitment forms — scanned, photographed, rotated, handwritten — into
validated spreadsheet rows. Wherever the engine is not certain it says so, explains why, and shows
the exact pixels it read.

> Open Source AI Hackathon 2026 · Central Virology Laboratory challenge, Israeli Ministry of Health
> Team: Ahuba, Shira

**Live demo:** _(link added in Task 28; access code in the submission description)_

<!-- Task 29 adds the recorded demo GIF here:
![Demo](assets/demo.gif)
-->


## Results

_Measured on the 15 held-out pages, which were never used for calibration, rule writing or reader
selection. Filled in Task 30 — a target is never published as an achievement._

| Metric | Result | Target |
|---|---|---|
| Silent error (wrong **and** not flagged) | — | ≤ 1% |
| Flag recall | — | ≥ 95% |
| Printed-field accuracy | — | ≥ 98% |
| Handwritten-field accuracy | — | ≥ 90% |
| Closed locally, no cloud call | — | measured |
| 30 forms end to end | — | < 4 min |
| Development cost | $0 | $0 |

See `eval/results/final_report.md` and [`docs/limitations.md`](docs/limitations.md).

## Run locally

```powershell
cp .env.example .env            # fill in GEMINI_API_KEYS (https://aistudio.google.com/apikey)
pip install -r requirements.txt
cd frontend; npm ci; $env:VITE_API_BASE="/v1"; npx vite build; cd ..
python -m uvicorn backend.app:app --port 8000     # web app + API on http://localhost:8000
```

Or `docker build -t safeline . && docker run -p 8000:8000 --env-file .env safeline`.
Deploy to a free Hugging Face Space: [`docs/DEPLOY.md`](docs/DEPLOY.md) (one script).

## How it works

Vision model first, code decides, human only where needed ([ADR-017](docs/decisions/ADR-017-vision-first.md)):

```
barcode sweep (local)  →  page read: Gemini 3 Flash, strict schema, closed sender list
   →  blind re-read of zoomed crops (the second reader never sees the first answer)
   →  code: ID check digit · real date · barcode vs reading · one number never in two fields
   →  A barcode-confirmed · B two readers agree · D orange, with the reason and the pixels
```

The model never reports its own confidence; agreement between independent readers and national
validation rules do. A field that cannot be verified stays empty and flagged, never guessed. If no
strong model answers (quota, outage) the page falls back to the local OCR cascade of PLAN §4 and
is flagged, so a run never stops. About 5–10 s a page, pages read in parallel.

![Architecture](assets/architecture.png)

Full design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) ·
Complete plan: [`docs/PLAN.md`](docs/PLAN.md)

## Tools and models

**Local engine:** Tesseract 5 (Hebrew + English) · zxing-cpp (barcodes, any symbology) ·
OpenCV headless (orientation, deskew, CLAHE) · pypdfium2 · TrOCR-small ONNX int8 (handwritten
digits) · YOLOv8n ONNX (region detection, included only if it won the bake-off) · rapidfuzz ·
Pydantic v2.

**Cloud, free tiers:** Gemini 3 Flash (page read) · Gemini 2.5 Flash (blind verification) ·
fallback OpenRouter free vision models.

**Application:** FastAPI · React + Vite + TypeScript + Tailwind + Radix · openpyxl · SQLite.

**Infrastructure, all free:** Vercel · Render (Docker) · UptimeRobot · Hugging Face Space
(standby) · GitHub Actions.

**Offline training only, not shipped at runtime:** ultralytics (AGPL-3.0), transformers, optimum.

## Privacy

Medical forms contain the most sensitive category of personal information, so the engine is built
to send as little as possible:

- Only **crops** of unresolved fields leave the machine, never a full page.
- Clinical regions — diagnosis, requested test, physician notes — are **never** sent.
- All page images and extracted values are purged after 30 minutes.
- No personal data in logs or in the learning store, which keeps only field *locations* and
  abstracted value *shapes*.
- `LOCAL_ONLY=true` makes the engine fully offline.

Details: [`docs/privacy.md`](docs/privacy.md). Medical information is "sensitive information"
under the Israeli Privacy Protection Law; a real deployment requires Ministry of Health
information-security approval. This repository is not legal advice.

## Repository

| Path | Contents |
|---|---|
| `docs/PLAN.md` | The complete specification: domain, architecture, pipeline, 30 tasks |
| `docs/ARCHITECTURE.md` | Ports and adapters, data flow, decision records |
| `docs/limitations.md` | What does not work yet, and licence notices |
| `.kiro/steering/` | Always-on project rules and code conventions |
| `.kiro/skills/` | Procedures: run the evaluation, add an institution, train the detector |
| `backend/` | FastAPI service and the pure-Python engine |
| `frontend/` | React RTL review workspace |
| `config/` | Closed sender list, model selection, versioned prompts |
| `eval/` | Gold set tooling, augmentation, metrics, bake-off |
| `reference/` | The four challenge PDFs — **git-ignored** |

## Licence

[MIT](LICENSE) for our code. Third-party notices in [`docs/limitations.md`](docs/limitations.md).
