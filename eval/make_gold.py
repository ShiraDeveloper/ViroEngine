"""Gold-set tooling: propose labels from the yellow highlighter, finalize, pick the held-out set
(PLAN §10.1-10.2, T7).

    python -m eval.make_gold --propose [--pdfs A.pdf B.pdf] [--locate]
    python -m eval.make_gold --finalize --draft eval/results/gold_draft.csv --out eval/gold.csv
    python -m eval.make_gold --pick-held-out --gold eval/gold.csv --out eval/held_out.txt
    python -m eval.make_gold --cross-check mine.csv hers.csv

EVALUATION ONLY. The yellow mask lives here, in `eval/`, and is used to build the gold set and
nothing else (project rule §4): `backend/core` must never import this module, and
`scripts/check_layering.py` forbids it. Highlighting is inconsistent across the samples and
absent on real forms, so no runtime decision may depend on it.

A draft row carries no guessed value: `proposed_value` is always empty and a human types every
`confirmed_value`. Output (stdout, logs) holds counts, page labels and field names only - never
a field value (project rule §7).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import csv
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Final
import uuid

import cv2
import numpy as np
from numpy.typing import NDArray
from pydantic import Field
import structlog

from backend.adapters.imaging.codec import Pixels, decode_bgr, encode_png
from backend.adapters.imaging.pdf import DocumentReader
from backend.core.errors import SafeLineError
from backend.core.geometry import grow, iou
from backend.core.models import Box, FieldKey, Strict
from backend.core.pipeline.orchestrator import apply_labels, iter_outcomes
from backend.core.pipeline.s0_intake import UploadedBlob, intake
from backend.core.ports.document import DocumentPort
from backend.settings import Settings
from eval.run_eval import (
    DEFAULT_HELD_OUT,
    DEFAULT_PDFS,
    DEFAULT_RESULTS,
    EMPTY_TOKEN,
    REPO_ROOT,
    EvalInputError,
    label_of_page,
    prepare_services,
)

log = structlog.get_logger()

Mask = NDArray[np.uint8]
# (page label, rendered page PNG) -> normalized box per field the engine could place on it.
Locator = Callable[[str, bytes], Mapping[FieldKey, Box]]

DEFAULT_FAMILIES: Final = REPO_ROOT / "config" / "gold_families.json"

# PLAN §10.1 step 1: yellow in OpenCV HSV (H runs 0-179). `S > 80` and `V > 120` are strict, so
# the inclusive lower bounds are one above. A pale grey page tint has S near 0 and is excluded.
YELLOW_LOW: Final = (20, 81, 121)
YELLOW_HIGH: Final = (40, 255, 255)
# PLAN §10.1 step 1: a component this small or smaller is scanner speckle, not a highlighter.
MIN_BLOB_AREA_PX: Final = 400
# PLAN §10.1 step 2: share of the page width/height added around a highlight when cropping.
CROP_PADDING: Final = 0.02
# Structural: a highlight stroke has gaps where black text sits on it; closing with a kernel this
# wide (pixels, 300 DPI) rejoins the stroke without merging neighbouring lines.
CLOSE_KERNEL_PX: Final = 5
# A highlight that overlaps no field box is attached to the nearest one only when it is within
# this distance (normalized page units, centre to centre). Beyond that it is left unassigned for
# the human: a far field is not the governing label, and a wrong attachment hides the highlight.
ATTACH_MAX_DISTANCE: Final = 0.15
# Pipeline corrections that move pixels. A page that needed one has field boxes in a different
# frame from the raw render the highlights are found in, so it gets no engine boxes.
GEOMETRIC_CORRECTIONS: Final = frozenset({"rotate", "perspective", "deskew"})

DRAFT_COLUMNS: Final = (
    "page_label",
    "field",
    "proposed_value",
    "crop_path",
    "highlighted",
    "confirmed_value",
    "notes",
)
# gold.csv: run_eval reads page_label/confirmed_value, calibrate reads page_id/value. `value` is
# `confirmed_value` with the empty token turned into "" (calibrate's "absent on the page").
GOLD_COLUMNS: Final = ("page_label", "field", "confirmed_value", "page_id", "value")
UNASSIGNED_COLUMNS: Final = ("page_label", "crop_path")
UNASSIGNED_NOTE: Final = "page has unassigned highlight(s): see unassigned_highlights.csv"

# PLAN §10.2: held-out size and seed; the seed is shared so both developers get the same set.
HELD_OUT_SIZE: Final = 15
HELD_OUT_SEED: Final = 20261018
# PLAN §10.2 quotas per family. The remainder of the 15 are ordinary printed pages (5).
HELD_OUT_QUOTAS: Final[dict[str, int]] = {
    "sap": 2,
    "handwriting_heavy": 2,
    "faint_stamp": 1,
    "fax_grade": 1,
    "multi_central": 1,
    "multi_commitment": 1,
    "no_commitment": 1,
    "letter_commitment": 1,
}
ORDINARY: Final = "ordinary_printed"

# Structural: how many row locations an error message lists before it summarises.
MAX_LISTED: Final = 10
_LABEL: Final = re.compile(r"^([A-Za-z]+)(\d+)$")
_SPACES: Final = re.compile(r"\s+")


class GoldIncomplete(EvalInputError):
    """`finalize` found a row with no confirmed value; nothing was written."""

    code = "GOLD_INCOMPLETE"


# --------------------------------------------------------------------------- yellow


def yellow_mask(image: Pixels) -> Mask:
    """Binary mask (0/255) of yellow-highlighter pixels in a BGR image: HSV H 20-40, S>80, V>120.
    EVALUATION ONLY - never called from backend/core. Raises EvalInputError for a non-BGR array."""
    if image.ndim != 3 or image.shape[2] != 3:
        raise EvalInputError("yellow_mask needs a BGR image")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask: Any = cv2.inRange(hsv, np.array(YELLOW_LOW, np.uint8), np.array(YELLOW_HIGH, np.uint8))
    return np.asarray(mask, dtype=np.uint8)


@dataclass(frozen=True)
class Highlight:
    """One highlighted blob: its normalized box (y_min, x_min, y_max, x_max) and pixel count."""

    box: Box
    pixels: int


def find_highlights(image: Pixels) -> list[Highlight]:
    """Connected yellow components larger than MIN_BLOB_AREA_PX, top-to-bottom then left-to-right.
    A page with no highlighting returns an empty list, never an error."""
    height, width = image.shape[:2]
    mask = yellow_mask(image)
    kernel = np.ones((CLOSE_KERNEL_PX, CLOSE_KERNEL_PX), np.uint8)
    closed: Any = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    found: list[Highlight] = []
    for i in range(1, count):  # component 0 is the background
        x, y, w, h, pixels = (int(v) for v in stats[i])
        if pixels > MIN_BLOB_AREA_PX:
            found.append(
                Highlight((y / height, x / width, (y + h) / height, (x + w) / width), pixels)
            )
    return sorted(found, key=lambda hl: (hl.box[0], hl.box[1]))


def attach_field(box: Box, field_boxes: Mapping[FieldKey, Box]) -> FieldKey | None:
    """The field whose box overlaps `box` most (IoU); with no overlap, the nearest field centre
    within ATTACH_MAX_DISTANCE; otherwise None. Ties go to the nearer centre."""

    def distance(other: Box) -> float:
        dy = (box[0] + box[2]) / 2 - (other[0] + other[2]) / 2
        dx = (box[1] + box[3]) / 2 - (other[1] + other[3]) / 2
        return math.hypot(dy, dx)

    ranked = sorted(
        ((iou(box, b), distance(b), key.value, key) for key, b in field_boxes.items()),
        key=lambda t: (-t[0], t[1], t[2]),
    )
    if not ranked:
        return None
    best_iou, best_distance, _, key = ranked[0]
    if best_iou > 0.0 or best_distance <= ATTACH_MAX_DISTANCE:
        return key
    return None


# --------------------------------------------------------------------------- propose


def _natural(label: str) -> tuple[str, int]:
    match = _LABEL.match(label)
    return (match.group(1), int(match.group(2))) if match else (label, 0)


def _crop_png(image: Pixels, box: Box) -> bytes:
    """PNG of `box` grown by CROP_PADDING on every side, at least one pixel each way."""
    height, width = image.shape[:2]
    y0, x0, y1, x1 = grow(
        box, top=CROP_PADDING, left=CROP_PADDING, bottom=CROP_PADDING, right=CROP_PADDING
    )
    top, left = int(y0 * height), int(x0 * width)
    bottom, right = max(top + 1, math.ceil(y1 * height)), max(left + 1, math.ceil(x1 * width))
    return encode_png(np.ascontiguousarray(image[top:bottom, left:right])).data


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def propose_labels(
    pdfs: Sequence[Path],
    out_dir: Path,
    *,
    documents: DocumentPort | None = None,
    locator: Locator | None = None,
    dpi: int | None = None,
) -> Path:
    """Render every page, find its highlights and write `<out_dir>/gold_draft.csv` plus crops
    under `crops/`, page images under `pages/` and any highlight no field claimed in
    `unassigned_highlights.csv`. All 7 fields of every page get a row (336 for 48 pages), not
    only highlighted ones; `proposed_value` stays empty (nothing is guessed). With a `locator`
    each field also gets a crop of its engine-found box. Returns the draft path.
    Raises EvalInputError for a missing PDF or one whose name gives no page label."""
    if documents is None or dpi is None:
        settings = Settings()
        documents = documents if documents is not None else DocumentReader(settings=settings)
        dpi = dpi if dpi is not None else settings.render_dpi
    rows: list[dict[str, str]] = []
    unassigned: list[dict[str, str]] = []
    pages = highlights_total = 0
    for pdf in pdfs:
        if not pdf.is_file():
            raise EvalInputError(f"sample PDF not found: {pdf.name}")
        data = pdf.read_bytes()
        for index in range(documents.inspect_pdf(data)):
            label = label_of_page(pdf.name, index)
            if label is None:
                raise EvalInputError(f"{pdf.name} gives no page label (expected samples_partN)")
            encoded = documents.render_pdf_page(data, index, dpi=dpi)
            image = decode_bgr(encoded.data)
            _write(out_dir / "pages" / f"{label}.png", encoded.data)
            page_rows, page_unassigned, found = _propose_page(
                label, image, encoded.data, out_dir, locator
            )
            rows += page_rows
            unassigned += page_unassigned
            pages += 1
            highlights_total += found
            del image
    draft = out_dir / "gold_draft.csv"
    _write_csv(draft, DRAFT_COLUMNS, rows)
    _write_csv(out_dir / "unassigned_highlights.csv", UNASSIGNED_COLUMNS, unassigned)
    log.info(
        "gold_draft_written",
        stage="T7",
        pages=pages,
        rows=len(rows),
        highlights=highlights_total,
        unassigned=len(unassigned),
    )
    return draft


def _propose_page(
    label: str, image: Pixels, png: bytes, out_dir: Path, locator: Locator | None
) -> tuple[list[dict[str, str]], list[dict[str, str]], int]:
    """Draft rows for one page, its unassigned highlights and its highlight count."""
    field_boxes: Mapping[FieldKey, Box] = locator(label, png) if locator is not None else {}
    attached: dict[FieldKey, list[str]] = {}
    unassigned: list[dict[str, str]] = []
    found = find_highlights(image)
    for n, hl in enumerate(found, start=1):
        key = attach_field(hl.box, field_boxes)
        name = f"{label}_{key.value}_hl{n}" if key is not None else f"{label}_hl{n}"
        rel = f"crops/{name}.png"
        _write(out_dir / rel, _crop_png(image, hl.box))
        if key is None:
            unassigned.append({"page_label": label, "crop_path": rel})
        else:
            attached.setdefault(key, []).append(rel)
    rows: list[dict[str, str]] = []
    for key in FieldKey:
        crops = attached.get(key, [])
        if not crops and key in field_boxes:
            rel = f"crops/{label}_{key.value}.png"
            _write(out_dir / rel, _crop_png(image, field_boxes[key]))
            crops_for_row = [rel]
        else:
            crops_for_row = crops
        rows.append(
            {
                "page_label": label,
                "field": key.value,
                "proposed_value": "",
                "crop_path": ";".join(crops_for_row),
                "highlighted": "1" if crops else "0",
                "confirmed_value": "",
                "notes": UNASSIGNED_NOTE if unassigned else "",
            }
        )
    return rows, unassigned, len(found)


def _write_csv(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
    """UTF-8 with a BOM so a spreadsheet opens Hebrew correctly; LF line ends on every machine."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def pipeline_locator(*, models: Path | None = None) -> Locator:
    """A Locator that runs the local pipeline (no cloud, LOCAL_ONLY) and reports where it found
    each field. Only boxes are used - never a value. A page the pipeline rotated, deskewed or
    straightened gets no boxes (its frame differs from the render), and neither does a page that
    fails; both are then handled by hand."""
    services = prepare_services(None, local_only=True, ablation=None, models=models)

    def locate(label: str, png: bytes) -> Mapping[FieldKey, Box]:
        job_id = f"gold-{uuid.uuid4().hex[:12]}"
        boxes: dict[FieldKey, Box] = {}
        try:
            result = intake(
                [UploadedBlob(filename=f"{label}.png", data=png)],
                settings=services.settings,
                documents=services.documents,
                storage=services.storage,
                job_id=job_id,
            )
            raws = apply_labels(result.pages, lambda r: label_of_page(r.source_file, r.page_index))
            for outcome in iter_outcomes(raws, services=services):
                if outcome.status != "done" or GEOMETRIC_CORRECTIONS & set(outcome.corrections):
                    continue
                boxes = {
                    key: ev.result.box
                    for key, ev in outcome.evaluations.items()
                    if ev.result.box is not None
                }
        finally:
            services.storage.purge_job(job_id)
        return boxes

    return locate


