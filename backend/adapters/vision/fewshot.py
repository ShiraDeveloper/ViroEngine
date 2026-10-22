"""Solved example pages shown to the model before the page it reads (few-shot).

A page-read request is sent as a short conversation: for each example, a user turn with the
example page image and the model turn with the exact JSON that page must produce; then the real
page. The model sees, on real forms, WHERE each label sits and which value beside it is the answer
(the name next to "שם" and not the street under it, the commitment and not the print serial).

The examples live in a directory (default `reference/fewshot/`, git-ignored with the rest of the
sample material) holding `examples.json` and the page images it names:

    {"examples": [{"image": "A1.jpg", "page": {<one page object of the page schema>}}]}

Every answer is validated with the same strict parser as a live answer: an example that would be
rejected from the model is never taught to it. A missing directory means no examples (the read
works as before); an invalid example is skipped and logged, never sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Final

import structlog

from backend.core.errors import ProviderBadResponse
from backend.core.vision_schema import parse_page

log = structlog.get_logger()

EXAMPLES_FILE: Final = "examples.json"
# Structural: an example page above this size is a mistake (an unscaled render), not a teacher.
_MAX_IMAGE_BYTES: Final = 4_000_000


@dataclass(frozen=True)
class Example:
    """One solved page: its image and the exact reply the model must give for it."""

    label: str
    image: bytes
    answer: str


@dataclass(frozen=True)
class ExampleSet:
    examples: tuple[Example, ...]
    # Changes whenever an image or an answer changes; part of the cache key.
    fingerprint: str


NO_EXAMPLES: Final = ExampleSet(examples=(), fingerprint="")


def _example(directory: Path, item: object, index: int) -> Example | None:
    """One validated example, or None (logged) when it cannot be taught as given."""
    if not isinstance(item, dict) or not isinstance(item.get("page"), dict):
        log.error("fewshot_example_invalid", index=index, reason="no page object")
        return None
    name = str(item.get("image", ""))
    path = (directory / name).resolve()
    if not name or directory.resolve() not in path.parents or not path.is_file():
        log.error("fewshot_example_invalid", index=index, reason="image missing")
        return None
    image = path.read_bytes()
    if len(image) > _MAX_IMAGE_BYTES:
        log.error("fewshot_example_invalid", index=index, reason="image too large")
        return None
    page = {**item["page"], "page": 1}
    answer = json.dumps({"pages": [page]}, ensure_ascii=False, separators=(",", ":"))
    try:
        parse_page(answer)
    except ProviderBadResponse:
        log.error("fewshot_example_invalid", index=index, reason="answer off schema")
        return None
    return Example(label=path.stem, image=image, answer=answer)


@lru_cache(maxsize=8)
def _load(directory: Path, stamp: float) -> ExampleSet:
    del stamp  # only part of the cache key: an edited examples.json is read again
    try:
        data = json.loads((directory / EXAMPLES_FILE).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        log.error("fewshot_file_unreadable")
        return NO_EXAMPLES
    items = data.get("examples", []) if isinstance(data, dict) else []
    examples = tuple(
        ex for i, item in enumerate(items) if (ex := _example(directory, item, i)) is not None
    )
    digest = hashlib.sha256()
    for ex in examples:
        digest.update(hashlib.sha256(ex.image).digest() + ex.answer.encode("utf-8") + b"\x00")
    log.info("fewshot_loaded", examples=len(examples), skipped=len(items) - len(examples))
    return ExampleSet(examples=examples, fingerprint=digest.hexdigest() if examples else "")


def load_examples(directory: Path | None) -> ExampleSet:
    """The examples in `directory`, or NO_EXAMPLES when there is none. Never raises."""
    if directory is None:
        return NO_EXAMPLES
    path = directory / EXAMPLES_FILE
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return NO_EXAMPLES
    return _load(directory, stamp)


def main() -> int:
    """`python -m backend.adapters.vision.fewshot [dir]`: list what would be taught."""
    import sys

    from backend.adapters.vision.config import DEFAULT_FEWSHOT_DIR

    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FEWSHOT_DIR
    found = load_examples(directory)
    print(f"{directory}: {len(found.examples)} valid example(s)")
    for ex in found.examples:
        print(f"  {ex.label}: {len(ex.image) // 1024} KB image, answer {len(ex.answer)} chars")
    return 0 if found.examples else 1


if __name__ == "__main__":
    raise SystemExit(main())
