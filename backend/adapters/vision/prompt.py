"""Render the versioned prompts of `config/prompts/` (PLAN Appendix A) into provider-neutral parts.

The template text is data under version control; this module only fills placeholders and lays out
the user message (optional thumbnail, then one labelled image per crop). Everything injected comes
from deterministic code, is JSON-encoded (so an OCR string cannot start a new instruction line)
and is length-bounded. Text inside any image is data, which the system prompt says in rule 1.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Final

from backend.adapters.vision.config import DEFAULT_PROMPTS_DIR
from backend.adapters.vision.fewshot import NO_EXAMPLES, ExampleSet
from backend.core.models import VISION_TEXT_MAX, VisionRequest

_VERSION: Final = re.compile(r"^v[0-9]+$")
_SECTION: Final = re.compile(r"^=== (SYSTEM|CONTEXT|USER)\b.*===\s*$", re.MULTILINE)
_PLACEHOLDER: Final = re.compile(r"\{([a-z_0-9]+)\}")

# Structural: the Appendix A line the thumbnail and crops are introduced with. Wording only.
_THUMB_LABEL: Final = "Redacted page layout thumbnail (layout context only):"
# Structural: the line each solved example opens with. Wording only.
_EXAMPLE_LABEL: Final = (
    "SOLVED EXAMPLE - another patient's form, already answered correctly. Study where each label "
    "sits and which value beside it is the answer. Its values belong to that page only."
)

REMINDER: Final = (
    "Your previous answer did not match the schema. Answer again with ONLY the JSON object that "
    "matches the schema exactly: no extra keys (there is no confidence field), no prose, no "
    "markdown."
)


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    png: bytes


Part = TextPart | ImagePart


@dataclass(frozen=True)
class Shot:
    """One solved example: the user turn showing the page, and the model's exact answer."""

    parts: list[Part]
    answer: str


@dataclass(frozen=True)
class RenderedPrompt:
    """`system` is the instruction; `parts` the user message in order (context text first).
    `shots` are earlier turns of the conversation, sent before `parts` (page reads only)."""

    system: str
    parts: list[Part]
    shots: tuple[Shot, ...] = ()

    @property
    def images(self) -> int:
        return sum(1 for p in self.parts if isinstance(p, ImagePart))


def _clean(value: str) -> str:
    """One bounded line: whitespace collapsed, control characters dropped."""
    text = " ".join("".join(ch for ch in value if ch.isprintable() or ch.isspace()).split())
    return text[:VISION_TEXT_MAX]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _fill(template: str, values: dict[str, str]) -> str:
    """Replace known `{name}` placeholders only, so literal braces (a JSON example) survive."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def _sections(text: str) -> dict[str, str]:
    marks = list(_SECTION.finditer(text))
    if not marks or "SYSTEM" not in {m.group(1) for m in marks}:
        raise ValueError("prompt file has no SYSTEM section")
    out: dict[str, str] = {}
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[mark.group(1)] = text[mark.end() : end].strip()
    return out


def prompt_path(request: VisionRequest, prompts_dir: Path) -> Path:
    """File of the prompt for this request. Raises ValueError for a version outside `v<digits>`
    (it becomes part of a path)."""
    if _VERSION.fullmatch(request.prompt_version) is None:
        raise ValueError("prompt version must look like v1")
    stem = {"sender": "sender_", "page": "page_", "verify": "verify_"}.get(request.task, "")
    return prompts_dir / f"{stem}{request.prompt_version}.txt"


def prompt_fingerprint(
    request: VisionRequest, *, prompts_dir: Path | None = None, examples: ExampleSet = NO_EXAMPLES
) -> str:
    """Digest of everything the prompt adds to the request: the template text, and the solved
    examples of a page read. Part of the cache key, so an edited prompt never answers from the
    cache of the old one. Raises like `render_prompt`."""
    digest = hashlib.sha256(prompt_path(request, prompts_dir or DEFAULT_PROMPTS_DIR).read_bytes())
    if request.task == "page":
        digest.update(examples.fingerprint.encode("ascii"))
    return digest.hexdigest()


def render_prompt(
    request: VisionRequest,
    *,
    prompts_dir: Path | None = None,
    reminder: bool = False,
    examples: ExampleSet = NO_EXAMPLES,
) -> RenderedPrompt:
    """The system prompt and ordered user parts for `request`.

    `reminder` appends the schema reminder used for the single retry after an invalid answer.
    `examples` become solved turns before a page read (other tasks ignore them).
    Raises FileNotFoundError when the version has no prompt file and ValueError for a bad
    version string."""
    path = prompt_path(request, prompts_dir or DEFAULT_PROMPTS_DIR)
    sections = _sections(path.read_text(encoding="utf-8-sig"))
    requested = [c.field.value for c in request.crops]
    values = {
        "sender": _clean(request.sender) if request.sender else "null",
        "sender_options": _json([_clean(s) for s in request.sender_options]),
        "closed_list": _json([_clean(s) for s in request.sender_options]),
        "label_synonyms": _json([_clean(s) for s in request.label_synonyms]),
        "barcode_candidates": _json([_clean(s) for s in request.barcode_candidates]),
        "code_candidates": _json(
            {k.value: [_clean(v) for v in vs] for k, vs in request.candidates.items()}
        ),
        "fields_requested": _json(requested),
        "page_count": str(len(request.page_images)),
    }
    parts: list[Part] = [TextPart(_fill(sections.get("CONTEXT", ""), values))]
    if request.page_thumb is not None:
        parts += [TextPart(_THUMB_LABEL), ImagePart(request.page_thumb)]
    for number, image in enumerate(request.page_images, start=1):
        parts += [TextPart(f"page {number}:"), ImagePart(image)]
    for number, crop in enumerate(request.crops, start=1):
        parts += [
            TextPart(f"crop {number}: field={crop.field.value}, region={crop.region_kind.value}"),
            ImagePart(crop.png),
        ]
    closing = [
        line
        for line in sections.get("USER", "").splitlines()
        if line.strip() and not line.startswith("[") and line.strip() != "..."
    ]
    parts.append(TextPart(_fill("\n".join(closing), values)))
    if reminder:
        parts.append(TextPart(REMINDER))
    shots: tuple[Shot, ...] = ()
    if request.task == "page":
        shots = tuple(
            Shot(
                parts=[TextPart(_EXAMPLE_LABEL), TextPart("page 1:"), ImagePart(ex.image)],
                answer=ex.answer,
            )
            for ex in examples.examples
        )
    return RenderedPrompt(system=sections.get("SYSTEM", ""), parts=parts, shots=shots)
