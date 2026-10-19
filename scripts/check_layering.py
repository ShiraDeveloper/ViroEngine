"""Check that backend/core/ does not import forbidden modules.

Run: python scripts/check_layering.py
Exit 0 → 0 violations.  Exit 1 → violations printed and counted.

This script is also invoked by .pre-commit-config.yaml and by ci.yml.
It must have zero dependencies beyond the standard library.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys

# Modules that must never be imported from inside backend/core/.
# The core is a pure Python domain; it has no knowledge of frameworks,
# vendor SDKs, database drivers or its own adapters.
FORBIDDEN: frozenset[str] = frozenset(
    {
        "fastapi",
        "pytesseract",
        "google",
        "httpx",
        "sqlite3",
        "cv2",
        "openpyxl",
        "backend.adapters",
    }
)


def find_violations(
    core_dir: Path,
    forbidden: frozenset[str] | set[str],
) -> list[tuple[Path, int, str]]:
    """Return (file, line, import_string) for every forbidden import in core_dir.

    forbidden = {"fastapi","pytesseract","google","httpx","sqlite3","cv2",
                 "openpyxl","backend.adapters"}
    Never raises; an unreadable or unparseable file is skipped with a warning.
    """
    violations: list[tuple[Path, int, str]] = []

    for py_file in sorted(core_dir.rglob("*.py")):
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError) as exc:
            print(f"warning: could not parse {py_file}: {exc}", file=sys.stderr)
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden(alias.name, forbidden):
                        violations.append((py_file, node.lineno, alias.name))

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _is_forbidden(module, forbidden):
                    violations.append((py_file, node.lineno, module))

    return violations


def _is_forbidden(name: str, forbidden: frozenset[str] | set[str]) -> bool:
    """Return True when *name* starts with any entry in *forbidden*."""
    for entry in forbidden:
        # Match exact root or dotted sub-path: "fastapi" matches "fastapi.routing"
        # "backend.adapters" matches "backend.adapters.fakes"
        if name == entry or name.startswith(entry + "."):
            return True
    return False


def main() -> int:
    """Check backend/core/ for forbidden imports. Returns 1 on any violation."""
    repo_root = Path(__file__).resolve().parents[1]
    core_dir = repo_root / "backend" / "core"

    if not core_dir.is_dir():
        # The core directory does not exist yet (before Task 3).
        # Nothing to check — this is not an error.
        print("layering: 0 violations (backend/core/ not yet created — skipped)")
        return 0

    violations = find_violations(core_dir, FORBIDDEN)

    if violations:
        for path, line, imp in violations:
            try:
                rel = path.relative_to(repo_root)
            except ValueError:
                rel = path
            print(f"VIOLATION  {rel}:{line}  import {imp!r}")
        print(f"\nlayering: {len(violations)} violation(s) — fix before committing")
        return 1

    print("layering: 0 violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
