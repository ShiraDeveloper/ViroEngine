"""check_docs.py — Verify that required documentation files exist and are well-formed.

Run: python scripts/check_docs.py
Exit 0 on success, 1 on any failure.
"""
from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]

# Documentation files that must always be present
REQUIRED_FILES: list[Path] = [
    REPO_ROOT / "docs" / "PLAN.md",
    REPO_ROOT / "docs" / "ARCHITECTURE.md",
    REPO_ROOT / "docs" / "limitations.md",
    REPO_ROOT / "docs" / "decisions" / "README.md",
]

# Every ADR file must start with a top-level heading
ADR_DIR = REPO_ROOT / "docs" / "decisions"


def check_required_files() -> list[str]:
    """Return a list of error messages for any missing required file."""
    errors: list[str] = []
    for path in REQUIRED_FILES:
        if not path.exists():
            errors.append(f"Required documentation file missing: {path.relative_to(REPO_ROOT)}")
    return errors


def check_adr_headings() -> list[str]:
    """Return error messages for ADR files that lack a top-level heading."""
    errors: list[str] = []
    if not ADR_DIR.is_dir():
        return errors
    for adr_file in sorted(ADR_DIR.glob("ADR-*.md")):
        text = adr_file.read_text(encoding="utf-8").lstrip()
        if not text.startswith("# "):
            errors.append(
                f"ADR file missing top-level heading: {adr_file.relative_to(REPO_ROOT)}"
            )
    return errors


def main() -> int:
    errors: list[str] = []
    errors.extend(check_required_files())
    errors.extend(check_adr_headings())

    if errors:
        for err in errors:
            print(f"::error::{err}", file=sys.stderr)
        return 1

    print(f"check_docs: {len(REQUIRED_FILES)} required files present, ADRs well-formed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
