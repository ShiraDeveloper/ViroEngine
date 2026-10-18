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
