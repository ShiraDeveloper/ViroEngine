# `reference/` — source material (git-ignored)

This folder holds the four PDFs distributed with the challenge. **Everything here except this
README is git-ignored**, and `.pre-commit-config.yaml` plus the CI `secrets` job block any attempt
to commit a file from it.

Why: the repository is public, and these are medical commitment forms. The laboratory altered the
personal details, but scanned patient forms still do not belong in a public repository.

## What to put here

| Filename (use exactly this) | Original |
|---|---|
| `hackathon_guidelines.pdf` | הנחיות האתגר — המעבדה המרכזית לנגיפים |
| `free_toolbox.pdf` | ארגז הכלים החינמי — האקתון |
| `samples_part1.pdf` | התחייבויות — דוגמאות, חלק 1 (26 pages) |
| `samples_part2.pdf` | התחייבויות — דוגמאות, חלק 2 (22 pages) |

The code, the tests and `docs/PLAN.md` refer to these exact names. Renaming them breaks the
tooling.

## Page labels

Sample pages are identified project-wide by label, used in `inventory.csv`, `gold.csv`,
`held_out.txt`, test names and task descriptions:

- `A1` … `A26` — pages 1–26 of `samples_part1.pdf`
- `B1` … `B22` — pages 1–22 of `samples_part2.pdf`

## The plan does not depend on these files

Every fact needed to build the product is written into `docs/PLAN.md` §2: the seven fields, where
each appears, the per-institution commitment grammar, the trap table, the document families and
the open unknowns. The PDFs are the fastest way to *see* a form, not a prerequisite.

Consequently:

- Evaluation and training tooling **skips gracefully** when the folder is absent — it exits 0 with
  a notice rather than failing.
- CI runs green on a fresh clone with no sample data, using synthetic fixtures generated in code.
- A new contributor can read the plan and start work before obtaining the PDFs.

## Getting them

Download from the challenge page of the hackathon community, or copy from wherever the team keeps
them. On the original development machine they were at:

```
C:\Users\user1\Desktop\הנחיות להאקתון\
```

Copy them in and rename to the four filenames above.

## Related files, also git-ignored

| Path | Why |
|---|---|
| `eval/gold.csv` | Contains values transcribed from the sample forms |
| `eval/results/` | Crops, reports and intermediate images derived from the samples |
| `models/*.onnx` | Trained weights; regenerate via `.kiro/skills/train-yolo` |

`eval/held_out.txt` **is** committed: it holds only the 15 reserved page labels, no values.
