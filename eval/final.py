"""Final held-out evaluation and ablation table (PLAN T30, §10.5, §10.8).

    git tag v1.0-freeze            # freeze first, measure second
    python -m eval.final --gold eval/gold.csv --out eval/results

Runs the held-out split once: the full system and each ablation of `run_eval.ABLATIONS`, then
writes ``final_report.json`` and ``final_report.md``. Three guards, each tested:

* **Freeze.** HEAD must be the commit the freeze tag points at (``--no-freeze-check`` for a rehearsal
  that is labelled as one in the report).
* **Once.** The held-out log (`eval.heldout_log`) must be empty: these pages have not been read by
  any earlier evaluation. The run is logged before it reads, so a crash still counts.
* **Complete.** The ablation table must hold every row; a missing one stops the report from being
  written rather than leaving a hole.

Numbers are reported as they are (§10.8). Targets missed are explained by hand in
``docs/limitations.md``; this module never softens a figure. The augmented held-out variants need
the stress augmenter (T25) and are listed as not run until it exists.

EVALUATION ONLY. Output holds counts and rates, never a field value.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import date
from pathlib import Path
import subprocess
import sys
from typing import Final

from backend.core.errors import SafeLineError
from backend.core.models import Strict
from eval.heldout_log import DEFAULT_LOG, GIT_TIMEOUT_S, REPO_ROOT, current_commit, read_runs
from eval.run_eval import (
    ABLATIONS,
    DEFAULT_GOLD,
    DEFAULT_HELD_OUT,
    DEFAULT_PDFS,
    DEFAULT_RESULTS,
    EvalReport,
    evaluate,
    render,
)

FREEZE_TAG: Final = "v1.0-freeze"
FULL_SYSTEM: Final = "full system"
AUGMENTED_NOTE: Final = "augmented held-out variants: not run (needs the stress augmenter, T25)"


class FreezeError(SafeLineError):
    """HEAD is not the frozen commit, so a final number would not be reproducible."""

    code = "FREEZE_REQUIRED"


class HeldOutAlreadyUsed(SafeLineError):
    """An earlier evaluation read the held-out pages; the final run would not be a first look."""

    code = "HELD_OUT_USED"


class IncompleteAblation(SafeLineError):
    """The ablation table lacks a row."""

    code = "ABLATION_INCOMPLETE"


class AblationRow(Strict):
    """One row of the ablation table: a layer switched off, or the full system."""

    name: str
    report: EvalReport


class FinalReport(Strict):
    """The held-out result with the context a published number needs."""

    date: date
    commit: str | None
    freeze_tag: str
    freeze_checked: bool
    rows: list[AblationRow]
    notes: list[str]


Evaluator = Callable[[str | None], EvalReport]


def tag_commit(tag: str = FREEZE_TAG) -> str | None:
    """The commit `tag` points at, or None when the tag does not exist."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"],
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


def check_freeze(*, head: str | None, tagged: str | None, tag: str = FREEZE_TAG) -> None:
    """Raises FreezeError unless `head` is the commit `tagged` names."""
    if tagged is None:
        raise FreezeError(f"tag {tag} does not exist: freeze first, measure second")
    if head is None or head != tagged:
        raise FreezeError(f"HEAD is not the commit tagged {tag}")


def assert_held_out_unread(log: Path) -> None:
    """Raises HeldOutAlreadyUsed when the log holds any earlier held-out run."""
    runs = read_runs(log)
    if runs:
        raise HeldOutAlreadyUsed(
            f"{len(runs)} earlier run(s) already read the held-out pages (first at {runs[0].at})"
        )


def run_final(
    *,
    evaluator: Evaluator,
    log: Path,
    head: str | None,
    tagged: str | None,
    check_freeze_tag: bool = True,
    today: date | None = None,
) -> FinalReport:
    """Run the full system and every ablation through `evaluator(ablation_or_None)`. The guards run
    first; the table is checked complete before it is returned. Raises FreezeError,
    HeldOutAlreadyUsed or IncompleteAblation."""
    if check_freeze_tag:
        check_freeze(head=head, tagged=tagged)
    assert_held_out_unread(log)
    rows = [AblationRow(name=FULL_SYSTEM, report=evaluator(None))]
    rows += [AblationRow(name=name, report=evaluator(name)) for name in ABLATIONS]
    ensure_complete(rows)
    notes = [AUGMENTED_NOTE]
    if not check_freeze_tag:
        notes.insert(0, "REHEARSAL: the freeze tag was not checked; these are not final numbers")
    return FinalReport(
        date=today or date.today(),
        commit=head,
        freeze_tag=FREEZE_TAG,
        freeze_checked=check_freeze_tag,
        rows=rows,
        notes=notes,
    )


