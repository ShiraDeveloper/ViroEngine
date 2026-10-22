"""Write `config/sample_hashes.json`: the SHA-256 of every rendered sample page (PLAN §8.5).

A page whose hash is in this list is a known sample form, and only then may crops of it go to a
provider without the job's explicit consent. The file holds hashes only, no image and no text, so
it is safe to commit; the sample PDFs themselves never are (`reference/` is git-ignored).

    python -m tools.make_sample_hashes            # reads reference/samples_part{1,2}.pdf
    python -m tools.make_sample_hashes --check    # exit 1 when the committed file is stale

The hash is of the pages as S0 renders them (`render_dpi`, the PDF renderer's output), so it can
differ between renderer versions: a mismatch only means the page is treated as unknown, which is the
safe direction (processed locally). Regenerate it with the renderer the deployment uses.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from backend.adapters.imaging.pdf import DocumentReader
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.pipeline.s0_intake import UploadedBlob, intake
from backend.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
PDFS = (ROOT / "reference" / "samples_part1.pdf", ROOT / "reference" / "samples_part2.pdf")
OUT = ROOT / "config" / "sample_hashes.json"


def rendered_hashes(pdfs: tuple[Path, ...] = PDFS) -> list[str]:
    """SHA-256 of each rendered page of `pdfs`, in page order. Raises FileNotFoundError."""
    with tempfile.TemporaryDirectory() as scratch:
        settings = Settings(_env_file=None, data_dir=scratch)  # type: ignore[call-arg]
        documents = DocumentReader(settings=settings)
        storage = TmpdirStorage(settings=settings)
        hashes: list[str] = []
        for number, pdf in enumerate(pdfs):
            result = intake(
                [UploadedBlob(filename=pdf.name, data=pdf.read_bytes())],
                settings=settings,
                documents=documents,
                storage=storage,
                job_id=f"hashes-{number}",
            )
            hashes += [page.sha256 for page in result.pages]
        return hashes


def document(hashes: list[str]) -> dict[str, object]:
    return {
        "_note": "SHA-256 of the rendered sample pages (PLAN 8.5). Hashes only; no image, no text.",
        "sha256": sorted(set(hashes)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when the file is stale")
    args = parser.parse_args(argv)
    if not all(p.is_file() for p in PDFS):
        sys.stdout.write("skip: reference/samples_part{1,2}.pdf are absent (git-ignored data)\n")
        return 0
    fresh = document(rendered_hashes())
    if args.check:
        current = json.loads(OUT.read_text(encoding="utf-8")) if OUT.is_file() else {}
        if current.get("sha256") != fresh["sha256"]:
            sys.stdout.write("config/sample_hashes.json is stale\n")
            return 1
        return 0
    OUT.write_text(json.dumps(fresh, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {OUT.name}: {len(fresh['sha256'])} pages\n")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
