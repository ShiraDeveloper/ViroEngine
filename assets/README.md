# `assets/` — submission media

Produced in **Task 29**. Unlike `reference/` and `models/`, these files **are committed** — they
contain no patient data and the README and the submission both link to them.

| File | What | Constraint |
|---|---|---|
| `demo.gif` | ~8 seconds of the review workspace: click an orange cell, the Evidence Lens zooms the stamp, pick a candidate | **< 5 MB** so it renders on GitHub |
| `architecture.png` | The hexagonal diagram from `docs/ARCHITECTURE.md`, exported | Legible at 1200 px wide; the submission form asks for an architecture image |
| `screen-*.png` | One screenshot per screen: login, upload, processing, review, export, dashboard | Part of the submission checklist |
| `sample-export.xlsx` | An Excel export produced from the sample forms, showing the confidence colours and cell comments | Must come from the altered sample forms, never real data |

The 60-second video is **not** committed — it is uploaded to the submission platform and a copy is
kept offline on the presenting laptop. See `docs/demo_script.md`.

Once the files exist, uncomment the two image references in the root `README.md`.
