"""Few-shot page reads: solved examples are validated, sent as earlier turns, and keyed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.adapters.vision.cache import cache_key
from backend.adapters.vision.config import ProvidersConfig
from backend.adapters.vision.fewshot import EXAMPLES_FILE, NO_EXAMPLES, load_examples
from backend.adapters.vision.gemini import GeminiProvider
from backend.adapters.vision.openrouter import OpenRouterProvider
from backend.adapters.vision.prompt import prompt_fingerprint, render_prompt
from backend.core.models import VisionRequest
from backend.core.vision_schema import page_schema
from backend.tests.fixtures.vision import PNG, fields_request

_ITEM = {"value": "", "label": "", "source": "unknown", "box": [0, 0, 0, 0]}


def _page(**fields: Any) -> dict[str, Any]:
    return {
        "page_role": "primary_form",
        "sender": "מאוחדת",
        "sender_box": [0, 0, 0, 0],
        "patient_address": "",
        "first_name": _ITEM,
        "last_name": _ITEM,
        "national_id": _ITEM,
        "receipt_date": _ITEM,
        "central_number": [],
        "commitment_number": [],
        "ambiguities": [],
        **fields,
    }


def _write(directory: Path, examples: list[dict[str, Any]]) -> Path:
    (directory / "a.jpg").write_bytes(PNG)
    (directory / EXAMPLES_FILE).write_text(
        json.dumps({"examples": examples}, ensure_ascii=False), encoding="utf-8"
    )
    return directory


def _page_request() -> VisionRequest:
    return VisionRequest(
        job_id="job-1",
        page_id="page#1",
        task="page",
        page_images=[PNG + b"real"],
        crops=[],
        sender_options=["מאוחדת"],
        prompt_version="v1",
        response_schema=page_schema(["מאוחדת"]),
    )


def test_a_missing_directory_means_no_examples(tmp_path: Path) -> None:
    assert load_examples(tmp_path / "absent") is NO_EXAMPLES
    assert load_examples(None) is NO_EXAMPLES


def test_only_valid_examples_are_taught(tmp_path: Path) -> None:
    name = {"value": "משה", "label": "שם פרטי", "source": "handwritten", "box": [1, 1, 2, 2]}
    _write(
        tmp_path,
        [
            {"image": "a.jpg", "page": _page(first_name=name)},
            {"image": "a.jpg", "page": _page(confidence=0.9)},  # off schema
            {"image": "missing.jpg", "page": _page()},
            {"image": "../a.jpg", "page": _page()},  # outside the directory
        ],
    )

    found = load_examples(tmp_path)

    assert len(found.examples) == 1
    answer = json.loads(found.examples[0].answer)
    assert answer["pages"][0]["page"] == 1
    assert answer["pages"][0]["first_name"]["value"] == "משה"
    assert found.fingerprint


def test_gemini_sends_examples_as_turns_before_the_real_page(tmp_path: Path) -> None:
    examples = load_examples(_write(tmp_path, [{"image": "a.jpg", "page": _page()}]))
    request = _page_request()
    provider = GeminiProvider(keys=["k"], model="gemini-2.5-flash", config=ProvidersConfig())

    body = provider._body(render_prompt(request, examples=examples), request.response_schema)

    roles = [turn["role"] for turn in body["contents"]]
    assert roles == ["user", "model", "user"]
    assert body["contents"][1]["parts"][0]["text"] == examples.examples[0].answer
    assert any("inlineData" in p for p in body["contents"][0]["parts"])
    assert any("inlineData" in p for p in body["contents"][2]["parts"])


def test_chat_providers_send_examples_as_assistant_turns(tmp_path: Path) -> None:
    examples = load_examples(_write(tmp_path, [{"image": "a.jpg", "page": _page()}]))
    request = _page_request()
    provider = OpenRouterProvider(keys=["k"], model="m/vision:free", config=ProvidersConfig())

    body = provider._body(render_prompt(request, examples=examples), request.response_schema)

    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_examples_are_for_page_reads_only(tmp_path: Path) -> None:
    examples = load_examples(_write(tmp_path, [{"image": "a.jpg", "page": _page()}]))

    assert render_prompt(fields_request(), examples=examples).shots == ()
    assert len(render_prompt(_page_request(), examples=examples).shots) == 1


def test_changing_the_examples_misses_the_cache(tmp_path: Path) -> None:
    request = _page_request()
    examples = load_examples(_write(tmp_path, [{"image": "a.jpg", "page": _page()}]))

    without = cache_key(request, "m", prompt_fingerprint(request))
    with_examples = cache_key(request, "m", prompt_fingerprint(request, examples=examples))

    assert without != with_examples


def test_the_verifier_never_reuses_the_readers_cached_answer() -> None:
    request = _page_request()
    fingerprint = prompt_fingerprint(request)

    reader = cache_key(request, "gemini-3-flash-preview", fingerprint)
    verifier = cache_key(request, "gemini-3-flash-preview", "verify:" + fingerprint)

    assert reader != verifier
