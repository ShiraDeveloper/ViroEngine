"""Append-only log of every evaluation that read held-out pages (PLAN T30, project rule §10).

The final report may use the held-out pages exactly once. This log is how that is checked rather
than promised: `eval.run_eval` appends a line each time a run selects a held-out page (when it is
given a log path), and `eval.final` refuses to start when the log already has an entry.

A line holds the time, the split, a page count, the ablation name and the git commit - never a
page label, a field or a value. EVALUATION ONLY: `backend/` never imports this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess
from typing import Final

from pydantic import ValidationError

from backend.core.models import Strict

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_LOG: Final = REPO_ROOT / "eval" / "results" / "heldout_runs.jsonl"
GIT_TIMEOUT_S: Final = 10  # structural: git rev-parse answers at once or something is wrong


class HeldOutRun(Strict):
    """One evaluation that selected held-out pages."""

    at: str  # ISO-8601 UTC
    split: str
    pages: int
    ablation: str | None
    commit: str | None


def current_commit() -> str | None:
    """The checked-out commit id, or None when git is unavailable or this is not a checkout."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO_ROOT,
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = done.stdout.strip()
    return out if done.returncode == 0 and out else None


def record_read(
    path: Path, *, split: str, pages: int, ablation: str | None, commit: str | None = None
) -> None:
    """Append one run to the log (creating it). Called before the pages are read, so a run that
    crashes half-way still counts as a use."""
    run = HeldOutRun(
        at=datetime.now(UTC).isoformat(timespec="seconds"),
        split=split,
        pages=pages,
        ablation=ablation,
        commit=commit if commit is not None else current_commit(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(run.model_dump_json() + "\n")


def read_runs(path: Path) -> list[HeldOutRun]:
    """Every logged run, oldest first; empty when there is no log. A malformed line is treated
    as a run too (its content is unknown, so it must not hide a use)."""
    if not path.is_file():
        return []
    runs: list[HeldOutRun] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            runs.append(HeldOutRun.model_validate_json(line))
        except ValidationError:
            runs.append(
                HeldOutRun(at="unknown", split="unknown", pages=0, ablation=None, commit=None)
            )
    return runs