# --------------------------------------------------------------------------- rows


@dataclass(frozen=True)
class Row:
    """One (page, field) row of a draft or gold file. `value` is stripped; "" when unconfirmed."""

    page: str
    field: FieldKey
    value: str


def _column(names: list[str], options: tuple[str, ...], what: str, path: Path) -> str:
    for option in options:
        if option in names:
            return option
    raise EvalInputError(f"{path.name} has no {what} column (expected one of {options})")


def read_rows(path: Path) -> list[Row]:
    """Rows of a draft or gold file in file order. Raises EvalInputError for a missing file or
    column, an unknown field or a duplicate (page, field)."""
    if not path.is_file():
        raise EvalInputError(f"file not found: {path.name}")
    rows: list[Row] = []
    seen: set[tuple[str, FieldKey]] = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = list(reader.fieldnames or [])
        page_col = _column(names, ("page_label", "page_id"), "page", path)
        value_col = _column(names, ("confirmed_value", "value"), "value", path)
        field_col = _column(names, ("field",), "field", path)
        for raw in reader:
            label, raw_field = (raw[page_col] or "").strip(), (raw[field_col] or "").strip()
            try:
                key = FieldKey(raw_field)
            except ValueError:
                raise EvalInputError(f"{path.name}: unknown field {raw_field!r}") from None
            if (label, key) in seen:
                raise EvalInputError(f"{path.name}: duplicate row for {label} {raw_field}")
            seen.add((label, key))
            rows.append(Row(label, key, (raw[value_col] or "").strip()))
    return rows


