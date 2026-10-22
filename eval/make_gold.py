"""Gold-set tooling (PLAN §10.1-10.2, T7). EVALUATION ONLY - nothing under backend/core imports it.

    python -m eval.make_gold --propose
    python -m eval.make_gold --finalize --draft eval/results/gold_draft.csv --out eval/gold.csv
    python -m eval.make_gold --pick-held-out --gold eval/gold.csv --out eval/held_out.txt

The lab highlighted fields in yellow on many pages. The highlighting is inconsistent (A9 has six
marks, A20 almost none), so it is used here to *propose* labels and never at runtime (project
rule §4). A human confirms every one of the 7 fields of every page; a field that is genuinely blank
on the form is written `<EMPTY>` so "no value" differs from "not yet labelled".

Privacy: the draft and the gold file hold transcribed values and live in git-ignored paths. The
commands print counts and page labels only - never a field value.

Edit the draft in a text editor, or import every column as TEXT: a spreadsheet silently drops the
leading zero of an ID such as 011223344.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass
from pathlib import Path
import random
import re
import sys
from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray
import pypdfium2

from backend.core.errors import SafeLineError
from backend.core.models import Box, FieldKey
from eval.run_eval import DEFAULT_GOLD, DEFAULT_HELD_OUT, DEFAULT_PDFS, EMPTY_TOKEN, label_of_page

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR: Final = REPO_ROOT / "eval" / "results"
DEFAULT_STRATA: Final = REPO_ROOT / "eval" / "strata.csv"

# Spec constants of PLAN §10.1 (the gold-set procedure, not engine thresholds).
# OpenCV hue runs 0-179, so the plan's "H 20-40" is yellow on this scale. The plan's bounds
# S>80 and V>120 are exclusive, hence 81 and 121.
YELLOW_LOW: Final = (20, 81, 121)
YELLOW_HIGH: Final = (40, 255, 255)
MIN_BLOB_AREA_PX: Final = 400  # smaller components are scanner speckle
CROP_PADDING: Final = 0.02  # fraction of the page side added around a crop
RENDER_DPI: Final = 300  # the raster density the engine reads at, so 400 px² means the same
THUMB_WIDTH_PX: Final = 900  # fallback page image when a field has no usable box
DEFAULT_SEED: Final = 20261018  # PLAN §10.3: both developers get the same held-out set
DEFAULT_HELD_OUT_SIZE: Final = 15
DEFAULT_EXPECTED_PAGES: Final = 48

DRAFT_COLUMNS: Final = (
    "page_label",
    "field",
    "proposed_value",
    "crop_path",
    "highlighted",
    "confirmed_value",
    "notes",
)
GOLD_COLUMNS: Final = ("page_id", "field", "value", "split")
SPLIT_DEV: Final = "dev"
SPLIT_HELD_OUT: Final = "held_out"

# PLAN §10.2 families and how many held-out pages each needs; untagged pages fill the rest.
FAMILY_QUOTAS: Final[dict[str, int]] = {
    "sap": 2,
    "handwriting_heavy": 2,
    "faint_stamp": 1,
    "fax_grade": 1,
    "multi_central": 1,
    "multi_commitment": 1,
    "no_commitment": 1,
    "letter_commitment": 1,
}
_MULTI_SEPARATOR: Final = ";"  # gold convention: several values on one field are `a;b;c`
_HAS_LETTER: Final = re.compile(r"[A-Za-z\u05d0-\u05ea]")
_LABEL: Final = re.compile(r"^([A-Za-z]+)(\d+)$")
_FIELD_ORDER: Final = {key.value: i for i, key in enumerate(FieldKey)}


class GoldInputError(SafeLineError):
    """The draft, the gold file or the strata are incomplete or unusable."""

    code = "GOLD_INPUT"


class HeldOutCoverageError(SafeLineError):
    """Too few pages exist for a held-out family; a quota is never silently relaxed."""

    code = "HELD_OUT_COVERAGE"


@dataclass(frozen=True)
class Proposal:
    """What the engine proposed for one field of one page (tool-internal, never logged)."""

    value: str | None
    box: Box | None  # normalized, in the engine's corrected page frame
    rotated: bool  # the engine rotated the page, so `box` no longer matches the raw render


@dataclass(frozen=True)
class GoldStats:
    """Counts a person can sanity check; holds no value."""

    rows: int
    pages: int
    empty_by_field: dict[str, int]

    def render(self) -> str:
        empty = ", ".join(f"{k} {v}" for k, v in self.empty_by_field.items() if v) or "none"
        return f"gold: {self.rows} rows, {self.pages} pages, empty values: {empty}"


# --------------------------------------------------------------------------- highlights


def yellow_mask(image: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """255 where a BGR pixel is highlighter yellow (HSV H 20-40, S>80, V>120), else 0.
    EVALUATION ONLY - never called from backend/core. Pale greys fail the saturation test."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(YELLOW_LOW), np.array(YELLOW_HIGH))
    return np.asarray(mask, dtype=np.uint8)


