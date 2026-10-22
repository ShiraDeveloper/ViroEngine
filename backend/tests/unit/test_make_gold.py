"""Tests for eval/make_gold.py (T7). Everything is synthetic: no reference/ and no real values."""

from __future__ import annotations

from collections.abc import Collection
import csv
from pathlib import Path

import numpy as np
import pytest

from backend.adapters.imaging.codec import Pixels, encode_png
from backend.core.models import Box, FieldKey
from backend.core.ports.document import EncodedImage
from eval.calibrate import read_gold as calibrate_read_gold
from eval.make_gold import (
    DRAFT_COLUMNS,
    HELD_OUT_SIZE,
    GoldIncomplete,
    attach_field,
    cross_check,
    finalize,
    find_highlights,
    main,
    pick_held_out,
    propose_labels,
    read_rows,
    yellow_mask,
)
from eval.run_eval import EMPTY_TOKEN, EvalInputError
from eval.run_eval import read_gold as run_eval_read_gold

YELLOW = (0, 230, 255)  # BGR; OpenCV hue ~27
PAGE_H, PAGE_W = 300, 400

SYNTHETIC_FAMILIES: dict[str, list[str]] = {
    "sap": ["A2", "A3", "A4", "B20", "B21"],
    "handwriting_heavy": ["A17", "A18", "B10", "B11"],
    "faint_stamp": ["A5", "B8"],
    "fax_grade": ["A12", "B3"],
    "multi_central": ["A9", "A14", "A10"],
    "multi_commitment": ["A25", "B20"],
    "no_commitment": ["A17", "A21"],
    "letter_commitment": ["B14", "B15"],
}


def page(
    rect: tuple[int, int, int, int] | None = None, colour: tuple[int, int, int] = YELLOW
) -> Pixels:
    """White BGR page with an optional filled rectangle (top, left, height, width)."""
    image = np.full((PAGE_H, PAGE_W, 3), 255, np.uint8)
    if rect is not None:
        top, left, h, w = rect
        image[top : top + h, left : left + w] = colour
    return image


def all_labels() -> list[str]:
    return [f"A{i}" for i in range(1, 27)] + [f"B{i}" for i in range(1, 23)]


def write_draft(
    path: Path,
    *,
    blank: Collection[tuple[str, str]] = (),
    empty: Collection[tuple[str, str]] = (),
    labels: list[str] | None = None,
) -> None:
    """A draft over `labels` (default: all 48 pages); `blank` rows stay unconfirmed and `empty`
    rows carry the empty token."""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(DRAFT_COLUMNS))
        writer.writeheader()
        for label in labels or all_labels():
            for key in FieldKey:
                if (label, key.value) in blank:
                    value = ""
                elif (label, key.value) in empty:
                    value = EMPTY_TOKEN
                else:
                    value = f"v-{label}-{key.value}"
                writer.writerow(
                    {
                        "page_label": label,
                        "field": key.value,
                        "proposed_value": "",
                        "crop_path": "",
                        "highlighted": "0",
                        "confirmed_value": value,
                        "notes": "",
                    }
                )


class FakeDocuments:
    """DocumentPort over in-memory pages: page 0 carries a highlight, the others are clean."""

    name = "fake"

    def __init__(self) -> None:
        self.pages = [page((50, 100, 20, 100)), page()]

    def inspect_pdf(self, data: bytes) -> int:
        return len(self.pages)

    def render_pdf_page(self, data: bytes, index: int, *, dpi: int) -> EncodedImage:
        return encode_png(self.pages[index])

    def decode_image(self, data: bytes) -> EncodedImage:
        return encode_png(self.pages[0])


def fake_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "samples_part1.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic")
    return pdf


# --------------------------------------------------------------------------- yellow mask


def test_yellow_mask_finds_synthetic_highlight() -> None:
    image = page((50, 100, 20, 100))

    mask = yellow_mask(image)
    found = find_highlights(image)

    assert int(np.count_nonzero(mask)) == 20 * 100
    assert len(found) == 1
    y0, x0, y1, x1 = found[0].box
    assert (y0, x0) == pytest.approx((50 / PAGE_H, 100 / PAGE_W))
    assert (y1, x1) == pytest.approx((70 / PAGE_H, 200 / PAGE_W))


def test_pale_grey_rectangle_is_not_detected() -> None:
    image = page((50, 100, 40, 200), colour=(215, 215, 215))

    assert int(np.count_nonzero(yellow_mask(image))) == 0
    assert find_highlights(image) == []


