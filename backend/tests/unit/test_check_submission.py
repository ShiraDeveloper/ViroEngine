"""Tests for scripts/check_submission.py (T30): the forbidden-path rules and the required files."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_submission import (
    MANUAL,
    REQUIRED_FILES,
    forbidden_tracked,
    missing_files,
    run_checks,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "path",
    [
        "reference/samples_part1.pdf",
        "eval/gold.csv",
        "gold.csv",
        "eval/gold_draft.csv",
        "models/trocr_small_encoder_int8.onnx",
        "models/yolov8n_regions_int8.onnx",
        ".env",
        "eval/results/bakeoff.csv",
        "deploy/server.pem",
    ],
)
def test_forbidden_paths_are_caught(path: str) -> None:
    assert [p for p, _ in forbidden_tracked([path])] == [path]


@pytest.mark.parametrize(
    "path",
    [
        "reference/README.md",
        "models/README.md",
        "eval/results/.gitkeep",
        ".env.example",
        "eval/make_gold.py",
        "config/senders.json",
    ],
)
def test_allowed_paths_pass(path: str) -> None:
    assert forbidden_tracked([path]) == []


def test_missing_required_file_is_reported(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    absent = missing_files(tmp_path)
    assert "LICENSE" in absent and "README.md" not in absent


def test_checklist_complete() -> None:
    """The automatic subset passes on this repository, and every manual item stays manual."""
    passed, failed = run_checks(REPO_ROOT)
    assert failed == [], failed
    assert len(passed) == 2
    assert len(MANUAL) >= 10 and "docs/privacy.md" in REQUIRED_FILES
