# Demo script and runbook

> **Status: skeleton.** Completed in **Task 29**; the Plan B URL is added in **Task 28**.
> Choreography rationale in [`PLAN.md`](./PLAN.md) §9.8; video script in §15.2.

## Live demo — ~90 seconds

Each step answers the question the previous step raises.

| # | Show | Answers |
|---|---|---|
| 1 | The pile: rotated pages, handwriting, a faint stamp | Is this a real problem? |
| 2 | Drag 30 forms; the Live Pipeline lights up | Does it actually run? |
| 3 | The `נסגרו על המחשב` counter climbing | Where does the data go? |
| 4 | The table: mostly green, three orange | Can I trust it? |
| 5 | Click an orange cell → Evidence Lens zooms the stamp → the reason appears | How do I know *why*? |
| 6 | The Reading Trail chips for that field | What is the architecture? |
| 7 | Pick a candidate, confirm, export; open the coloured Excel | Does it finish the job? |
| 8 | Stress Mode: yellow removed, rotated, blurred — still working | Is it tuned to the samples? |
| 9 | Dashboard: silent error, ablation table, learning curve | Can you prove any of this? |

**Best page for step 5:** `A1` (Meuhedet). The receipt stamp says `18-06-2026` while the printed
header says `15.06.2026`. The engine takes the stamp. One click shows exactly why.

## Recorded video — 60 seconds

See `PLAN.md` §15.2 for the second-by-second table. Hebrew narration, burned-in Hebrew subtitles,
opens without registration. **The closing numbers are the real held-out figures** — placeholders
must never ship.

## Hosting runbook (T28)

Status: **not deployed.** Everything below is written and tested up to the public URL; the URLs are
filled in by whoever does the first deploy. Why these hosts: `decisions/ADR-014-hosting.md`.

| Piece | Where | Config |
|---|---|---|
| Backend (primary, recommended) | Hugging Face Docker Space | `deploy/huggingface/README.md` + `Dockerfile` |
| Backend (alternative) | Render, Docker | `render.yaml` (free plan is 0.1 CPU: too slow for 30 forms) |
| Frontend | Vercel | `frontend/vercel.json`; set `VITE_API_BASE` to the backend URL |
| Warm-up | UptimeRobot, `/v1/health`, every 10 min | exactly **one** monitor, primary backend only |

**First deploy**

1. Create the backend host and set its secrets in the host's dashboard (never in the repo):
   `API_KEY`, `APP_ACCESS_CODE`, `ALLOWED_ORIGINS` (the Vercel URL), `GEMINI_API_KEYS`,
   `OPENROUTER_API_KEY`, `OPENROUTER_VISION_MODEL`, `GROQ_API_KEY`.
2. Create the Vercel project from `frontend/`; set `VITE_API_BASE`.
3. In GitHub, add the Actions secrets `RENDER_DEPLOY_HOOK`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`,
   `VERCEL_PROJECT_ID`, `PUBLIC_API_URL`, `PUBLIC_WEB_URL`, `SMOKE_API_KEY`, `SMOKE_ACCESS_CODE`.
4. Push to `main` with green CI: `deploy.yml` deploys both and runs the smoke test.
5. By hand: `python -m tools.smoke_test --api <api> --web <web>` (with `API_KEY` and
   `APP_ACCESS_CODE` exported to include the upload round trip).
6. Run the 48-page batch once and record latency and peak memory in ADR-014.
7. Leave it idle for an hour, then check that `/v1/health` answers without a cold start.

**Plan B.** If the smoke test fails or the primary is down: point `VITE_API_BASE` on Vercel at the
standby backend and redeploy the frontend. Record the standby URL here once it exists:
`standby URL: (not created yet)`.

**Health means ready.** `/v1/health` answers 503 with `"status":"unready"` when the Hebrew OCR pack is
missing or Tesseract cannot run, and uploads are refused with 503 `OCR_UNAVAILABLE`. A deployment whose
health is not `ok` must not be demonstrated.

## Before the demo

1. **One hour before:** `curl $API/v1/health` → confirm `"tesseract":"ready"` and `heb` in the
   language list, and that the models are loaded.
2. Confirm UptimeRobot shows the service up for the last 60 minutes.
3. Run the 48-page batch once. This warms the cache and populates the learning store, so the
   learning curve has two points.
4. Check remaining provider quota on the dashboard. If low, reorder `GEMINI_API_KEYS`.
5. Download to the presenting laptop: the video, the sample Excel, screenshots of all six screens.
6. Test the projector resolution — the review screen splits at 1280 px and stacks below 1024 px.

## If something fails

| Failure | Action |
|---|---|
| **Backend down or slow** | Switch `VITE_API_BASE` on Vercel to the Hugging Face Space standby. One environment variable, no redeploy of the frontend. _URL: added in Task 28._ |
| **Both hosts down** | Present the recorded video plus the pre-generated Excel, and run the engine locally with `LOCAL_ONLY=true` — it needs no internet at all. |
| **Provider quota exhausted mid-demo** | Say so out loud and keep going. The cascade is the point: fields turn orange with `לא ניתן לאמת — נדרשת בדיקה`, which demonstrates the design rather than contradicting it. |
| **A page fails to process** | Expected and handled. It appears in the orphans tab; the other 29 forms complete. Point at it as the per-page error isolation working. |
| **Laptop or network dies** | The video and screenshots are on the laptop; the submission already contains the live link. |

## Commands

```bash
# warm-up
curl -s $API_URL/v1/health | python -m json.tool
python -m tools.smoke_test --api $API_URL --web $WEB_URL

# fully offline fallback
LOCAL_ONLY=true uvicorn backend.app:app --port 8000
cd frontend && VITE_API_BASE=http://localhost:8000 npm run dev
```

## After the hackathon

Delete or rotate every API key, remove the deploy hooks, and confirm
`git log -S` finds no secret in history.