@pytest.mark.parametrize(
    ("height", "width", "expected"),
    [(10, 20, 0), (20, 20, 0), (21, 20, 1)],
    ids=["200px-ignored", "400px-boundary-ignored", "420px-kept"],
)
def test_small_blobs_ignored(height: int, width: int, expected: int) -> None:
    assert len(find_highlights(page((10, 10, height, width)))) == expected


def test_yellow_mask_rejects_grayscale_input() -> None:
    with pytest.raises(EvalInputError):
        yellow_mask(np.zeros((10, 10), np.uint8))


# --------------------------------------------------------------------------- attach


FIELD_BOXES: dict[FieldKey, Box] = {
    FieldKey.receipt_date: (0.10, 0.10, 0.20, 0.40),
    FieldKey.central_number: (0.50, 0.10, 0.60, 0.40),
}


@pytest.mark.parametrize(
    ("highlight", "expected"),
    [
        ((0.11, 0.12, 0.19, 0.38), FieldKey.receipt_date),  # overlaps
        ((0.51, 0.12, 0.59, 0.38), FieldKey.central_number),  # overlaps the other box
        ((0.22, 0.10, 0.26, 0.40), FieldKey.receipt_date),  # no overlap, nearest within range
        ((0.90, 0.80, 0.95, 0.95), None),  # no overlap and far from every field
    ],
    ids=["overlap-a", "overlap-b", "nearest", "too-far"],
)
def test_attach_field_uses_overlap_then_nearest(highlight: Box, expected: FieldKey | None) -> None:
    assert attach_field(highlight, FIELD_BOXES) == expected


def test_attach_field_without_boxes_is_unassigned() -> None:
    assert attach_field((0.1, 0.1, 0.2, 0.2), {}) is None


# --------------------------------------------------------------------------- propose


def test_propose_emits_a_row_for_every_field_of_every_page(tmp_path: Path) -> None:
    draft = propose_labels(
        [fake_pdf(tmp_path)], tmp_path / "out", documents=FakeDocuments(), dpi=100
    )

    rows = list(csv.DictReader(draft.open(encoding="utf-8-sig")))

    assert len(rows) == 2 * len(FieldKey)
    assert {r["page_label"] for r in rows} == {"A1", "A2"}
    assert all(r["proposed_value"] == "" and r["confirmed_value"] == "" for r in rows)
    assert list(rows[0]) == list(DRAFT_COLUMNS)


def test_propose_attaches_highlight_crop_to_located_field(tmp_path: Path) -> None:
    box: Box = (50 / PAGE_H, 100 / PAGE_W, 70 / PAGE_H, 200 / PAGE_W)

    def locator(label: str, png: bytes) -> dict[FieldKey, Box]:
        return {FieldKey.receipt_date: box}

    out = tmp_path / "out"
    draft = propose_labels(
        [fake_pdf(tmp_path)], out, documents=FakeDocuments(), locator=locator, dpi=100
    )

    rows = {
        (r["page_label"], r["field"]): r for r in csv.DictReader(draft.open(encoding="utf-8-sig"))
    }
    hit = rows[("A1", "receipt_date")]
    assert hit["highlighted"] == "1"
    assert (out / hit["crop_path"]).is_file()
    assert rows[("A2", "receipt_date")]["highlighted"] == "0"
    assert rows[("A1", "sender")]["highlighted"] == "0"
    assert rows[("A1", "sender")]["crop_path"] == ""  # the locator did not place the sender


def test_propose_lists_highlight_without_locator_as_unassigned(tmp_path: Path) -> None:
    out = tmp_path / "out"
    draft = propose_labels([fake_pdf(tmp_path)], out, documents=FakeDocuments(), dpi=100)

    unassigned = list(
        csv.DictReader((out / "unassigned_highlights.csv").open(encoding="utf-8-sig"))
    )
    rows = list(csv.DictReader(draft.open(encoding="utf-8-sig")))

    assert [u["page_label"] for u in unassigned] == ["A1"]
    assert (out / unassigned[0]["crop_path"]).is_file()
    assert all(r["highlighted"] == "0" for r in rows)
    assert all("unassigned" in r["notes"] for r in rows if r["page_label"] == "A1")


def test_propose_refuses_pdf_without_page_label(tmp_path: Path) -> None:
    other = tmp_path / "scan.pdf"
    other.write_bytes(b"%PDF-1.4 synthetic")

    with pytest.raises(EvalInputError):
        propose_labels([other], tmp_path / "out", documents=FakeDocuments(), dpi=100)


def test_propose_refuses_missing_pdf(tmp_path: Path) -> None:
    with pytest.raises(EvalInputError):
        propose_labels(
            [tmp_path / "samples_part1.pdf"], tmp_path / "out", documents=FakeDocuments()
        )