def _listed(rows: Sequence[Row]) -> str:
    shown = ", ".join(f"{r.page} {r.field.value}" for r in rows[:MAX_LISTED])
    return shown + (f", and {len(rows) - MAX_LISTED} more" if len(rows) > MAX_LISTED else "")


# --------------------------------------------------------------------------- finalize


class GoldStats(Strict):
    """What `finalize` wrote: row and page counts and, per field, how many were `<EMPTY>`."""

    rows: int
    pages: int
    empty_by_field: dict[str, int] = Field(default_factory=dict)

    def render(self) -> str:
        """`gold: 336 rows, 48 pages, empty values: commitment_number 3, receipt_date 2`."""
        empties = ", ".join(f"{k} {n}" for k, n in self.empty_by_field.items() if n)
        return f"gold: {self.rows} rows, {self.pages} pages, empty values: {empties or 'none'}"


def finalize(draft: Path, out: Path) -> GoldStats:
    """Write `out` (gold.csv) from a fully confirmed draft. Refuses, writing nothing, when any
    row has an empty `confirmed_value` (GoldIncomplete) or a page lacks one of the 7 fields
    (EvalInputError). `<EMPTY>` - a field genuinely blank on the form - is kept verbatim and
    counted per field so an abnormal count is noticed."""
    rows = read_rows(draft)
    if not rows:
        raise EvalInputError(f"{draft.name} holds no rows")
    blank = [r for r in rows if not r.value]
    if blank:
        raise GoldIncomplete(f"{len(blank)} unconfirmed row(s): {_listed(blank)}")
    by_page: dict[str, set[FieldKey]] = {}
    for r in rows:
        by_page.setdefault(r.page, set()).add(r.field)
    short = sorted((p for p, keys in by_page.items() if len(keys) != len(FieldKey)), key=_natural)
    if short:
        raise EvalInputError(f"page(s) without all {len(FieldKey)} fields: {', '.join(short)}")
    ordered = sorted(rows, key=lambda r: (_natural(r.page), list(FieldKey).index(r.field)))
    _write_csv(
        out,
        GOLD_COLUMNS,
        [
            {
                "page_label": r.page,
                "field": r.field.value,
                "confirmed_value": r.value,
                "page_id": r.page,
                "value": "" if r.value == EMPTY_TOKEN else r.value,
            }
            for r in ordered
        ],
    )
    empties = {
        k.value: sum(1 for r in rows if r.field is k and r.value == EMPTY_TOKEN) for k in FieldKey
    }
    stats = GoldStats(rows=len(rows), pages=len(by_page), empty_by_field=empties)
    log.info("gold_written", stage="T7", rows=stats.rows, pages=stats.pages)
    return stats