def find_highlights(image: NDArray[np.uint8], *, min_area: int = MIN_BLOB_AREA_PX) -> list[Box]:
    """Normalized boxes (y_min, x_min, y_max, x_max) of yellow components larger than `min_area`
    pixels, top to bottom. Smaller blobs are speckle and dropped; a blank page gives []."""
    height, width = image.shape[:2]
    count, _, stats, _ = cv2.connectedComponentsWithStats(yellow_mask(image), connectivity=8)
    boxes: list[Box] = []
    for i in range(1, count):  # 0 is the background
        x, y, w, h, area = (int(v) for v in stats[i])
        if area > min_area:
            boxes.append((y / height, x / width, (y + h) / height, (x + w) / width))
    return sorted(boxes)


def iou(a: Box, b: Box) -> float:
    """Intersection over union of two normalized boxes; 0.0 when they do not overlap."""
    top, left = max(a[0], b[0]), max(a[1], b[1])
    bottom, right = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, bottom - top) * max(0.0, right - left)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _centre_distance(a: Box, b: Box) -> float:
    return float(np.hypot((a[0] + a[2] - b[0] - b[2]) / 2, (a[1] + a[3] - b[1] - b[3]) / 2))


def attach_highlights(
    highlights: Sequence[Box], fields: Mapping[FieldKey, Box]
) -> tuple[dict[FieldKey, list[Box]], int]:
    """Give each highlight to the field whose box overlaps it most (ties: nearest centre).
    A highlight that overlaps no field is unattached. Returns (attached, unattached count)."""
    attached: dict[FieldKey, list[Box]] = {}
    unattached = 0
    for hl in highlights:
        best_key: FieldKey | None = None
        best = (0.0, 0.0)
        for key, box in fields.items():
            score = (iou(hl, box), -_centre_distance(hl, box))
            if score[0] > 0.0 and (best_key is None or score > best):
                best_key, best = key, score
        if best_key is None:
            unattached += 1
        else:
            attached.setdefault(best_key, []).append(hl)
    return attached, unattached


def union_box(boxes: Sequence[Box]) -> Box:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def crop(image: NDArray[np.uint8], box: Box, *, padding: float = CROP_PADDING) -> NDArray[np.uint8]:
    """The region of `box` grown by `padding` of the page side, clamped to the page."""
    height, width = image.shape[:2]
    y0 = max(0, int((box[0] - padding) * height))
    x0 = max(0, int((box[1] - padding) * width))
    y1 = min(height, int(np.ceil((box[2] + padding) * height)))
    x1 = min(width, int(np.ceil((box[3] + padding) * width)))
    out: NDArray[np.uint8] = image[y0:y1, x0:x1]
    return out


# --------------------------------------------------------------------------- proposals


def pipeline_proposals(pdfs: Sequence[Path]) -> dict[str, dict[FieldKey, Proposal]]:
    """Run the local-only engine over the sample PDFs and keep value and box of every field.
    Raises GoldInputError when no PDF is present."""
    import uuid

    from backend.core.pipeline.orchestrator import apply_labels, iter_outcomes
    from backend.core.pipeline.s0_intake import UploadedBlob, intake
    from eval.run_eval import prepare_services

    present = [p for p in pdfs if p.is_file()]
    if not present:
        raise GoldInputError("sample PDFs not found (reference/samples_part*.pdf)")
    svc = prepare_services(None, local_only=True, ablation=None, models=None)
    job_id = f"gold-{uuid.uuid4().hex[:12]}"
    proposals: dict[str, dict[FieldKey, Proposal]] = {}
    try:
        result = intake(
            [UploadedBlob(filename=p.name, data=p.read_bytes()) for p in present],
            settings=svc.settings,
            documents=svc.documents,
            storage=svc.storage,
            job_id=job_id,
        )
        raws = apply_labels(result.pages, lambda r: label_of_page(r.source_file, r.page_index))
        for outcome in iter_outcomes(raws, services=svc):
            if outcome.label is None:
                continue
            rotated = "rotate" in outcome.corrections
            proposals[outcome.label] = {
                key: Proposal(ev.result.value, ev.result.box, rotated)
                for key, ev in outcome.evaluations.items()
            }
    finally:
        svc.storage.purge_job(job_id)
    return proposals


