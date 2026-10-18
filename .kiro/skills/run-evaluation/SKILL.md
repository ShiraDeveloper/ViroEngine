---
name: run-evaluation
description: Run the Safe Line accuracy evaluation, interpret the metrics, and update the ablation table. Use when measuring extraction accuracy, checking for regressions after an engine change, calibrating thresholds, or producing the final held-out report.
---

# Run the accuracy evaluation

## Prerequisites

1. `reference/samples_part1.pdf` and `reference/samples_part2.pdf` present (git-ignored).
2. `eval/gold.csv` exists — 336 confirmed rows, produced by Task 7. Git-ignored.
3. `eval/held_out.txt` exists — 15 page labels. Committed.
4. Tesseract with Hebrew: `tesseract --list-langs` includes `heb`.
5. For a run that includes escalation, `GEMINI_API_KEYS` is set in `.env`.

If `reference/` or `gold.csv` is missing, the runner exits 0 with a skip message. That is
intentional so CI works without sample data.

## The three splits

| Split | Pages | When to use |
|---|---|---|
| `dev` | 33 | Daily development, calibration, bake-off, any tuning |
| `heldout` | 15 | **Final reporting only** (Task 30) |
| `all` | 48 | Sanity checks only. Never for a reported number |

> **Binding rule.** Held-out pages are never used to tune a threshold, write a rule, choose a
> reader or pick a prompt. `--split dev` refuses to read held-out labels. `--split heldout` prints
> a final-report banner. Using held-out numbers during development invalidates the submission's
> accuracy claim.

## Commands

```bash
# daily check after any engine change
python -m eval.run_eval --gold eval/gold.csv --split dev

# the local-only baseline (M3) - proves the engine works with no AI service
python -m eval.run_eval --gold eval/gold.csv --split dev --local-only

# robustness: degraded variants
python -m eval.run_eval --gold eval/gold.csv --split dev --variants nohl,rot,blur,jpeg,fax

# fill the ablation table by toggling layers through config overrides
python -m eval.run_eval --gold eval/gold.csv --split dev --ablation all

# tune weights and the escalation threshold (dev only; refuses heldout)
python -m eval.calibrate --gold eval/gold.csv --split dev

# emit JSON for the dashboard
python -m eval.run_eval --gold eval/gold.csv --split dev --emit-json

# FINAL, Task 30 only, after tagging v1.0-freeze
python -m eval.run_eval --gold eval/gold.csv --split heldout --ablation all --emit-json
```

Outputs land in `eval/results/` (git-ignored): `latest.json`, `ablation.json`,
`final_report.{md,json}`, per-field breakdowns.

## Reading the metrics

Read them in this order. The order is the argument.

| Metric | Target | What a bad value means |
|---|---|---|
| **Silent error** | ≤ 1% | The leading metric. Wrong **and** not flagged. A rise means the scoring engine is overconfident — check which signals fired on the failing fields before touching anything else |
| **Flag recall** | ≥ 95% | Of all wrong fields, how many were flagged. Low recall with low silent error is impossible; if both look good, verify the gold set |
| Flag precision | reported | Guards against flagging everything. Falling precision with rising recall means the threshold is too aggressive |
| Flag rate | ≤ 15% | Above this the technician's hour does not shrink enough |
| Printed accuracy | ≥ 98% | A drop usually means a region or label change, not an OCR change |
| Handwritten accuracy | ≥ 90% | Expected to be the weakest. Names are the hardest |
| Local close rate | reported | Should rise after the learning layer is populated. A drop means regions or hints regressed |
| Escalation rate | reported | Rising escalation with flat accuracy wastes quota |
| p50 / p95 latency | p50 < 8 s | Check against the 512 MB host budget |
| Measured cost | ≈ $0 | Comes from real provider-call counters, not an estimate |

**Correctly-empty-and-flagged fields count as correct.** Leaving Rosenblatt's commitment number
empty is the designed behaviour, not an error.

## After an engine change

1. Run `--split dev`.
2. Paste the before/after numbers into the PR description. This is part of the Definition of Done
   for any accuracy-affecting change.
3. If silent error rose, do not proceed. Find which fields regressed:
   `python -m eval.run_eval --gold eval/gold.csv --split dev --per-field --show-failures`
4. If a threshold needs moving, run `eval.calibrate` rather than editing a number by hand. It
   writes `config/models.yaml` with the date and split it was derived from.

## Updating the ablation table

1. `python -m eval.run_eval --gold eval/gold.csv --split dev --ablation all`
2. Copy the values into `docs/PLAN.md` §10.5.
3. Empty cells stay `—`. **Never write a zero for a value that was not measured.**
4. In Task 30, repeat with `--split heldout` and replace the table with those numbers.

Layers are toggled through `config/models.yaml` overrides, not code edits — if a layer cannot be
switched off from config, that is a bug in the modularity.

## Reporting rules

- Every number published carries its split and date: `held-out · 27.10 14:20`.
- A target is never written as an achievement.
- A missed target is reported with its real value, plus an explanation in `docs/limitations.md`.
- Final submission numbers come from `--split heldout` **after** the code freeze.