def ensure_complete(rows: list[AblationRow]) -> None:
    """Raises IncompleteAblation unless the table has the full system and every ablation, once."""
    expected = [FULL_SYSTEM, *ABLATIONS]
    names = [r.name for r in rows]
    if names != expected:
        missing = sorted(set(expected) - set(names))
        raise IncompleteAblation(f"ablation table rows {names} != {expected}; missing {missing}")
    for row in rows:
        if row.report.split != "heldout" or row.report.fields == 0:
            raise IncompleteAblation(f"row '{row.name}' is not a held-out measurement")


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_markdown(report: FinalReport) -> str:
    """The report as Markdown: the table first, then the full-system metrics."""
    head = [
        "# Final report - held-out split",
        "",
        f"Date {report.date.isoformat()} | commit {report.commit or 'unknown'} | "
        f"freeze tag {report.freeze_tag} {'checked' if report.freeze_checked else 'NOT checked'}",
        "",
        "| Configuration | Pages | Fields | Accuracy | Silent error | Flag recall | Flag rate |"
        " Local close | Cloud calls | p50 ms |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report.rows:
        r = row.report
        head.append(
            f"| {row.name} | {r.pages} | {r.fields} | {_fmt(r.accuracy)} | {_fmt(r.silent_error)} | "
            f"{_fmt(r.flag_recall)} | {_fmt(r.flag_rate)} | {_fmt(r.local_close_rate)} | "
            f"{r.cloud_calls} | {r.p50_latency_ms if r.p50_latency_ms is not None else 'n/a'} |"
        )
    head += [
        "",
        "## Full system",
        "",
        "```",
        render(report.rows[0].report, per_field=True),
        "```",
        "",
    ]
    head += [f"- {note}" for note in report.notes]
    head += [
        "- Compare each figure with its PLAN §1 target by hand; a missed target goes to "
        "`docs/limitations.md` with its real value."
    ]
    return "\n".join(head) + "\n"


def write_report(report: FinalReport, out_dir: Path) -> None:
    """Write final_report.json and final_report.md (UTF-8, LF)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "final_report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8", newline="\n"
    )
    (out_dir / "final_report.md").write_text(
        render_markdown(report), encoding="utf-8", newline="\n"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Exit 0 with a message when the data is absent (CI), 2 on a refused run."""
    p = argparse.ArgumentParser(description="Final held-out evaluation and ablation table.")
    p.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    p.add_argument("--held-out", type=Path, default=DEFAULT_HELD_OUT)
    p.add_argument("--out", type=Path, default=DEFAULT_RESULTS)
    p.add_argument("--log", type=Path, default=DEFAULT_LOG)
    p.add_argument("--local-only", action="store_true")
    p.add_argument("--no-freeze-check", action="store_true", help="rehearsal; labelled as such")
    args = p.parse_args(argv)
    if not args.gold.is_file() or not any(f.is_file() for f in DEFAULT_PDFS):
        sys.stdout.write("skip: eval/gold.csv or the sample PDFs are absent (git-ignored data)\n")
        return 0

    def evaluator(ablation: str | None) -> EvalReport:
        return evaluate(
            args.gold,
            split="heldout",
            ablation=ablation,
            local_only=args.local_only,
            held_out=args.held_out,
            heldout_log=args.log,
        )

    try:
        report = run_final(
            evaluator=evaluator,
            log=args.log,
            head=current_commit(),
            tagged=tag_commit(),
            check_freeze_tag=not args.no_freeze_check,
        )
    except SafeLineError as exc:
        sys.stderr.write(f"{exc.code}: {exc.detail}\n")
        return 2
    write_report(report, args.out)
    sys.stdout.write(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
