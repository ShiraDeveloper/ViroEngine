---
title: Safe Line API
emoji: 🛡️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8000
pinned: false
---

# Safe Line API (Hugging Face Space)

Backend of the Safe Line demo (Docker SDK). This file is the **Space's** `README.md`; it is not the
project README. Push the repository (with this file as the root `README.md` of the Space repo) to the
Space and set the secrets in **Settings -> Variables and secrets**:

`API_KEY`, `APP_ACCESS_CODE`, `ALLOWED_ORIGINS` (the frontend URL), `GEMINI_API_KEYS`,
`OPENROUTER_API_KEY`, `OPENROUTER_VISION_MODEL`, `GROQ_API_KEY`. Never commit them.

Also set the variables `MAX_CONCURRENCY=2`, `MAX_UPLOAD_MB=25`, `JOB_TTL_MINUTES=30` and
`FORWARDED_ALLOW_IPS=*`.

Check it: `curl https://<space>.hf.space/v1/health` must report `"status":"ok"` with `heb` in
`ocr_langs`. See `docs/demo_script.md` for the runbook and `docs/decisions/ADR-014-hosting.md` for why.
