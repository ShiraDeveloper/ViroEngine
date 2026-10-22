"""Provider failover, rotation, breaker, cache and validation (PLAN §13 T18).

The central test is `test_all_providers_down_flags_fields_and_run_completes`: when every provider
fails, the run still finishes and the affected fields are flagged `providers_exhausted`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog

from backend.adapters.vision.chain import VisionChainAdapter
from backend.adapters.vision.transport import HttpResponse
from backend.core.errors import ProviderExhausted
from backend.core.models import FieldKey, ReaderName
from backend.settings import Settings
from backend.tests.fixtures.vision import (
    GEMINI_HOST,
    GROQ_HOST,
    OPENROUTER_HOST,
    FakeClock,
    FakeTransport,
    all_down,
    answer_json,
    chat_body,
    fields_request,
    gemini_body,
    ok,
    status,
    timeout_error,
)

KEY_A, KEY_B = "TESTKEY_ALPHA_0001", "TESTKEY_BRAVO_0002"
OR_KEY, GROQ_KEY = "TESTKEY_ROUTER_0003", "TESTKEY_GROQ_0004"
GOOD = answer_json(first_name="דנה", last_name="כהן")


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "gemini_api_keys": f"{KEY_A},{KEY_B}",
        "openrouter_api_key": OR_KEY,
        "openrouter_vision_model": "vendor/vision-model:free",
        "data_dir": str(tmp_path),
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type]


def make_chain(
    tmp_path: Path,
    transport: FakeTransport,
    clock: FakeClock | None = None,
    *,
    rand: float = 0.5,
    **overrides: object,
) -> tuple[VisionChainAdapter, FakeClock]:
    clock = clock or FakeClock()
    chain = VisionChainAdapter(
        make_settings(tmp_path, **overrides),
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
        rand=lambda: rand,
    )
    return chain, clock


def gemini_reply(text: str = GOOD) -> HttpResponse:
    return ok(body=gemini_body(text))


def chat_reply(text: str = GOOD) -> HttpResponse:
    return ok(body=chat_body(text))


# --- credential ring ------------------------------------------------------------------------


def test_429_rotates_to_next_credential(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [status(429), gemini_reply()]})
    chain, _ = make_chain(tmp_path, transport)

    response = chain.extract(fields_request(FieldKey.first_name, FieldKey.last_name))

    assert response.provider is ReaderName.gemini
    assert transport.keys_used(GEMINI_HOST) == [KEY_A, KEY_B]
    assert response.answers[FieldKey.first_name].values == ["דנה"]


def test_401_marks_credential_dead_once(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [status(401), gemini_reply()]})
    chain, _ = make_chain(tmp_path, transport)

    with structlog.testing.capture_logs() as logs:
        chain.extract(fields_request(FieldKey.first_name))
        chain.extract(fields_request(FieldKey.last_name))

    dead = [e for e in logs if e["event"] == "credential_dead"]
    assert len(dead) == 1
    assert dead[0]["label"] == "k1"
    assert transport.keys_used(GEMINI_HOST) == [KEY_A, KEY_B, KEY_B]


def test_all_credentials_exhausted_moves_to_openrouter(tmp_path: Path) -> None:
    transport = FakeTransport(
        {GEMINI_HOST: [status(429)], OPENROUTER_HOST: [chat_reply()]},
    )
    chain, _ = make_chain(tmp_path, transport)

    response = chain.extract(fields_request(FieldKey.first_name, FieldKey.last_name))

    assert response.provider is ReaderName.openrouter
    assert "gemini:rate_limited" in response.failovers
    assert "gemini->openrouter" in response.failovers
    assert transport.keys_used(OPENROUTER_HOST) == [OR_KEY]


def test_groq_is_skipped_without_a_vision_model(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [status(429)], OPENROUTER_HOST: [status(503)]})
    chain, _ = make_chain(tmp_path, transport, groq_api_key=GROQ_KEY)

    with pytest.raises(ProviderExhausted):
        chain.extract(fields_request(FieldKey.first_name))

    assert transport.calls(GROQ_HOST) == 0


def test_groq_answers_when_it_has_a_vision_model(tmp_path: Path) -> None:
    transport = FakeTransport(
        {GEMINI_HOST: [status(429)], OPENROUTER_HOST: [status(503)], GROQ_HOST: [chat_reply()]}
    )
    chain, _ = make_chain(
        tmp_path, transport, groq_api_key=GROQ_KEY, groq_vision_model="vendor/groq-vision"
    )

    response = chain.extract(fields_request(FieldKey.first_name))

    assert response.provider is ReaderName.groq


# --- central resilience gate ----------------------------------------------------------------


def test_all_providers_down_raises_provider_exhausted_with_hops(tmp_path: Path) -> None:
    chain, _ = make_chain(tmp_path, all_down())

    with pytest.raises(ProviderExhausted) as caught:
        chain.extract(fields_request(FieldKey.first_name, FieldKey.last_name))

    assert caught.value.failovers[0] == "gemini:unavailable"
    assert "gemini->openrouter" in caught.value.failovers


# --- circuit breaker, backoff ---------------------------------------------------------------


def test_circuit_breaker_opens_and_recovers(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [status(503)] * 15 + [gemini_reply()]})
    chain, clock = make_chain(tmp_path, transport, openrouter_api_key="")
    request = fields_request(FieldKey.first_name)

    for _ in range(5):  # five consecutive failures (three attempts each) open the circuit
        with pytest.raises(ProviderExhausted):
            chain.extract(request)
    calls_when_open = transport.calls(GEMINI_HOST)
    with pytest.raises(ProviderExhausted) as refused:
        chain.extract(request)

    assert transport.calls(GEMINI_HOST) == calls_when_open  # refused without a request
    assert "gemini:circuit_open" in refused.value.failovers
    assert chain.health().state == "open"

    clock.advance(61.0)  # the open period ends: one probe goes through and succeeds
    assert chain.extract(request).provider is ReaderName.gemini
    assert chain.health().state == "ok"


@pytest.mark.parametrize(
    ("rand", "expected"),
    [(0.0, [0.375, 0.75]), (0.5, [0.5, 1.0]), (1.0, [0.625, 1.25])],
)
def test_backoff_has_jitter(tmp_path: Path, rand: float, expected: list[float]) -> None:
    transport = FakeTransport({GEMINI_HOST: [status(503)]})
    chain, clock = make_chain(tmp_path, transport, rand=rand, openrouter_api_key="")

    with pytest.raises(ProviderExhausted):
        chain.extract(fields_request(FieldKey.first_name))

    assert transport.calls(GEMINI_HOST) == 3  # three attempts; a 503 is not a key problem
    assert clock.slept[:2] == pytest.approx(expected)


def test_backoff_delay_is_capped(tmp_path: Path) -> None:
    chain, _ = make_chain(tmp_path, FakeTransport())

    assert chain._backoff(10) == pytest.approx(8.0)


def test_timeout_is_retried_like_a_server_error(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [timeout_error(), gemini_reply()]})
    chain, clock = make_chain(tmp_path, transport)

    response = chain.extract(fields_request(FieldKey.first_name))

    assert response.provider is ReaderName.gemini
    assert len(clock.slept) == 1


# --- cache and batching ---------------------------------------------------------------------


def test_cache_hit_makes_no_call(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [gemini_reply()]})
    chain, _ = make_chain(tmp_path, transport)
    request = fields_request(FieldKey.first_name, FieldKey.last_name)

    first = chain.extract(request)
    second = chain.extract(request)

    assert transport.total == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.answers == first.answers


def test_cache_misses_when_prompt_version_changes(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [gemini_reply()]})
    chain, _ = make_chain(tmp_path, transport)

    chain.extract(fields_request(FieldKey.first_name))
    with pytest.raises(FileNotFoundError):
        chain.extract(fields_request(FieldKey.first_name, prompt_version="v9"))

    assert transport.total == 1


def test_one_call_per_page_for_three_unresolved_fields(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [gemini_reply(answer_json(first_name="דנה"))]})
    chain, _ = make_chain(tmp_path, transport)

    chain.extract(fields_request(FieldKey.first_name, FieldKey.last_name, FieldKey.national_id))

    assert transport.total == 1
    parts = transport.sent[0][2]["contents"][0]["parts"]
    assert sum(1 for p in parts if "inlineData" in p) == 3


# --- validation -----------------------------------------------------------------------------


def test_invalid_schema_response_rejected_then_field_flagged(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [gemini_reply('{"fields": {}}')]})
    chain, _ = make_chain(tmp_path, transport, openrouter_api_key="")

    with pytest.raises(ProviderExhausted) as caught:
        chain.extract(fields_request(FieldKey.first_name))

    assert transport.total == 2  # the answer, then exactly one retry with the reminder
    assert "gemini:bad_response" in caught.value.failovers
    retry_parts = transport.sent[1][2]["contents"][0]["parts"]
    assert any("did not match the schema" in p.get("text", "") for p in retry_parts)


def test_invalid_answer_then_valid_retry_is_accepted(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [gemini_reply("not json"), gemini_reply()]})
    chain, _ = make_chain(tmp_path, transport)

    response = chain.extract(fields_request(FieldKey.first_name))

    assert response.answers[FieldKey.first_name].values == ["דנה"]
    assert transport.total == 2


@pytest.mark.parametrize(
    "text",
    [
        '{"fields": {"first_name": {"value": "דנה", "source": "printed", "alts": [], '
        '"confidence": 0.99}}, "ambiguities": []}',
        '{"fields": {}, "ambiguities": [], "confidence": 0.99}',
    ],
)
def test_model_cannot_set_its_own_confidence(tmp_path: Path, text: str) -> None:
    transport = FakeTransport({GEMINI_HOST: [gemini_reply(text)]})
    chain, _ = make_chain(tmp_path, transport, openrouter_api_key="")

    with pytest.raises(ProviderExhausted):
        chain.extract(fields_request(FieldKey.first_name))


def test_fenced_json_answer_is_accepted(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [gemini_reply(f"```json\n{GOOD}\n```")]})
    chain, _ = make_chain(tmp_path, transport)

    assert chain.extract(fields_request(FieldKey.first_name)).answers


def test_sender_task_returns_the_closed_list_answer(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [gemini_reply('{"sender": "בית חולים הדסה"}')]})
    chain, _ = make_chain(tmp_path, transport)
    request = fields_request(FieldKey.sender, task="sender").model_copy(
        update={"sender_options": ["בית חולים הדסה", "לא מוכר"]}
    )

    response = chain.extract(request)

    assert response.candidates == ["בית חולים הדסה"]


# --- keys and health ------------------------------------------------------------------------


def test_keys_never_appear_in_logs(tmp_path: Path) -> None:
    transport = FakeTransport({GEMINI_HOST: [status(401), status(429)], OPENROUTER_HOST: [status(503)]})
    chain, _ = make_chain(tmp_path, transport)

    with structlog.testing.capture_logs() as logs, pytest.raises(ProviderExhausted):
        chain.extract(fields_request(FieldKey.first_name))

    everything = repr(logs) + repr(chain.health())
    for secret in (KEY_A, KEY_B, OR_KEY):
        assert secret not in everything
    assert any(e["event"] == "credential_dead" for e in logs)


def test_health_lists_labels_not_keys(tmp_path: Path) -> None:
    chain, _ = make_chain(tmp_path, FakeTransport())

    health = chain.health()

    assert health.credentials == 3
    assert health.labels == ["gemini:k1", "gemini:k2", "openrouter:k1"]
    assert KEY_A not in health.model_dump_json()


def test_health_is_exhausted_without_any_credential(tmp_path: Path) -> None:
    chain, _ = make_chain(tmp_path, FakeTransport(), gemini_api_keys="", openrouter_api_key="")

    assert chain.health().state == "exhausted"
    with pytest.raises(ProviderExhausted):
        chain.extract(fields_request(FieldKey.first_name))