def render_pages(pdf: Path, *, dpi: int = RENDER_DPI) -> list[tuple[str, NDArray[np.uint8]]]:
    """(label, BGR raster) per page of a sample PDF. A PDF not named like a sample has no labels
    and gives []. Rendering is for evaluation crops only."""
    out: list[tuple[str, NDArray[np.uint8]]] = []
    document = pypdfium2.PdfDocument(str(pdf))
    try:
        for index in range(len(document)):
            label = label_of_page(pdf.name, index)
            if label is None:
                continue
            page = document[index]
            try:
                rgb = np.asarray(page.render(scale=dpi / 72.0).to_pil().convert("RGB"))
            finally:
                page.close()
            out.append((label, np.asarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), dtype=np.uint8)))
    finally:
        document.close()
    return out


def _write_png(path: Path, image: NDArray[np.uint8]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise GoldInputError(f"could not write {path.name}")


def _label_key(label: str) -> tuple[str, int]:
    match = _LABEL.match(label)
    return (match.group(1), int(match.group(2))) if match else (label, 0)


def propose_labels(
    pdfs: Sequence[Path],
    out_dir: Path,
    *,
    proposals: Mapping[str, Mapping[FieldKey, Proposal]] | None = None,
    force: bool = False,
) -> Path:
    """Write `gold_draft.csv` and a crop per field into `out_dir`: 7 rows per page, with
    `confirmed_value` blank. `proposals` come from the local engine unless given (tests).
    Refuses to overwrite an existing draft unless `force`, because it holds a person's work.
    Raises GoldInputError."""
    draft = out_dir / "gold_draft.csv"
    if draft.exists() and not force:
        raise GoldInputError("gold_draft.csv already exists; --force would discard confirmed work")
    present = [p for p in pdfs if p.is_file()]
    if not present:
        raise GoldInputError("sample PDFs not found (reference/samples_part*.pdf)")
    if proposals is None:
        proposals = pipeline_proposals(present)

    rows: list[dict[str, str]] = []
    for pdf in present:
        for label, image in render_pages(pdf):
            rows += _page_rows(label, image, proposals.get(label, {}), out_dir)
    if not rows:
        raise GoldInputError("no labelled page found; PDFs must be named like samples_partN.pdf")
    rows.sort(key=lambda r: (_label_key(r["page_label"]), _FIELD_ORDER[r["field"]]))
    out_dir.mkdir(parents=True, exist_ok=True)
    with draft.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=DRAFT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return draft


def _page_rows(
    label: str,
    image: NDArray[np.uint8],
    page_proposals: Mapping[FieldKey, Proposal],
    out_dir: Path,
) -> list[dict[str, str]]:
    """The 7 draft rows of one page, writing each field crop (or the page image) under crops/."""
    crops = out_dir / "crops"
    usable = {k: p.box for k, p in page_proposals.items() if p.box is not None and not p.rotated}
    attached, unattached = attach_highlights(find_highlights(image), usable)
    rotated = any(p.rotated for p in page_proposals.values())

    page_name = f"{label}_page.png"
    scale = THUMB_WIDTH_PX / image.shape[1]
    page_written = False
    rows: list[dict[str, str]] = []
    for key in FieldKey:
        proposal = page_proposals.get(key)
        hits = attached.get(key, [])
        box = union_box(hits) if hits else usable.get(key)
        notes: list[str] = []
        if proposal is None:
            notes.append("no proposal")
        if rotated:
            notes.append("page was rotated: no field crop, use the page image")
        if unattached:
            notes.append(f"{unattached} highlight(s) matched no field")
        if box is not None:
            image_name = f"{label}_{key.value}.png"
            _write_png(crops / image_name, crop(image, box))
        else:
            image_name = page_name
            if not page_written:
                small = np.asarray(
                    cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA),
                    dtype=np.uint8,
                )
                _write_png(crops / page_name, small)
                page_written = True
        rows.append(
            {
                "page_label": label,
                "field": key.value,
                "proposed_value": (proposal.value or "") if proposal else "",
                "crop_path": (crops / image_name).relative_to(out_dir).as_posix(),
                "highlighted": "yes" if hits else "no",
                "confirmed_value": "",
                "notes": "; ".join(notes),
            }
        )
    return rows


# --------------------------------------------------------------------------- finalize


def _read_draft(draft: Path) -> list[dict[str, str]]:
    if not draft.is_file():
        raise GoldInputError(f"draft not found: {draft.name}")
    with draft.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = {"page_label", "field", "confirmed_value"} - set(reader.fieldnames or [])
        if missing:
            raise GoldInputError(f"{draft.name} lacks columns: {sorted(missing)}")
        return list(reader)


