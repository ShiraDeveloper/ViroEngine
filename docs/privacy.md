# Privacy and data handling

> **Status: skeleton.** Completed in **Task 27** alongside the security test suite.
> Controls are specified in [`PLAN.md`](./PLAN.md) §8; each has a named test that proves it.

## What this system handles

Israeli medical commitment forms. They contain identifying information (name, national ID) and
sit inside a medical context — the most sensitive category of personal data. The engine is built
to touch as little of it as possible.

**The engine extracts seven administrative fields** and deliberately never reads clinical content:
diagnosis, requested test and physician notes are detected as `RegionKind.clinical` so they can be
**excluded** from anything that leaves the machine.

## Data flow

```
upload  →  job temp directory (mode 0700)
        →  three page variants: color, gray_bin, thumb
        →  local OCR + barcode decode          [nothing leaves the machine]
        →  unresolved fields only: crops + one 512px thumbnail  →  provider
        →  values in the job record
        →  export rendered on demand (never stored)
        →  purge after JOB_TTL_MINUTES (default 30)
```

| Stage | What exists | Where |
|---|---|---|
| Upload | original bytes | job temp directory |
| Processing | three page variants | same directory |
| Escalation | field crops + one thumbnail | in memory and in the request body only |
| Results | extracted values | memory + `job.json` in the same directory |
| Audit | `ReviewEdit` entries | same directory |
| **After TTL** | **counters only** | `run_report.json`, `run_metric` — neither holds a value |

## What leaves the machine

Only **crops of fields the engine could not resolve locally**, plus one 512 px thumbnail for
layout context. Never a full page. Never a clinical region — that exclusion is enforced by an
assertion in the request builder, not by convention, and is tested by
`security/test_no_clinical_in_payload.py`.

`LOCAL_ONLY=true` disables every provider. Unresolved fields are flagged for the technician
instead. A test patches the socket layer and asserts **zero** network connections in this mode.

## Retention

- `JOB_TTL_MINUTES`, default **30**. Purge deletes the whole job directory.
- A sweeper runs every 5 minutes so a restart cannot leave orphaned data.
- The UI warns the technician 5 minutes before expiry.
- `DELETE /v1/jobs/{id}` purges immediately.
- In Docker, `DATA_DIR` points at `/tmp`, so nothing survives a container restart.

## No personal data in logs or in the learning store

- **Logs:** a `structlog` processor drops forbidden keys before any sink sees them. It is a
  processor, not a convention, so it cannot be bypassed at a call site. `LOG_LEVEL=DEBUG` does not
  enable value logging.
- **Learning store:** holds field *locations* (normalized boxes), static printed *label text*, and
  abstracted value *shapes* (`X132767` → `A######`). Names, IDs, dates, form values and images are
  rejected by `_assert_pii_free`, which raises rather than logs. The digits are discarded at
  extraction, so a value never reaches the storage layer.

## Provider terms

**On the free tier, Gemini may use input to improve Google's products.** The same applies to some
other free tiers (Mistral). Consequences for this project:

1. Only the **altered sample forms** supplied with the challenge were ever uploaded.
2. A permanent, non-dismissible banner in the UI states that this is a demonstration system and
   only sample forms should be uploaded.
3. The deployment proposal for the laboratory is a paid or organizational tier with no training on
   input, or `LOCAL_ONLY` mode with no external calls at all.

## Access control

| Control | Detail |
|---|---|
| `X-API-Key` | Required on every `/v1` route except `/health`; compared with `secrets.compare_digest` |
| `X-Access-Code` | Required on uploads — the demo gate for judges |
| Rate limiting | 60 requests/min per IP, 10 uploads/min |
| CORS | Strict allow-list, no wildcard |
| Headers | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, CSP, HSTS |
| Audit log | Append-only `ReviewEdit`; every edit and undo is recorded |

## Secrets

Real values live only in `.env` (git-ignored) and in the hosting provider's secret store.
`gitleaks` runs as a pre-commit hook **and** in CI across full history. Every key is deleted or
rotated after the hackathon — an explicit item in the submission checklist.

## Regulatory note

Medical information is "sensitive information" under the Israeli Privacy Protection Law and its
information-security regulations. A real deployment inside the Central Virology Laboratory would
require review and approval by the Ministry of Health's information-security function, a data
processing agreement with any external provider, and a decision on whether cloud escalation is
permitted at all — which is why `LOCAL_ONLY` is a first-class supported mode rather than a
fallback.

**This document is an engineering description, not legal advice.**