# --------------------------------------------------------------------------- held-out


def load_families(path: Path = DEFAULT_FAMILIES) -> dict[str, list[str]]:
    """Family name -> page labels from the committed label-only mapping. Raises EvalInputError
    when the file is missing or malformed."""
    if not path.is_file():
        raise EvalInputError(f"family mapping not found: {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        families = data["families"]
        return {str(k): [str(label) for label in v] for k, v in families.items()}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise EvalInputError(f"{path.name} is not a valid family mapping") from exc


def _rank(label: str, seed: int) -> str:
    """Portable pseudo-random order: a hash of seed and label, identical on every machine."""
    return hashlib.sha256(f"{seed}:{label}".encode()).hexdigest()


def pick_held_out(
    gold: Path,
    out: Path,
    n: int = HELD_OUT_SIZE,
    seed: int = HELD_OUT_SEED,
    *,
    families: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """Choose `n` held-out page labels stratified over the families of PLAN §10.2 (HELD_OUT_QUOTAS
    plus ordinary printed pages for the rest) and write them one per line to `out`, natural order.
    Seeded and hash-ranked, so the same gold labels give the same set on every machine. Scarcest
    families are filled first. Raises EvalInputError when a family has fewer unused pages than its
    quota (never fills it with an unrelated page) or `n` is smaller than the quotas."""
    family_map = dict(load_families() if families is None else families)
    labels = {r.page for r in read_rows(gold)}
    ordinary_quota = n - sum(HELD_OUT_QUOTAS.values())
    if ordinary_quota < 0:
        raise EvalInputError(f"n={n} is smaller than the {sum(HELD_OUT_QUOTAS.values())} quotas")
    tagged = {label for members in family_map.values() for label in members}
    pools = {name: sorted(labels & set(family_map.get(name, ()))) for name in HELD_OUT_QUOTAS}
    pools[ORDINARY] = sorted(labels - tagged)
    quotas = {**HELD_OUT_QUOTAS, ORDINARY: ordinary_quota}
    chosen: list[str] = []
    for name in sorted(quotas, key=lambda f: (len(pools[f]), f)):
        free = sorted((x for x in pools[name] if x not in chosen), key=lambda x: _rank(x, seed))
        if len(free) < quotas[name]:
            raise EvalInputError(
                f"family {name}: needs {quotas[name]} page(s), only {len(free)} available"
            )
        chosen += free[: quotas[name]]
    picked = sorted(chosen, key=_natural)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(picked) + "\n")
    log.info("held_out_written", stage="T7", pages=len(picked))
    return picked


# --------------------------------------------------------------------------- cross-check


def _normalized(value: str) -> str:
    return _SPACES.sub(" ", value).strip()


def cross_check(first: Path, second: Path) -> tuple[int, list[tuple[str, str]]]:
    """(rows compared, disagreeing (page_label, field) pairs) between two labellers' files. Only
    rows confirmed in both are compared; whitespace differences do not count. Values are never
    returned. Raises EvalInputError for an unusable file."""
    mine = {(r.page, r.field): r.value for r in read_rows(first)}
    theirs = {(r.page, r.field): r.value for r in read_rows(second)}
    both = [k for k in mine if k in theirs and mine[k] and theirs[k]]
    differing = [k for k in both if _normalized(mine[k]) != _normalized(theirs[k])]
    ordered = sorted(differing, key=lambda k: (_natural(k[0]), list(FieldKey).index(k[1])))
    return len(both), [(page, key.value) for page, key in ordered]


# --------------------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Prints counts, labels and field names only. Exit 2 on unusable input."""
    p = argparse.ArgumentParser(description="Build and finalize the Safe Line gold set.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--propose", action="store_true", help="write the draft and crops")
    mode.add_argument("--finalize", action="store_true", help="write gold.csv from a draft")
    mode.add_argument("--pick-held-out", action="store_true", help="write held_out.txt")
    mode.add_argument("--cross-check", nargs=2, type=Path, metavar=("MINE", "HERS"))
    p.add_argument("--pdfs", nargs="+", type=Path, default=list(DEFAULT_PDFS))
    p.add_argument("--locate", action="store_true", help="use the local pipeline to place fields")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_RESULTS)
    p.add_argument("--draft", type=Path, default=DEFAULT_RESULTS / "gold_draft.csv")
    p.add_argument("--gold", type=Path, default=REPO_ROOT / "eval" / "gold.csv")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--n", type=int, default=HELD_OUT_SIZE)
    p.add_argument("--seed", type=int, default=HELD_OUT_SEED)
    p.add_argument("--families", type=Path, default=DEFAULT_FAMILIES)
    args = p.parse_args(argv)
    out = sys.stdout.write
    try:
        if args.propose:
            locator = pipeline_locator() if args.locate else None
            draft = propose_labels(args.pdfs, args.out_dir, locator=locator)
            rows = read_rows(draft)
            out(f"draft: {len(rows)} rows, {len({r.page for r in rows})} pages, crops in "
                f"{args.out_dir.name}/crops\n")  # fmt: skip
        elif args.finalize:
            stats = finalize(args.draft, args.out or REPO_ROOT / "eval" / "gold.csv")
            out(stats.render() + "\n")
        elif args.pick_held_out:
            picked = pick_held_out(
                args.gold,
                args.out or DEFAULT_HELD_OUT,
                args.n,
                args.seed,
                families=load_families(args.families),
            )
            out(f"held-out: {' '.join(picked)} ({len(picked)})\n")
        else:
            compared, differing = cross_check(*args.cross_check)
            out(f"cross-check: {compared} rows compared, {len(differing)} disagree\n")
            for page, field in differing:
                out(f"  {page} {field}\n")
    except SafeLineError as exc:
        sys.stderr.write(f"{exc.code}: {exc.detail}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
