"""Five degraded variants per page for evaluation and for Stress Mode (PLAN §10.3, T25).

    python -m eval.augment --pages reference/samples_part1.pdf --out eval/results/aug --seed 20261018

EVALUATION ONLY. `nohl` reuses the yellow mask from `eval/make_gold.py`, so this module lives in
`eval/` and `backend/core` must never import it (project rule §4; `scripts/check_layering.py`).
The runtime reaches it only through the `(png_bytes, op, seed) -> png_bytes` callable that
`api/stress.py` expects (`augment_png`). Every variant is seeded, so two machines and CI produce
byte-identical images.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys
from typing import Any, Final

import cv2
import numpy as np
import structlog

from backend.adapters.imaging.codec import Pixels, decode_bgr, encode_png
from backend.adapters.imaging.pdf import DocumentReader
from backend.core.errors import RenderFailed
from backend.core.models import Strict
from backend.settings import Settings
from eval.make_gold import yellow_mask
from eval.run_eval import DEFAULT_RESULTS, EvalInputError, label_of_page

log = structlog.get_logger()

OPS: Final = ("nohl", "rot", "blur", "jpeg", "fax")
DEFAULT_SEED: Final = 20261018

# PLAN §10.3 parameters of each variant.
ROT_MAX_SKEW_DEG: Final = 3.0
BLUR_SIGMA: Final = 1.2
BLUR_RESCALE: Final = 0.9
JPEG_QUALITY: Final = 35
FAX_SCALE: Final = 0.8
FAX_NOISE_SHARE: Final = 0.02
# Structural: the 4x4 Bayer matrix that makes the 1-bit dither deterministic and seed-independent.
BAYER_4: Final = np.array(
    [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32
)
# Structural: pixels within this many pixels of the mask are anti-aliased black-on-yellow edges.
HL_RING_PX: Final = 1
WHITE: Final = 255


class AugmentStats(Strict):
    """What `augment_set` wrote: image counts only, never pixels or file contents."""

    sources: int
    written: int
    per_op: dict[str, int]
    seed: int


def _remove_highlight(img: Pixels) -> Pixels:
    """Yellow pixels become white; the dark ink under the highlight is kept. A one-pixel ring of
    anti-aliased edge is desaturated to grey so no yellow fringe remains."""
    mask = yellow_mask(img) > 0
    kernel = np.ones((2 * HL_RING_PX + 1, 2 * HL_RING_PX + 1), np.uint8)
    grown: Any = cv2.dilate(mask.astype(np.uint8), kernel)
    ring = (grown > 0) & ~mask
    out = img.copy()
    grey: Any = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    out[ring] = np.repeat(grey[ring][:, None], 3, axis=1)
    out[mask] = WHITE
    return np.asarray(out, dtype=np.uint8)


def _rotate(img: Pixels, rng: np.random.Generator) -> Pixels:
    """A random multiple of 90 degrees, then a skew of at most ROT_MAX_SKEW_DEG either way."""
    turned = np.ascontiguousarray(np.rot90(img, k=int(rng.integers(0, 4))))
    angle = float(rng.uniform(-ROT_MAX_SKEW_DEG, ROT_MAX_SKEW_DEG))
    height, width = turned.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    skewed: Any = cv2.warpAffine(
        turned, matrix, (width, height), borderMode=cv2.BORDER_CONSTANT, borderValue=(WHITE,) * 3
    )
    return np.asarray(skewed, dtype=np.uint8)


def _blur(img: Pixels) -> Pixels:
    height, width = img.shape[:2]
    soft: Any = cv2.GaussianBlur(img, (0, 0), BLUR_SIGMA)
    small: Any = cv2.resize(
        soft,
        (max(1, round(width * BLUR_RESCALE)), max(1, round(height * BLUR_RESCALE))),
        interpolation=cv2.INTER_AREA,
    )
    back: Any = cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)
    return np.asarray(back, dtype=np.uint8)


def _jpeg(img: Pixels) -> Pixels:
    ok, encoded = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise EvalInputError("jpeg re-encoding failed")
    return decode_bgr(encoded.tobytes())


def _fax(img: Pixels, rng: np.random.Generator) -> Pixels:
    """Scale to FAX_SCALE, ordered-dither to 1 bit, flip FAX_NOISE_SHARE of the pixels."""
    grey: Any = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    height, width = grey.shape[:2]
    small: Any = cv2.resize(
        grey,
        (max(1, round(width * FAX_SCALE)), max(1, round(height * FAX_SCALE))),
        interpolation=cv2.INTER_AREA,
    )
    h, w = small.shape[:2]
    tiled = np.tile(BAYER_4, (h // 4 + 1, w // 4 + 1))[:h, :w]
    threshold = (tiled + 0.5) / 16.0 * WHITE
    bits = np.where(small.astype(np.float32) > threshold, WHITE, 0).astype(np.uint8)
    noisy = rng.random((h, w)) < FAX_NOISE_SHARE
    salt = rng.random((h, w)) < 0.5
    bits[noisy] = np.where(salt[noisy], WHITE, 0).astype(np.uint8)
    return np.asarray(cv2.cvtColor(bits, cv2.COLOR_GRAY2BGR), dtype=np.uint8)


def augment_page(img: Pixels, op: str, *, rng: np.random.Generator) -> Pixels:
    """One degraded copy of a BGR page. Raises EvalInputError for an unknown `op` or a non-BGR
    array; the input is never modified."""
    if img.ndim != 3 or img.shape[2] != 3:
        raise EvalInputError("augment_page needs a BGR image")
    if op == "nohl":
        return _remove_highlight(img)
    if op == "rot":
        return _rotate(img, rng)
    if op == "blur":
        return _blur(img)
    if op == "jpeg":
        return _jpeg(img)
    if op == "fax":
        return _fax(img, rng)
    raise EvalInputError(f"unknown augmentation {op!r}; choose from {list(OPS)}")


def augment_png(data: bytes, op: str, seed: int) -> bytes:
    """The Stress Mode augmenter (`api/stress.py`): PNG bytes in, degraded PNG bytes out.
    Raises RenderFailed for bytes that are not an image, EvalInputError for an unknown op."""
    rng = np.random.default_rng([seed, OPS.index(op) if op in OPS else len(OPS)])
    return encode_png(augment_page(decode_bgr(data), op, rng=rng)).data


def augment_set(pages: Sequence[Path], out_dir: Path, *, seed: int = DEFAULT_SEED) -> AugmentStats:
    """Write every op of every page image under `out_dir` as `<stem>__<op>.png`. A page that
    cannot be decoded is skipped and logged, never aborting the set."""
    out_dir.mkdir(parents=True, exist_ok=True)
    per_op = dict.fromkeys(OPS, 0)
    for index, path in enumerate(pages):
        try:
            img = decode_bgr(path.read_bytes())
        except (OSError, RenderFailed) as exc:
            log.warning(
                "augment_page_skipped", stage="T25", page=path.stem, error=type(exc).__name__
            )
            continue
        for op_index, op in enumerate(OPS):
            rng = np.random.default_rng([seed, index, op_index])
            (out_dir / f"{path.stem}__{op}.png").write_bytes(
                encode_png(augment_page(img, op, rng=rng)).data
            )
            per_op[op] += 1
    return AugmentStats(sources=len(pages), written=sum(per_op.values()), per_op=per_op, seed=seed)


def render_pdfs(pdfs: Sequence[Path], out_dir: Path) -> list[Path]:
    """Render every page of each sample PDF to `<out_dir>/<label>.png` and return the paths."""
    settings = Settings()
    reader = DocumentReader(settings=settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for pdf in pdfs:
        if not pdf.is_file():
            raise EvalInputError(f"sample PDF not found: {pdf.name}")
        data = pdf.read_bytes()
        for index in range(reader.inspect_pdf(data)):
            label = label_of_page(pdf.name, index)
            if label is None:
                raise EvalInputError(f"{pdf.name} gives no page label (expected samples_partN)")
            target = out_dir / f"{label}.png"
            target.write_bytes(reader.render_pdf_page(data, index, dpi=settings.render_dpi).data)
            paths.append(target)
    return paths


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Prints counts only. Exit 0 when the sample PDFs are absent (CI), 2 on
    unusable input."""
    p = argparse.ArgumentParser(description="Generate the five degraded variants per page.")
    p.add_argument("--pages", nargs="+", type=Path, required=True, help="sample PDFs or PNGs")
    p.add_argument("--out", type=Path, default=DEFAULT_RESULTS / "aug")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = p.parse_args(argv)
    if not any(f.is_file() for f in args.pages):
        sys.stdout.write("skip: the sample inputs are absent (git-ignored data)\n")
        return 0
    try:
        pdfs = [f for f in args.pages if f.suffix.lower() == ".pdf"]
        images = [f for f in args.pages if f.suffix.lower() != ".pdf"]
        sources = render_pdfs(pdfs, args.out / "clean") + images
        stats = augment_set(sources, args.out, seed=args.seed)
    except EvalInputError as exc:
        sys.stderr.write(f"{exc.code}: {exc.detail}\n")
        return 2
    sys.stdout.write(
        f"augmented {stats.sources} pages -> {stats.written} images | {stats.per_op}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
