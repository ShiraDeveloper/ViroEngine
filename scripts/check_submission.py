"""check_submission.py - the machine-checkable part of the pre-submission checklist (PLAN T30).

    python scripts/check_submission.py

Offline checks only: nothing forbidden is tracked by git (sample forms, the gold set, model
weights, a real .env, evaluation results), the required files exist, and the architecture
diagram and licence are present. Items that need a person or the network (public link works in a
private window, CI green on the frozen commit, video under 60 s, keys rotated) are printed as
MANUAL and never reported as passed. Exit 0 when every automatic check passes, 1 otherwise.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import re
import subprocess
import sys
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
GIT_TIMEOUT_S: Final = 30  # structural: ls-files on a small repository returns at once

# A tracked path matching one of these must never be committed (project rules §7 and T30).
FORBIDDEN: Final = (
    (re.compile(r"^reference/(?!README\.md$)"), "sample forms under reference/"),
    (re.compile(r"(^|/)gold\.csv$"), "the gold set"),
    (re.compile(r"^eval/gold_draft"), "a gold draft"),
    (re.compile(r"\.onnx$"), "model weights"),
    (re.compile(r"(^|/)\.env$"), "a real .env"),
    (re.compile(r"^eval/results/(?!\.gitkeep$)"), "evaluation results"),
    (re.compile(r"^models/(?!README\.md$)"), "files under models/"),
    (re.compile(r"\.(pem|key|p12)$"), "a key file"),
)
REQUIRED_FILES: Final = (
    "README.md",
    "LICENSE",
    ".env.example",
    "docs/PLAN.md",
    "docs/ARCHITECTURE.md",
    "docs/limitations.md",
    "docs/privacy.md",
    "docs/openapi.json",
    "config/models.yaml",
)
MANUAL: Final = (
    "public link opens from a private window with the access code in the description",
    "still works after 60 minutes idle",
    "repository is public with README and licence",
    "gitleaks detect --log-opts=--all is clean, and git log -S finds no key pattern",
    "video under 60 s with subtitles and no login",
    "tool list matches config/models.yaml with versions",
    "sample Excel and screenshots of all six screens exist",
    "CI is green on the frozen commit (tag v1.0-freeze)",
    "three personal answers prepared by each developer, separately",
    "offline backups on the laptop",
    "API keys deleted or rotated",
)


def forbidden_tracked(paths: Iterable[str]) -> list[tuple[str, str]]:
    """(path, why) for every tracked path that must not be committed. Paths use forward slashes."""
    found: list[tuple[str, str]] = []
    for path in paths:
        for pattern, why in FORBIDDEN:
            if pattern.search(path):
                found.append((path, why))
                break
    return found


def missing_files(root: Path, required: Iterable[str] = REQUIRED_FILES) -> list[str]:
    """Required files that do not exist under `root`."""
    return [name for name in required if not (root / name).is_file()]


def tracked_files(root: Path) -> list[str] | None:
    """Paths git tracks, or None when git is unavailable or `root` is not a checkout."""
    try:
        done = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            check=False,
            cwd=root,
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return [line for line in done.stdout.splitlines() if line]


def run_checks(root: Path = REPO_ROOT) -> tuple[list[str], list[str]]:
    """(passed, failed) check descriptions for the automatic checks."""
    passed: list[str] = []
    failed: list[str] = []
    tracked = tracked_files(root)
    if tracked is None:
        failed.append("git could not list tracked files")
    else:
        bad = forbidden_tracked(tracked)
        if bad:
            failed += [f"tracked but forbidden ({why}): {path}" for path, why in bad]
        else:
            passed.append("no forbidden file is tracked")
    absent = missing_files(root)
    if absent:
        failed += [f"required file missing: {name}" for name in absent]
    else:
        passed.append("all required files exist")
    return passed, failed


def main() -> int:
    passed, failed = run_checks()
    for line in passed:
        sys.stdout.write(f"ok      {line}\n")
    for line in failed:
        sys.stdout.write(f"FAILED  {line}\n")
    for line in MANUAL:
        sys.stdout.write(f"MANUAL  {line}\n")
    total = len(passed) + len(failed)
    sys.stdout.write(f"{len(passed)}/{total} automatic checks passed; {len(MANUAL)} manual items\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