def _write_gold(out: Path, rows: Sequence[tuple[str, str, str, str]]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(GOLD_COLUMNS)
        writer.writerows(rows)


def finalize(draft: Path, out: Path, *, expected_pages: int | None = None) -> GoldStats:
    """Turn a fully confirmed draft into `gold.csv` (page_id, field, value, split).

    Refuses - writing nothing - when any row is unconfirmed, a field is unknown or duplicated, a
    page lacks one of the 7 fields, or `expected_pages` is set and not met. `<EMPTY>` is kept
    verbatim. Raises GoldInputError naming page labels and counts, never a value."""
    rows = _read_draft(draft)
    seen: set[tuple[str, str]] = set()
    pending: list[str] = []
    for row in rows:
        label, raw_field = row["page_label"].strip(), row["field"].strip()
        if raw_field not in _FIELD_ORDER:
            raise GoldInputError(f"{draft.name}: unknown field {raw_field!r}")
        if (label, raw_field) in seen:
            raise GoldInputError(f"{draft.name}: duplicate row for {label} {raw_field}")
        seen.add((label, raw_field))
        if not (row["confirmed_value"] or "").strip():
            pending.append(f"{label}/{raw_field}")
    pages = sorted({label for label, _ in seen}, key=_label_key)
    absent = [f"{p}/{k}" for p in pages for k in _FIELD_ORDER if (p, k) not in seen]
    if absent:
        raise GoldInputError(f"{len(absent)} field row(s) missing, e.g. {', '.join(absent[:5])}")
    if pending:
        raise GoldInputError(f"{len(pending)} unconfirmed row(s), e.g. {', '.join(pending[:5])}")
    if expected_pages is not None and len(pages) != expected_pages:
        raise GoldInputError(f"expected {expected_pages} pages, the draft has {len(pages)}")

    empty: Counter[str] = Counter()
    gold_rows: list[tuple[str, str, str, str]] = []
    for row in rows:
        value, field = row["confirmed_value"].strip(), row["field"].strip()
        if value == EMPTY_TOKEN:
            empty[field] += 1
        gold_rows.append((row["page_label"].strip(), field, value, SPLIT_DEV))
    gold_rows.sort(key=lambda r: (_label_key(r[0]), _FIELD_ORDER[r[1]]))
    _write_gold(out, gold_rows)
    return GoldStats(
        rows=len(gold_rows), pages=len(pages), empty_by_field={k: empty[k] for k in _FIELD_ORDER}
    )


# --------------------------------------------------------------------------- held-out


def read_gold_pages(gold: Path) -> dict[str, dict[str, str]]:
    """label -> {field: value} from a finalized gold.csv. Raises GoldInputError."""
    if not gold.is_file():
        raise GoldInputError(f"gold file not found: {gold.name}")
    pages: dict[str, dict[str, str]] = {}
    with gold.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not {"page_id", "field", "value"} <= set(reader.fieldnames or []):
            raise GoldInputError(f"{gold.name} lacks page_id/field/value columns")
        for row in reader:
            pages.setdefault(row["page_id"].strip(), {})[row["field"].strip()] = row[
                "value"
            ].strip()
    if not pages:
        raise GoldInputError(f"{gold.name} holds no rows")
    return pages


def read_strata(path: Path | None) -> dict[str, set[str]]:
    """label -> families from a hand-kept `page_label,families` CSV (families split on `;`).
    A missing file means no hand tags. An unknown family raises GoldInputError."""
    if path is None or not path.is_file():
        return {}
    tags: dict[str, set[str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            families = {f.strip() for f in (row.get("families") or "").split(";") if f.strip()}
            unknown = families - FAMILY_QUOTAS.keys()
            if unknown:
                raise GoldInputError(f"{path.name}: unknown family {sorted(unknown)}")
            tags.setdefault(row["page_label"].strip(), set()).update(families)
    return tags


def derive_families(values: Mapping[str, str]) -> set[str]:
    """The families that follow from a page's gold values alone (the rest need hand tags)."""
    families: set[str] = set()
    commitment = values.get(FieldKey.commitment_number.value, "")
    if commitment == EMPTY_TOKEN:
        families.add("no_commitment")
    elif _MULTI_SEPARATOR in commitment:
        families.add("multi_commitment")
    if _HAS_LETTER.search(commitment.replace(EMPTY_TOKEN, "")):
        families.add("letter_commitment")
    if _MULTI_SEPARATOR in values.get(FieldKey.central_number.value, ""):
        families.add("multi_central")
    return families


def select_held_out(
    page_families: Mapping[str, set[str]],
    *,
    n: int = DEFAULT_HELD_OUT_SIZE,
    seed: int = DEFAULT_SEED,
) -> list[str]:
    """Seeded stratified choice of `n` labels: every family reaches its quota (rarest first, a
    page counts for every family it carries), then ordinary pages fill the rest. Same inputs and
    seed give the same labels. Raises HeldOutCoverageError rather than relax a quota."""
    rng = random.Random(seed)
    labels = sorted(page_families, key=_label_key)
    if len(labels) < n:
        raise HeldOutCoverageError(f"only {len(labels)} pages for a held-out set of {n}")
    chosen: list[str] = []
    for family in sorted(FAMILY_QUOTAS, key=lambda f: (_eligible(page_families, f), f)):
        have = sum(1 for lb in chosen if family in page_families[lb])
        pool = [lb for lb in labels if lb not in chosen and family in page_families[lb]]
        need = FAMILY_QUOTAS[family] - have
        if need > len(pool):
            raise HeldOutCoverageError(
                f"family {family}: need {need} more page(s), {len(pool)} left"
            )
        if need > 0:
            chosen += rng.sample(pool, need)
    rest = [lb for lb in labels if lb not in chosen]
    ordinary = [lb for lb in rest if not page_families[lb]]
    others = [lb for lb in rest if page_families[lb]]
    missing = n - len(chosen)
    if missing < 0:
        raise HeldOutCoverageError(f"quotas need {len(chosen)} pages, more than n={n}")
    take = rng.sample(ordinary, min(missing, len(ordinary)))
    take += rng.sample(others, missing - len(take))
    return sorted(chosen + take, key=_label_key)


def _eligible(page_families: Mapping[str, set[str]], family: str) -> int:
    return sum(1 for fams in page_families.values() if family in fams)


def pick_held_out(
    gold: Path,
    out: Path,
    n: int = DEFAULT_HELD_OUT_SIZE,
    seed: int = DEFAULT_SEED,
    *,
    strata: Path | None = DEFAULT_STRATA,
) -> list[str]:
    """Choose the held-out pages from `gold` and write their labels (only labels) to `out`.
    Families come from the gold values plus the optional `strata` hand tags.
    Raises GoldInputError / HeldOutCoverageError and writes nothing."""
    pages = read_gold_pages(gold)
    tags = read_strata(strata)
    stray = sorted(set(tags) - set(pages), key=_label_key)
    if stray:
        raise GoldInputError(f"strata names page(s) absent from gold: {', '.join(stray[:5])}")
    families = {lb: derive_families(vals) | tags.get(lb, set()) for lb, vals in pages.items()}
    labels = select_held_out(families, n=n, seed=seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# Held-out pages (PLAN 10.2). Labels only. Never tune on these.\n"
        + "\n".join(labels)
        + "\n",
        encoding="utf-8",
    )
    return labels


# --------------------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    """CLI. Exit 0 on success, 2 on unusable input. Prints counts and labels, never values."""
    p = argparse.ArgumentParser(description="Build the gold set and choose the held-out pages.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--propose", action="store_true", help="write the draft and crops (default)")
    mode.add_argument("--finalize", action="store_true", help="confirmed draft -> gold.csv")
    mode.add_argument("--pick-held-out", action="store_true", help="gold.csv -> held_out.txt")
    p.add_argument("--pdfs", type=Path, nargs="+", default=list(DEFAULT_PDFS))
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--force", action="store_true", help="overwrite an existing draft")
    p.add_argument("--draft", type=Path, default=DEFAULT_OUT_DIR / "gold_draft.csv")
    p.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--expected-pages", type=int, default=DEFAULT_EXPECTED_PAGES)
    p.add_argument("--strata", type=Path, default=DEFAULT_STRATA)
    p.add_argument("--n", type=int, default=DEFAULT_HELD_OUT_SIZE)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = p.parse_args(argv)
    try:
        if args.finalize:
            stats = finalize(
                args.draft, args.out or DEFAULT_GOLD, expected_pages=args.expected_pages
            )
            print(stats.render())
        elif args.pick_held_out:
            labels = pick_held_out(
                args.gold, args.out or DEFAULT_HELD_OUT, args.n, args.seed, strata=args.strata
            )
            print(f"held-out: {' '.join(labels)} ({len(labels)})")
        else:
            draft = propose_labels(args.pdfs, args.out_dir, force=args.force)
            print(f"draft written: {draft.name} (confirm every confirmed_value; use {EMPTY_TOKEN})")
    except SafeLineError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