# --------------------------------------------------------------------------- finalize


def test_finalize_rejects_incomplete_draft(tmp_path: Path) -> None:
    draft, out = tmp_path / "draft.csv", tmp_path / "gold.csv"
    write_draft(draft, blank={("A3", "national_id"), ("B7", "receipt_date")})

    with pytest.raises(GoldIncomplete) as caught:
        finalize(draft, out)

    assert caught.value.code == "GOLD_INCOMPLETE"
    assert "2 unconfirmed" in caught.value.detail
    assert "A3 national_id" in caught.value.detail
    assert not out.exists()


def test_finalize_rejects_whitespace_only_value(tmp_path: Path) -> None:
    draft = tmp_path / "draft.csv"
    write_draft(draft, labels=["A1"])
    text = draft.read_text(encoding="utf-8-sig").replace("v-A1-sender", "   ")
    draft.write_text(text, encoding="utf-8-sig")

    with pytest.raises(GoldIncomplete):
        finalize(draft, tmp_path / "gold.csv")


def test_finalize_rejects_page_missing_a_field(tmp_path: Path) -> None:
    draft = tmp_path / "draft.csv"
    write_draft(draft, labels=["A1"])
    kept = [ln for ln in draft.read_text(encoding="utf-8-sig").splitlines() if "sender" not in ln]
    draft.write_text("\n".join(kept) + "\n", encoding="utf-8-sig")

    with pytest.raises(EvalInputError, match="A1"):
        finalize(draft, tmp_path / "gold.csv")


def test_empty_token_is_preserved(tmp_path: Path) -> None:
    draft, out = tmp_path / "draft.csv", tmp_path / "gold.csv"
    write_draft(draft, empty={("A17", "commitment_number")})

    finalize(draft, out)

    rows = {(r.page, r.field): r.value for r in read_rows(out)}
    assert rows[("A17", FieldKey.commitment_number)] == EMPTY_TOKEN
    assert rows[("A17", FieldKey.sender)] == "v-A17-sender"


def test_finalize_reports_per_field_empty_counts(tmp_path: Path) -> None:
    draft, out = tmp_path / "draft.csv", tmp_path / "gold.csv"
    write_draft(
        draft,
        empty={
            ("A17", "commitment_number"),
            ("A18", "commitment_number"),
            ("A19", "commitment_number"),
            ("B7", "receipt_date"),
            ("B8", "receipt_date"),
        },
    )

    stats = finalize(draft, out)

    assert stats.rows == 48 * len(FieldKey)
    assert stats.pages == 48
    assert stats.empty_by_field["commitment_number"] == 3
    assert stats.empty_by_field["receipt_date"] == 2
    assert stats.empty_by_field["sender"] == 0
    assert stats.render() == (
        "gold: 336 rows, 48 pages, empty values: receipt_date 2, commitment_number 3"
    )


def test_finalized_gold_is_readable_by_run_eval_and_calibrate(tmp_path: Path) -> None:
    draft, out = tmp_path / "draft.csv", tmp_path / "gold.csv"
    write_draft(draft, labels=["A1", "B2"], empty={("A1", "commitment_number")})

    finalize(draft, out)

    evaluated = run_eval_read_gold(out)
    assert evaluated["A1"][FieldKey.commitment_number] is None  # <EMPTY> means blank on the form
    assert evaluated["B2"][FieldKey.sender] == "v-B2-sender"
    calibrated = calibrate_read_gold(out)
    assert calibrated[("A1", "commitment_number")][0] == ""
    assert calibrated[("B2", "sender")][0] == "v-B2-sender"


def test_finalize_orders_rows_by_page_then_field(tmp_path: Path) -> None:
    draft, out = tmp_path / "draft.csv", tmp_path / "gold.csv"
    write_draft(draft, labels=["B2", "A10", "A9"])

    finalize(draft, out)

    pages = [r.page for r in read_rows(out)]
    assert pages[:: len(FieldKey)] == ["A9", "A10", "B2"]


# --------------------------------------------------------------------------- held-out


def gold_file(tmp_path: Path) -> Path:
    draft, gold = tmp_path / "draft.csv", tmp_path / "gold.csv"
    write_draft(draft)
    finalize(draft, gold)
    return gold


def held_out_set(labels: list[str]) -> dict[str, list[str]]:
    """Which family each chosen label belongs to (a label may belong to several)."""
    return {
        name: [x for x in labels if x in members] for name, members in SYNTHETIC_FAMILIES.items()
    }


