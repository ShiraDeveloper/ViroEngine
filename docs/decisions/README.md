# Architecture Decision Records

One file per decision, append-only. A decision is recorded here when it would otherwise have to be
re-argued, or when a judge is likely to ask "why did you do it that way?".

## Format

`ADR-<NNN>-<slug>.md`, kept short:

```markdown
# ADR-005 — No fixed digit lengths

- **Status:** accepted
- **Date:** 2026-10-19
- **Deciders:** Ahuba, Shira
- **Task:** T11

## Context
What forced the decision. Include the observation or measurement.

## Decision
What we do.

## Consequences
What this costs, and what it rules out.

## Alternatives rejected
What else was considered, and the reason it lost.
```

## Index

Summaries in [`PLAN.md`](../PLAN.md) §3.8. The last five are filled by the task that measures them,
and a negative result is recorded just as fully as a positive one.

| ADR | Decision | Status | Task |
|---|---|---|---|
| 001 | Hexagonal ports and adapters | accepted | T3 |
| 002 | Local-first cascade; cloud only for low-score fields | accepted | T15, T18 |
| 003 | Escalation on a combined score, not OCR self-confidence | accepted | T14 |
| 004 | Only national standards are hard validations | accepted | T8 |
| 005 | No fixed digit lengths; identification by label proximity | accepted | T11 |
| 006 | Yellow mask only for the gold set, never at runtime | accepted | T7 |
| 007 | API-first; Excel is one export adapter | accepted | T19, T22 |
| 008 | Credential rotation then provider failover | accepted | T18 |
| 009 | Free tier only: Vercel + Render, Hugging Face as Plan B | accepted | T28 |
| 010 | Learning stores locations and shapes, never values | accepted | T24 |
| 011 | React + FastAPI rather than Streamlit | accepted | T4 |
| 012 | YOLOv8n as a measured experiment, not a commitment | accepted | T16 |
| 013 | Reader selection | **pending** | T17 bake-off |
| 014 | Hosting: stay on Render or move to the Space | **pending** | Day-3 measurement |
| 015 | Does the sticker barcode equal the printed digits? Decides whether level A exists | **pending** | T5 |
| 016 | Region detector: rule-based or YOLO | **pending** | T16 |
| 017 | Final results | **pending** | T30 |
