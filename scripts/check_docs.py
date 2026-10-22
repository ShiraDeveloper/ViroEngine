"""check_docs.py - Verify the documentation set (T29): files, links, honest numbers, topics.

Run: python scripts/check_docs.py
Exit 0 on success, 1 on any failure. Pure standard library; never raises on a malformed file.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]

# Documentation files that must always be present
REQUIRED_FILES: list[Path] = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "PLAN.md",
    REPO_ROOT / "docs" / "ARCHITECTURE.md",
    REPO_ROOT / "docs" / "limitations.md",
    REPO_ROOT / "docs" / "privacy.md",
    REPO_ROOT / "docs" / "demo_script.md",
    REPO_ROOT / "docs" / "decisions" / "README.md",
]

# Every ADR file must start with a top-level heading
ADR_DIR = REPO_ROOT / "docs" / "decisions"

# Documents whose relative links must resolve. PLAN.md is the spec and quotes paths that are
# created by later tasks, so it is not link-checked.
LINK_CHECKED: list[Path] = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "ARCHITECTURE.md",
    REPO_ROOT / "docs" / "limitations.md",
    REPO_ROOT / "docs" / "privacy.md",
    REPO_ROOT / "docs" / "demo_script.md",
    REPO_ROOT / "docs" / "decisions" / "README.md",
    REPO_ROOT / "assets" / "README.md",
]

# Topic -> case-insensitive regexes, ALL of which must match somewhere in limitations.md.
# These are the eight topics PLAN.md Task 29 requires.
LIMITATION_TOPICS: dict[str, list[str]] = {
    "handwriting": [r"hebrew handwrit"],
    "no free local reader": [r"no free local tool"],
    "provider training on input": [r"free-tier gemini", r"improve google"],
    "licence": [r"ultralytics", r"agpl"],
    "accessibility": [r"wcag", r"screen reader"],
    "learning layer": [r"learning layer", r"three"],
    "open questions": [r"open questions", r"unanswered"],
    "missed targets": [r"reported results versus targets", r"measured \(held-out\)"],
}

# A results line must never carry a placeholder that could be mistaken for a number.
PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9])(XX+|TODO|TBD|\[X\]|\[x\]|\?\?+)(?![A-Za-z0-9])")
_LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|!\[[^\]]*\]\(([^)\s]+)\)")
_FENCE = re.compile(r"^\s*(```|~~~)")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_EXTERNAL = ("http://", "https://", "mailto:", "#", "tel:")


def _rel(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def check_required_files() -> list[str]:
    """Return a list of error messages for any missing required file."""
    return [
        f"Required documentation file missing: {_rel(p)}" for p in REQUIRED_FILES if not p.exists()
    ]


def check_adr_headings() -> list[str]:
    """Return error messages for ADR files that lack a top-level heading."""
    errors: list[str] = []
    if not ADR_DIR.is_dir():
        return errors
    for adr_file in sorted(ADR_DIR.glob("ADR-*.md")):
        if not _read(adr_file).lstrip().startswith("# "):
            errors.append(f"ADR file missing top-level heading: {_rel(adr_file)}")
    return errors


def extract_links(text: str) -> list[str]:
    """Relative link targets in markdown text, ignoring fenced code and HTML comments."""
    kept: list[str] = []
    in_fence = False
    for line in _COMMENT.sub("", text).splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    targets: list[str] = []
    for match in _LINK.finditer("\n".join(kept)):
        target = match.group(1) or match.group(2)
        if target and not target.startswith(_EXTERNAL):
            targets.append(target)
    return targets


def check_links(documents: list[Path] | None = None) -> tuple[int, list[str]]:
    """Return (links checked, errors). A link is broken when its file target does not exist."""
    checked = 0
    errors: list[str] = []
    for doc in documents if documents is not None else LINK_CHECKED:
        if not doc.exists():
            continue
        for target in extract_links(_read(doc)):
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            checked += 1
            if not (doc.parent / path_part).resolve().exists():
                errors.append(f"Broken link in {_rel(doc)}: {target}")
    return checked, errors


def find_placeholders(text: str) -> list[str]:
    """Results lines (table rows or bullets mentioning a metric) that contain a placeholder."""
    bad: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or set(stripped) <= set("|-: "):
            continue
        if PLACEHOLDER.search(stripped):
            bad.append(stripped)
    return bad


def check_placeholders(documents: list[Path] | None = None) -> list[str]:
    """Return errors for placeholder tokens inside results tables of README and limitations."""
    docs = documents or [REPO_ROOT / "README.md", REPO_ROOT / "docs" / "limitations.md"]
    return [
        f"Placeholder in a results line of {_rel(doc)}: {line}"
        for doc in docs
        if doc.exists()
        for line in find_placeholders(_read(doc))
    ]


def missing_topics(text: str) -> list[str]:
    """Names of required limitation topics that limitations.md does not cover."""
    lowered = text.lower()
    return [
        name
        for name, patterns in LIMITATION_TOPICS.items()
        if not all(re.search(p, lowered) for p in patterns)
    ]


def check_limitations(path: Path | None = None) -> tuple[int, list[str]]:
    """Return (topics covered, errors)."""
    target = path or REPO_ROOT / "docs" / "limitations.md"
    missing = missing_topics(_read(target))
    covered = len(LIMITATION_TOPICS) - len(missing)
    return covered, [f"limitations.md does not cover: {name}" for name in missing]


def readme_numbers_errors(readme: str, report: dict[str, object]) -> list[str]:
    """Each headline metric in the final report must appear in the README results table.

    Applies only once a final report exists; formatting is the report's `readme_figures` map
    (metric name -> string exactly as printed), so no rounding rule lives here.
    """
    figures = report.get("readme_figures")
    if not isinstance(figures, dict):
        return []
    return [
        f"README results do not show {name} = {value} from the final report"
        for name, value in figures.items()
        if str(value) not in readme
    ]


def check_readme_numbers() -> list[str]:
    """Compare the README with eval/results/final_report.json when that file exists."""
    report_path = REPO_ROOT / "eval" / "results" / "final_report.json"
    if not report_path.exists():
        return []
    try:
        report = json.loads(_read(report_path))
    except json.JSONDecodeError:
        return [f"{_rel(report_path)} is not valid JSON"]
    if not isinstance(report, dict):
        return [f"{_rel(report_path)} is not a JSON object"]
    return readme_numbers_errors(_read(REPO_ROOT / "README.md"), report)


def main() -> int:
    errors: list[str] = []
    errors.extend(check_required_files())
    errors.extend(check_adr_headings())
    checked, link_errors = check_links()
    errors.extend(link_errors)
    placeholder_errors = check_placeholders()
    errors.extend(placeholder_errors)
    covered, topic_errors = check_limitations()
    errors.extend(topic_errors)
    errors.extend(check_readme_numbers())

    if errors:
        for err in errors:
            print(f"::error::{err}", file=sys.stderr)
        return 1

    placeholders = "none" if not placeholder_errors else f"{len(placeholder_errors)} found"
    print(
        f"links: {checked} checked, {len(link_errors)} broken | placeholders: {placeholders} | "
        f"limitations: {covered}/{len(LIMITATION_TOPICS)} topics present"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