def test_held_out_is_stratified_and_seeded(tmp_path: Path) -> None:
    gold = gold_file(tmp_path)

    first = pick_held_out(gold, tmp_path / "a.txt", families=SYNTHETIC_FAMILIES)
    again = pick_held_out(gold, tmp_path / "b.txt", families=SYNTHETIC_FAMILIES)

    assert first == again
    assert (tmp_path / "a.txt").read_bytes() == (tmp_path / "b.txt").read_bytes()
    covered = held_out_set(first)
    assert len(covered["sap"]) >= 2
    assert len(covered["handwriting_heavy"]) >= 2
    for family in (
        "faint_stamp",
        "fax_grade",
        "multi_central",
        "multi_commitment",
        "no_commitment",
        "letter_commitment",
    ):
        assert covered[family], f"no {family} page in the held-out set"
    tagged = {x for members in SYNTHETIC_FAMILIES.values() for x in members}
    assert len([x for x in first if x not in tagged]) == 5  # ordinary printed pages


def test_held_out_file_has_exactly_fifteen_unique_labels(tmp_path: Path) -> None:
    gold = gold_file(tmp_path)
    out = tmp_path / "held_out.txt"

    pick_held_out(gold, out, families=SYNTHETIC_FAMILIES)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == HELD_OUT_SIZE == len(set(lines))
    assert set(lines) <= set(all_labels())
    assert out.read_bytes().endswith(b"\n") and b"\r" not in out.read_bytes()


def test_held_out_changes_with_the_seed(tmp_path: Path) -> None:
    gold = gold_file(tmp_path)

    default = pick_held_out(gold, tmp_path / "a.txt", families=SYNTHETIC_FAMILIES)
    other = pick_held_out(gold, tmp_path / "b.txt", seed=1, families=SYNTHETIC_FAMILIES)

    assert default != other


def test_held_out_refuses_when_a_family_has_no_page(tmp_path: Path) -> None:
    gold = gold_file(tmp_path)
    families = {**SYNTHETIC_FAMILIES, "fax_grade": []}
    out = tmp_path / "held_out.txt"

    with pytest.raises(EvalInputError, match="fax_grade"):
        pick_held_out(gold, out, families=families)

    assert not out.exists()


def test_held_out_never_writes_a_value(tmp_path: Path) -> None:
    gold = gold_file(tmp_path)
    out = tmp_path / "held_out.txt"

    pick_held_out(gold, out, families=SYNTHETIC_FAMILIES)

    assert "v-" not in out.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- cross-check


def test_cross_check_lists_disagreements_without_values(tmp_path: Path) -> None:
    mine, hers = tmp_path / "mine.csv", tmp_path / "hers.csv"
    write_draft(mine, labels=["A1", "A2"])
    write_draft(hers, labels=["A1", "A2"])
    hers.write_text(
        hers.read_text(encoding="utf-8-sig").replace("v-A2-national_id", "other"),
        encoding="utf-8-sig",
    )

    compared, differing = cross_check(mine, hers)

    assert compared == 2 * len(FieldKey)
    assert differing == [("A2", "national_id")]


def test_cross_check_skips_rows_not_confirmed_by_both(tmp_path: Path) -> None:
    mine, hers = tmp_path / "mine.csv", tmp_path / "hers.csv"
    write_draft(mine, labels=["A1"], blank={("A1", "sender")})
    write_draft(hers, labels=["A1"])
    hers.write_text(
        hers.read_text(encoding="utf-8-sig").replace("v-A1-sender", "x"), encoding="utf-8-sig"
    )

    compared, differing = cross_check(mine, hers)

    assert compared == len(FieldKey) - 1
    assert differing == []


def test_cross_check_cli_prints_locations_not_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mine, hers = tmp_path / "mine.csv", tmp_path / "hers.csv"
    write_draft(mine, labels=["A1"])
    write_draft(hers, labels=["A1"])
    hers.write_text(
        hers.read_text(encoding="utf-8-sig").replace("v-A1-last_name", "secret-name"),
        encoding="utf-8-sig",
    )

    code = main(["--cross-check", str(mine), str(hers)])

    shown = capsys.readouterr().out
    assert code == 0
    assert "A1 last_name" in shown
    assert "secret-name" not in shown and "v-A1" not in shown


def test_finalize_cli_prints_counts_and_returns_2_when_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    draft, out = tmp_path / "draft.csv", tmp_path / "gold.csv"
    write_draft(draft, labels=["A1"], blank={("A1", "sender")})

    code = main(["--finalize", "--draft", str(draft), "--out", str(out)])

    captured = capsys.readouterr()
    assert code == 2
    assert "GOLD_INCOMPLETE" in captured.err
    assert "v-A1" not in captured.out + captured.err
    assert not out.exists()
