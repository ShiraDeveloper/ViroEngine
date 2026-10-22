# ADR-014 - Hosting: where the backend runs, and what was measured

- **Status:** proposed (nothing is deployed yet; the decision needs a measurement on the real host)
- **Date:** 2026-10-22
- **Deciders:** Ahuba, Shira
- **Task:** T28

## Context

PLAN §11 plans the backend on Render's free tier (Docker, kept warm by one UptimeRobot monitor), the
frontend on Vercel, and a Hugging Face Space as Plan B. Facts checked on 2026-10-22:

- **Render free web service:** 512 MB RAM and **0.1 CPU**; spins down after 15 minutes without
  inbound traffic and takes about a minute to come back; 750 instance hours a month per workspace
  (one always-on service uses about 744, so exactly one monitor may keep it warm).
- **Hugging Face free CPU Space:** 2 vCPU and 16 GB RAM, Docker supported; sleeps after about 48 hours
  without traffic.

What the engine costs, measured locally (Windows laptop, Python 3.12, Tesseract 5.4 with
`tessdata_fast` Hebrew, `LOCAL_ONLY`, no cloud call, 8 pages of `samples_part1.pdf`, one worker):

| Measure | Value |
|---|---|
| Latency | **12.2 s per page** (97 s for 8 pages) |
| Python process, resident memory | 49 MB idle, 133 MB after intake, **220 MB peak** |
| Not included | Tesseract child processes (each loads the Hebrew model) and the OS |

At 12.2 s/page the target "30 forms in under 4 minutes" (8 s/page) is **missed on a laptop with several
full cores**. A 0.1-CPU host is a tenth of one core.

## Decision

Pending a measurement, the recommendation is:

1. **Primary backend: a Hugging Face Docker Space** (2 vCPU, 16 GB), not Render free. `deploy/huggingface/`
   holds the Space's `README.md` (front matter with `sdk: docker`, `app_port`). The same `Dockerfile`
   serves both hosts.
2. **Render stays as the documented alternative** (`render.yaml` is complete) for a paid instance,
   where the 0.1-CPU limit disappears. On the free plan it is expected to be too slow to demonstrate
   a 30-form batch; this has not been measured on Render.
3. **Frontend on Vercel** (`frontend/vercel.json`: SPA fallback, security headers, immutable asset
   cache). The UI calls the API at `VITE_API_BASE` (CORS allow-list = the Vercel URL).
4. **Warm-up:** exactly one UptimeRobot monitor on `/v1/health`, 10-minute interval, on the primary
   backend only.
5. **TrOCR is not shipped in the image.** The weights are 64 MB, git-ignored, and measured to close
   **zero** local fields on the sample handwriting (T13); the adapter degrades to "no reader" when the
   file is absent. This keeps the image and memory small. T17's bake-off confirms or reverses it.
6. **The Gemini SDK (`google-genai`) is not installed.** Providers are reached over REST with `httpx`
   (T18), which removes a large dependency tree from the image.

## Consequences

- Nothing here has been deployed or smoke-tested on a public URL; `tools/smoke_test.py` is tested
  against a scripted server only. **M5 is not declared.**
- `FORWARDED_ALLOW_IPS=*` is set so the rate limiter sees the real client behind the host's proxy;
  that trusts the proxy's header, which is safe only because the container is reachable solely
  through it.
- Free hosts spin down, so the first request after a long idle is slow; the monitor prevents it for the
  primary only. The HF standby is warmed by hand before a demo.
- Memory: one worker (`MAX_CONCURRENCY=1`), `MAX_UPLOAD_MB=25`, so a page and its variants fit in a
  512 MB host if one is ever used.

## Alternatives rejected

- **Render free as primary:** the CPU allowance makes the demo workload infeasible by the numbers above.
- **A second always-on service on Render:** two services would exceed the 750 free hours and suspend both.
- **Cloud Run / Fly.io:** not evaluated; both need a payment method on file, which the team does not
  assume.
