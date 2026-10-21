"""Tests for scripts/check_layering.py.

test_core_has_no_forbidden_imports        — the real backend/core/ is clean
test_checker_detects_planted_violation    — a file with `import fastapi` is caught
test_checker_detects_backend_adapters_import — backend.adapters is also caught
test_checker_ignores_clean_file           — stdlib-only file produces no violations
test_checker_handles_dotted_submodule     — cv2.version is still caught as cv2
"""

from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from scripts.check_layering import FORBIDDEN, find_violations


def test_core_has_no_forbidden_imports() -> None:
    """The real backend/core/ must not import any forbidden module.

    Skips gracefully when backend/core/ does not yet exist (before Task 3).
    """
    repo_root = Path(__file__).resolve().parents[3]
    core_dir = repo_root / "backend" / "core"

    if not core_dir.is_dir():
        pytest.skip("backend/core/ not yet created — will be checked once Task 3 merges")

    violations = find_violations(core_dir, FORBIDDEN)
    formatted = [
        f"{p.relative_to(repo_root)}:{line}  import {imp!r}" for p, line, imp in violations
    ]
    assert not violations, "Forbidden imports found in backend/core/:\n" + "\n".join(formatted)


def test_checker_detects_planted_violation(tmp_path: Path) -> None:
    """A file importing fastapi inside a fake core tree must be detected."""
    fake_core = tmp_path / "backend" / "core"
    fake_core.mkdir(parents=True)

    bad_file = fake_core / "bad_module.py"
    bad_file.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            import fastapi
            from fastapi import APIRouter
            """
        ),
        encoding="utf-8",
    )

    violations = find_violations(fake_core, FORBIDDEN)

    assert len(violations) >= 1, "Expected at least one violation, got none"
    found_files = {str(v[0]) for v in violations}
    assert str(bad_file) in found_files, f"Expected {bad_file} in violations, got: {found_files}"


def test_checker_detects_backend_adapters_import(tmp_path: Path) -> None:
    """Importing backend.adapters from within the core must be detected."""
    fake_core = tmp_path / "backend" / "core"
    fake_core.mkdir(parents=True)

    bad_file = fake_core / "sneaky.py"
    bad_file.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            from backend.adapters import fakes
            """
        ),
        encoding="utf-8",
    )

    violations = find_violations(fake_core, FORBIDDEN)

    assert len(violations) >= 1, "backend.adapters import must be detected"
    found_imports = {v[2] for v in violations}
    assert "backend.adapters" in found_imports, (
        f"Expected 'backend.adapters' in detected imports, got: {found_imports}"
    )


def test_checker_ignores_clean_file(tmp_path: Path) -> None:
    """A file with only stdlib imports must not trigger any violation."""
    fake_core = tmp_path / "backend" / "core"
    fake_core.mkdir(parents=True)

    clean_file = fake_core / "clean_module.py"
    clean_file.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            import re
            import datetime
            from pathlib import Path
            from typing import Protocol
            """
        ),
        encoding="utf-8",
    )

    violations = find_violations(fake_core, FORBIDDEN)
    assert violations == [], f"Unexpected violations in clean file: {violations}"


def test_checker_handles_dotted_submodule(tmp_path: Path) -> None:
    """import cv2.version inside core must be caught even though it is a submodule."""
    fake_core = tmp_path / "backend" / "core"
    fake_core.mkdir(parents=True)

    bad_file = fake_core / "dotted.py"
    bad_file.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            import cv2.version
            """
        ),
        encoding="utf-8",
    )

    violations = find_violations(fake_core, FORBIDDEN)
    assert any("cv2" in imp for _, _, imp in violations), (
        "cv2.version must be caught as a cv2 violation"
    )
