# Privacy and data handling

> **Status: complete for the engine as built (Task 27, 2026-10-22).** Controls are specified in
> [`PLAN.md`](./PLAN.md) §8; each has a named test that proves it (table at the end). What is *not*
> proved is listed in [`limitations.md`](./limitations.md).

## What this system handles

Israeli medical commitment forms. They contain identifying information (name, national ID) and
sit inside a medical context — the most sensitive category of personal data. The engine is built
to touch as little of it as possible.

**The engine extracts seven administrative fields** and deliberately never reads clinical content:
diagnosis, requested test and physician notes are detected as `RegionKind.clinical` so they can be
**excluded** from anything that leaves the machine.

## Data flow

```
upload  →  job registry (memory) + job directory under DATA_DIR/jobs/<id> (mode 0700)
        →  three page variants: color, gray_bin, thumb          (files, same directory)
        →  local OCR + barcode decode                            [nothing leaves the machine]
        →  unresolved fields only: crops (+ one redacted 512px thumbnail when a field needs page
           context)  →  provider                                 [only if allowed, see below]
        →  values in the job record                              (memory)
        →  export rendered on demand                             (never stored)
        →  purge at JOB_TTL_MINUTES (default 30)
```

| Stage | What exists | Where |
|---|---|---|
| Upload | original bytes | memory until the pipeline takes them, then released |
| Processing | page variants | job directory |
| Escalation | field crops + at most one redacted thumbnail | in memory and in the request body only |
| Provider answers | the values the model read (never an image) | a cache file keyed by content hash under `DATA_DIR`, **deleted after `JOB_TTL_MINUTES`** by the sweeper |
| Results | extracted values | job registry (memory) |
| Audit | `ReviewEdit` entries (old and new values) | job registry (memory) |
| **After the purge** | **counters only** | `run_metric` (no value); a `purged` tombstone for 10 minutes |

Nothing is written to a shared location, and a restart loses every job together with its files,
which is the intended lifetime.

## What leaves the machine

Only **crops of fields the engine could not resolve locally**, plus — only when a field needs page
context — one 512 px thumbnail in which every clinical region **and the patient box** have been
painted over *before* it is downscaled, so no glyph survives the resample. Never a full page.
Never a clinical region: that exclusion is an assertion in the request builder, not a convention.

A **document that is not one of the sample forms** (its page hash is not in
`config/sample_hashes.json`) is processed locally by default; its crops go to a provider only if the
job carries an explicit consent, recorded in the run report. Fields that would have escalated are
flagged for the technician instead, and the Reading Trail shows no cloud step.

`LOCAL_ONLY=true` disables every provider. A test patches the socket layer and asserts **zero**
network connections in this mode, through the chain, the container and the whole pipeline.

## Retention

- `JOB_TTL_MINUTES`, default **30**. The purge deletes the job directory, the audit log, the undo
  history and every value in the registry. Cached provider answers are content-addressed, not
  per-job, so they carry their own lifetime of the same length and the 5-minute sweeper deletes
  the expired ones.
- A job's own timer fires at the TTL; a sweeper runs every 5 minutes for timers a restart lost; and
  the TTL is also applied on access, so a job past its time is never served as live.
- A job that is still running is never purged under its feet.
- For 10 more minutes the job answers `status: purged`, so the UI shows a message instead of a 404.
- `DELETE /v1/jobs/{id}` purges immediately.
- In Docker, `DATA_DIR` points at `/tmp`, so no job file survives a container restart.
- The **learning database survives** purges and restarts by design, which is safe because it holds
  no value (next section).

## No personal data in logs or in the learning store

- **Logs.** A `structlog` processor, the last step before rendering, replaces forbidden keys at any
  nesting depth, reduces every non-primitive object to its type name (never its `repr`) and scrubs
  credential-shaped strings and this process's own configured secrets from free text and
  tracebacks. It is a processor, not a convention, so it cannot be bypassed at a call site.
  `LOG_LEVEL=DEBUG` does not enable value logging. A test runs a page, an edit and a confirmation
  with planted values and searches the whole output for them.
