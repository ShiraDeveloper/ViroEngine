---
title: Safe Line
emoji: 🔬
colorFrom: blue
colorTo: green
sdk: docker
app_port: 8000
pinned: false
---

# Safe Line · שורה בטוחה (Hugging Face Space)

Commitment forms → validated Excel rows. This Space runs the whole demo in one container: the web
app at `/` and the API at `/v1`. This file is the **Space's** `README.md`, copied in by
`scripts/deploy_hf.ps1`; the project README lives in the GitHub repository.

Set these in **Settings → Variables and secrets** (never commit them):

| Name | Kind | Value |
|---|---|---|
| `GEMINI_API_KEYS` | secret | one or more Google AI Studio keys, comma-separated |
| `APP_ACCESS_CODE` | secret | the code the judges type on the login screen |
| `ALLOWED_ORIGINS` | variable | `https://<user>-<space>.hf.space` |
| `FORWARDED_ALLOW_IPS` | variable | `*` |
| `JOB_TTL_MINUTES` | variable | `30` |

Optional: `OPENROUTER_API_KEY` + `OPENROUTER_VISION_MODEL` (a fallback provider). Leave `API_KEY`
unset in this one-container setup (the web app is served from the same origin; uploads are
protected by `APP_ACCESS_CODE`).

Check it: `https://<user>-<space>.hf.space/v1/health` must answer `"status":"ok"`.
Full guide: `docs/DEPLOY.md` in the repository.
