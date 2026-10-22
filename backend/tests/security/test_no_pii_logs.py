"""No personal data and no credential in the logs (PLAN §8 T-14, project rule §7).

The redaction is a structlog processor, last before rendering, so these tests go through the real
chain (`configure`) and read what a sink would receive, not what the call site meant to log.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
import pytest
import structlog

from backend.adapters.imaging.cv import CvImaging
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.models import FieldKey, OcrWord, PageRaw
from backend.core.pipeline.orchestrator import process_page
from backend.logging_config import (
    REDACT_KEYS,
    REDACTED,
    add_secret,
    clear_secrets,
    configure,
    redact,
    redact_value,
    scrub_text,
)
from backend.tests.fixtures.api import HEADERS, running_app, seed_job
from backend.tests.fixtures.layout import barcode
from backend.tests.fixtures.synthetic.pages import Pixels, clean_page
from backend.tests.fixtures.synthetic.pipeline_doubles import PageScriptedBarcode, PageScriptedOcr
from backend.tests.fixtures.vision import ScriptedVision, answer
from backend.tests.fixtures.vision_scenarios import filler_words, pipeline_services

# Values a form carries and keys a deployment holds; none may ever reach a log line.
NAME, ID, CENTRAL, DATE, COMMITMENT = "דנה", "011223344", "9373091", "18/06/2026", "X132767"
KEY = "AIzaSyTESTKEY0123456789abcdefghijk"
ACCESS = "TESTCODE_0002_abcdef"
PLANTED = (NAME, ID, CENTRAL, DATE, COMMITMENT)


@pytest.fixture(autouse=True)
def _clean_secrets() -> Iterator[None]:
    clear_secrets()
    yield
    clear_secrets()
    structlog.reset_defaults()


def emitted(capfd: pytest.CaptureFixture[str]) -> str:
    return capfd.readouterr().out


# --- the processor ----------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(REDACT_KEYS))
def test_every_sensitive_key_is_redacted_at_the_top_level(key: str) -> None:
    out = redact(None, "info", {"event": "x", key: "secret-value-123", "page_id": "p1"})

    assert out[key] == REDACTED
    assert out["page_id"] == "p1", "a safe key is untouched"


@pytest.mark.parametrize("key", ["VALUE", "National_ID", "Api_Key", "X-API-Key"])
def test_key_matching_ignores_case(key: str) -> None:
    assert redact(None, "info", {key: "v"})[key] == REDACTED


def test_redaction_cannot_be_bypassed_by_nested_dict(capfd: pytest.CaptureFixture[str]) -> None:
    configure("DEBUG")
    log = structlog.get_logger()

    log.info(
        "nested",
        context={"row": {"fields": [{"value": NAME}, {"national_id": ID}], "n": 3}},
        batch=[{"first_name": NAME}, ({"receipt_date": DATE},)],
        deep={"a": {"b": {"c": {"d": {"e": {"commitment_number": COMMITMENT}}}}}},
    )

    out = emitted(capfd)
    assert not any(v in out for v in PLANTED), out
    assert '"n": 3' in out, "harmless data survives"


def test_an_object_is_logged_as_its_type_not_its_repr(capfd: pytest.CaptureFixture[str]) -> None:
    class Holder(BaseModel):
        label: str

    configure("DEBUG")

    structlog.get_logger().info(
        "obj", holder=Holder(label=NAME), blob=NAME.encode(), err=ValueError(ID)
    )

    out = emitted(capfd)
    assert NAME not in out and ID not in out
    assert "<Holder>" in out and "<bytes>" in out and "<ValueError>" in out


def test_a_value_inside_an_exception_traceback_is_scrubbed(
    capfd: pytest.CaptureFixture[str],
) -> None:
    configure("DEBUG", secrets=[KEY])

    try:
        raise RuntimeError(f"provider said no for key {KEY}")
    except RuntimeError:
        structlog.get_logger().exception("boom")

    out = emitted(capfd)
    assert KEY not in out and "boom" in out


# --- credentials ------------------------------------------------------------------------------


def test_api_keys_never_logged(capfd: pytest.CaptureFixture[str]) -> None:
    configure("DEBUG", secrets=[KEY, ACCESS])
    log = structlog.get_logger()

    log.info(f"calling provider with {KEY}")
    log.info("headers", headers={"x-goog-api-key": KEY, "Authorization": f"Bearer {KEY}"})
    log.warning("note", message=f"access code was {ACCESS}")
    log.info("free text", detail="api_key=abcdef0123456789 and gsk_abcdefghijklmnopqrstuv")

    out = emitted(capfd)
    for secret in (KEY, ACCESS, "abcdef0123456789", "gsk_abcdefghijklmnopqrstuv"):
        assert secret not in out, f"{secret!r} reached the log"
    assert REDACTED in out


@pytest.mark.parametrize(
    "text",
    [
        "AIzaSyTESTKEY1b2C3d4E5f6G7h8I9j0K1l",
        "sk-abcdefghijklmnopqrstuvwxyz012345",
        "gsk_abcdefghijklmnopqrstuvwxyz",
        "Bearer abcdef1234567890",
        "token=abcdef123456",
        "SECRET: hunter2hunter2",
    ],
)
def test_credential_shaped_strings_are_scrubbed(text: str) -> None:
    assert text not in scrub_text(f"before {text} after")
    assert scrub_text("a plain sentence about a form") == "a plain sentence about a form"


def test_a_short_secret_is_not_registered() -> None:
    add_secret("abc")

    assert scrub_text("abc def") == "abc def"


def test_redact_value_bounds_its_recursion() -> None:
    node: dict[str, Any] = {}
    cursor = node
    for _ in range(30):
        cursor["next"] = {}
        cursor = cursor["next"]
    cursor["note"] = ID

    assert ID not in json.dumps(redact_value(node))


# --- the pipeline -----------------------------------------------------------------------------


def test_field_values_never_logged(
    capfd: pytest.CaptureFixture[str],
    tmp_path: Path,
    make_page: Callable[[Pixels], PageRaw],
    storage: TmpdirStorage,
    imaging: CvImaging,
) -> None:
    """A whole page through S1-S9 with values planted in the OCR, the barcode and the model's
    answer; the log of that run is searched for every one of them."""
    from backend.settings import Settings

    configure("DEBUG", secrets=[KEY])
    settings = Settings(_env_file=None, data_dir=str(tmp_path), gemini_api_keys=KEY)  # type: ignore[call-arg]
    raw = make_page(clean_page(width=900, height=1280)).model_copy(update={"known_sample": True})
    vision = ScriptedVision(
        answers={FieldKey.first_name: answer(NAME), FieldKey.commitment_number: answer(COMMITMENT)}
    )
    services = pipeline_services(settings, storage, imaging, vision, raw.page_id)
    services.ocr_printed = PageScriptedOcr(  # type: ignore[assignment]
        words={
            raw.page_id: [
                *filler_words(),
                OcrWord(text=NAME, box=(0.30, 0.50, 0.33, 0.60), confidence=0.9),
                OcrWord(text=CENTRAL, box=(0.088, 0.55, 0.100, 0.75), confidence=0.9),
                OcrWord(text=DATE, box=(0.05, 0.60, 0.07, 0.80), confidence=0.9),
            ]
        }
    )
    services.barcode = PageScriptedBarcode(  # type: ignore[assignment]
        hits={raw.page_id: [barcode(CENTRAL, (0.065, 0.50, 0.085, 0.80))]}
    )

    outcome = process_page(raw, services=services)

    out = emitted(capfd)
    assert outcome.status == "done"
    assert out.count("stage_done") >= 3, "the run did log (the test is not vacuous)"
    for planted in (*PLANTED, KEY):
        assert planted not in out, f"{planted!r} reached the log"


def test_the_api_logs_no_value_when_a_row_is_edited_and_confirmed(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure("DEBUG", secrets=[KEY])
    monkeypatch.setenv("GEMINI_API_KEYS", KEY)
    with running_app(monkeypatch, tmp_path) as h:
        configure("DEBUG", secrets=[KEY])
        entry = seed_job(h)
        edit = h.client.patch(
            f"/v1/jobs/{entry.job_id}/rows/row-p1-1/fields/first_name",
            json={"value": "משה-שם-מיוחד"},
            headers=HEADERS,
        )
        h.client.patch(
            f"/v1/jobs/{entry.job_id}/rows/row-p1-1/fields/national_id",
            json={"value": "123456789"},  # refused: the log must not echo it either
            headers=HEADERS,
        )
        h.client.post(f"/v1/jobs/{entry.job_id}/confirm", headers=HEADERS)
        h.client.get(f"/v1/jobs/{entry.job_id}/export", params={"format": "csv"}, headers=HEADERS)

    out = emitted(capfd)
    assert edit.status_code == 200
    for planted in ("משה-שם-מיוחד", "123456789", "937309", "דנה", "כהן", KEY):
        assert planted not in out, f"{planted!r} reached the log"