- **Learning store.** Holds field *locations* (normalized boxes), static printed *label text*,
  abstracted value *shapes* (`X132767` → `A######`) and counters. The observation type has no value
  field; a forbidden key raises `PiiLeak` before anything is written; the digits are discarded at
  extraction. A test searches every table **and the raw database file** for the planted values.
  A location or shape is used only after three human confirmations; label synonyms and proposed
  institutions also need an explicit approval.

## Prompt injection

A form can print instructions. Four layers: (1) the system prompt says text in an image is data and
every string spliced into a prompt is JSON-encoded and length-bounded; (2) the model must answer in
a strict schema with no unknown keys (there is no confidence field), so an instruction-following
answer is rejected; (3) the model has no tools, network or code execution; (4) validation and
scoring run after the model answers, so it cannot promote its own output. The malicious-form
fixture (`backend/tests/fixtures/make_malicious_form.py`) is generated by code.

## Input validation

Files are identified by content, never by extension; encrypted PDFs, PDFs with JavaScript or other
active content, ZIP bombs (ratio cap), path traversal and nested archives are refused before a page
is rendered; size, page and megapixel caps bound memory. An oversized request body is refused at the
HTTP boundary before it is buffered.

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
| `X-API-Key` | Required on every `/v1` route except `/health`; compared with `secrets.compare_digest`. **If `API_KEY` is empty the API is open** and the application logs `auth_disabled` at start-up: every deployment must set it |
| `X-Access-Code` | Required on uploads — the demo gate for judges |
| Rate limiting | 60 requests/min per client, 10 uploads/min; `/health` exempt; `429` with `Retry-After` |
| Body cap | `MAX_UPLOAD_MB` + 2 MB, enforced on the declared length and on a chunked stream |
| CORS | Strict allow-list from `ALLOWED_ORIGINS`; a wildcard is refused at start-up |
| Headers | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, CSP `default-src 'self'`, HSTS over TLS — on every answer, refusals included |
| Audit log | Append-only `ReviewEdit`; every edit and undo is recorded; values are cleared at the purge |

The client address used for rate limiting is the socket peer. Behind a reverse proxy, run the server
with proxy headers enabled so the real client is seen (see deployment notes).

## Secrets

Real values live only in `.env` (git-ignored) and in the hosting provider's secret store.
`gitleaks` runs as a pre-commit hook **and** in CI across full history; the repository history was
also scanned for key-shaped strings. Fake credentials in tests are marked `TESTKEY` and allow-listed.
Every key is deleted or rotated after the hackathon — an explicit item in the submission checklist.

## How each control is proved

| Row | Control | Test |
|---|---|---|
| T-1…T-5 | Input validation, body cap | `security/test_input_validation.py`, `security/test_auth.py::test_body_over_the_cap…` |
| T-6 | Authentication | `security/test_auth.py::test_requires_api_key`, `::test_requires_access_code` |
| T-7 | Rate limit | `security/test_auth.py::test_rate_limit_blocks_burst` |
| T-8 | CORS | `security/test_auth.py::test_cors_rejects_unknown_origin` |
| T-9 | Headers | `security/test_auth.py::test_security_headers_present` |
| T-10 | Prompt injection | `security/test_prompt_injection.py` |
| T-11, T-12 | Clinical / whole pages | `security/test_no_clinical_in_payload.py` |
| T-13 | Retention | `security/test_ttl_purge.py` |
| T-14 | PII in logs | `security/test_no_pii_logs.py` |
| T-15 | Learning store | `security/test_learning_store_rejects_values.py` |
| T-16 | Secret in the repo | `security/test_env_example.py`, CI job `secrets` |
| T-17 | Secret through the API | `security/test_health_does_not_expose_keys.py` |
| T-18 | Dependencies | CI jobs `bandit`, `pip-audit` (clean at the pinned versions) |
| T-19 | Egress in local mode | `security/test_local_only.py` |
| T-20 | Tampering | `integration/test_audit_log.py` |
| T-21 | Non-sample documents | `security/test_non_sample_defaults_local_only.py` |

## Regulatory note

Medical information is "sensitive information" under the Israeli Privacy Protection Law and its
information-security regulations. A real deployment inside the Central Virology Laboratory would
require review and approval by the Ministry of Health's information-security function, a data
processing agreement with any external provider, and a decision on whether cloud escalation is
permitted at all — which is why `LOCAL_ONLY` is a first-class supported mode rather than a
fallback.

**This document is an engineering description, not legal advice.**
