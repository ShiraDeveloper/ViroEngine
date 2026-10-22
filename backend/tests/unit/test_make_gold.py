"""Tests for eval/make_gold.py (T7). Images are synthetic; no sample PDF or gold file is needed."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from backend.core.models import FieldKey
from eval.make_gold import (
    FAMILY_QUOTAS,
    GoldInputError,
    HeldOutCoverageError,
    attach_highlights,
    crop,
    derive_families,
    finalize,
    find_highlights,
    pick_held_out,
    yellow_mask,
)
from eval.run_eval import EMPTY_TOKEN

FIELDS = [k.value for k in FieldKey]
YELLOW_BGR = (0, 255, 255)  # HSV H=30, S=255, V=255
PALE_GREY_BGR = (225, 225, 225)


def _page(width: int = 400, height: int = 400) -> np.ndarray:
    return np.full((height, width, 3), 255, dtype=np.uint8)


def _paint(img: np.ndarray, x: int, y: int, w: int, h: int, bgr: tuple[int, int, int]) -> None:
    img[y : y + h, x : x + w] = bgr


def test_yellow_mask_finds_synthetic_highlight() -> None:
    img = _page()
    _paint(img, 50, 50, 100, 30, YELLOW_BGR)
    mask = yellow_mask(img)
    assert mask[60, 100] == 255
    assert mask[300, 300] == 0


def test_pale_grey_rectangle_is_not_detected() -> None:
    img = _page()
    _paint(img, 50, 50, 100, 30, PALE_GREY_BGR)
    assert find_highlights(img) == []


def test_small_blobs_ignored() -> None:
    img = _page()
    _paint(img, 10, 10, 20, 10, YELLOW_BGR)  # 200 px²
    assert find_highlights(img) == []


def test_blob_above_threshold_is_kept_as_normalized_box() -> None:
    img = _page()
    _paint(img, 40, 80, 40, 20, YELLOW_BGR)  # 800 px²
    (box,) = find_highlights(img)
    assert box == pytest.approx((0.2, 0.1, 0.25, 0.2))


def test_highlight_attaches_to_field_with_largest_overlap() -> None:
    fields = {
        FieldKey.national_id: (0.1, 0.1, 0.2, 0.5),
        FieldKey.last_name: (0.5, 0.1, 0.6, 0.5),
    }
    attached, unattached = attach_highlights([(0.11, 0.1, 0.2, 0.5), (0.8, 0.8, 0.9, 0.9)], fields)
    assert list(attached) == [FieldKey.national_id]
    assert unattached == 1


def test_crop_is_padded_and_clamped_to_page() -> None:
    img = _page(400, 400)
    out = crop(img, (0.0, 0.0, 0.25, 0.25), padding=0.02)
    assert out.shape[:2] == (108, 108)  # 100 px box + 8 px padding on the far side only


def _write_draft(path: Path, pages: list[str], confirmed: dict[tuple[str, str], str]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "page_label",
                "field",
                "proposed_value",
                "crop_path",
                "highlighted",
                "confirmed_value",
                "notes",
            ]
        )
        for label in pages:
            for field in FIELDS:
                writer.writerow(
                    [label, field, "", "", "no", confirmed.get((label, field), "x"), ""]
                )
    return path


def test_finalize_rejects_incomplete_draft(tmp_path: Path) -> None:
    draft = _write_draft(tmp_path / "d.csv", ["A1"], {("A1", "sender"): ""})
    out = tmp_path / "gold.csv"
    with pytest.raises(GoldInputError, match="unconfirmed"):
        finalize(draft, out)
    assert not out.exists()


def test_finalize_rejects_missing_field_row(tmp_path: Path) -> None:
    draft = _write_draft(tmp_path / "d.csv", ["A1"], {})
    lines = draft.read_text(encoding="utf-8-sig").splitlines()
    draft.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8-sig")
    with pytest.raises(GoldInputError, match="missing"):
        finalize(draft, tmp_path / "gold.csv")


def test_finalize_rejects_wrong_page_count(tmp_path: Path) -> None:
    draft = _write_draft(tmp_path / "d.csv", ["A1", "A2"], {})
    with pytest.raises(GoldInputError, match="expected 48 pages"):
        finalize(draft, tmp_path / "gold.csv", expected_pages=48)


def test_empty_token_is_preserved(tmp_path: Path) -> None:
    draft = _write_draft(
        tmp_path / "d.csv",
        ["A1", "A2"],
        {("A1", "commitment_number"): EMPTY_TOKEN, ("A2", "commitment_number"): EMPTY_TOKEN},
    )
    out = tmp_path / "gold.csv"
    stats = finalize(draft, out)
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    empties = [r for r in rows if r["value"] == EMPTY_TOKEN]
    assert len(rows) == 14 and len(empties) == 2
    assert stats.empty_by_field["commitment_number"] == 2
    assert "commitment_number 2" in stats.render()


def test_leading_zero_survives_finalize(tmp_path: Path) -> None:
    draft = _write_draft(tmp_path / "d.csv", ["A1"], {("A1", "national_id"): "011223344"})
    out = tmp_path / "gold.csv"
    finalize(draft, out)
    values = {r["field"]: r["value"] for r in csv.DictReader(out.open(encoding="utf-8"))}
    assert values["national_id"] == "011223344"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"commitment_number": EMPTY_TOKEN}, {"no_commitment"}),
        ({"commitment_number": "X132767"}, {"letter_commitment"}),
        ({"commitment_number": "111;222"}, {"multi_commitment"}),
        ({"central_number": "1;2;3"}, {"multi_central"}),
        ({"commitment_number": "730578626", "central_number": "937309"}, set()),
    ],
)
def test_families_derived_from_gold_values(values: dict[str, str], expected: set[str]) -> None:
    assert derive_families(values) == expected


def _gold(path: Path, pages: dict[str, dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["page_id", "field", "value", "split"])
        for label, vals in pages.items():
            for field in FIELDS:
                writer.writerow([label, field, vals.get(field, "1"), "dev"])
    return path


def _corpus() -> tuple[dict[str, dict[str, str]], str]:
    """48 pages: A1-A26, B1-B22. Derivable families sit on fixed pages; the rest via strata."""
    pages = {f"A{i}": {} for i in range(1, 27)} | {f"B{i}": {} for i in range(1, 23)}
    pages["A17"] = {"commitment_number": EMPTY_TOKEN}
    pages["B14"] = {"commitment_number": "X132767"}
    pages["A9"] = {"central_number": "1;2;3"}
    pages["B2"] = {"commitment_number": "11;22"}
    rows = ["page_label,families"]
    rows += [f"A{i},sap" for i in (2, 4, 6, 8)]
    rows += [f"A{i},handwriting_heavy" for i in (3, 5, 7)]
    rows += ["A10,faint_stamp", "A11,faint_stamp", "B6,fax_grade", "B7,fax_grade"]
    return pages, "\n".join(rows) + "\n"


def test_held_out_is_stratified_and_seeded(tmp_path: Path) -> None:
    pages, strata_text = _corpus()
    gold = _gold(tmp_path / "gold.csv", pages)
    strata = tmp_path / "strata.csv"
    strata.write_text(strata_text, encoding="utf-8")

    first = pick_held_out(gold, tmp_path / "h1.txt", strata=strata)
    again = pick_held_out(gold, tmp_path / "h2.txt", strata=strata)
    other = pick_held_out(gold, tmp_path / "h3.txt", seed=1, strata=strata)

    assert first == again and len(first) == len(set(first)) == 15
    assert first != other
    tags = {lb: derive_families(pages[lb]) for lb in pages}
    for line in strata_text.splitlines()[1:]:
        label, fams = line.split(",")
        tags[label] |= {fams}
    for family, quota in FAMILY_QUOTAS.items():
        assert sum(1 for lb in first if family in tags[lb]) >= quota, family
    text = (tmp_path / "h1.txt").read_text(encoding="utf-8")
    assert [ln for ln in text.splitlines() if not ln.startswith("#")] == first


def test_held_out_refuses_missing_family(tmp_path: Path) -> None:
    pages, strata_text = _corpus()
    gold = _gold(tmp_path / "gold.csv", pages)
    strata = tmp_path / "strata.csv"
    strata.write_text(strata_text.replace("fax_grade", "sap"), encoding="utf-8")
    out = tmp_path / "held_out.txt"
    with pytest.raises(HeldOutCoverageError, match="fax_grade"):
        pick_held_out(gold, out, strata=strata)
    assert not out.exists()


def test_strata_unknown_family_rejected(tmp_path: Path) -> None:
    pages, _ = _corpus()
    gold = _gold(tmp_path / "gold.csv", pages)
    strata = tmp_path / "strata.csv"
    strata.write_text("page_label,families\nA1,bogus\n", encoding="utf-8")
    with pytest.raises(GoldInputError, match="unknown family"):
        pick_held_out(gold, tmp_path / "h.txt", strata=strata)


def test_core_does_not_import_eval() -> None:
    """The yellow mask lives in eval/ and must be unreachable from the engine (rule §4)."""
    import ast

    core = Path(__file__).resolve().parents[2] / "core"
    offenders = []
    for path in core.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom) and node.level == 0
                else []
            )
            if any(n == "eval" or n.startswith("eval.") for n in names):
                offenders.append(path.name)
    assert offenders == []
