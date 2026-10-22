"""The provider chain (PLAN §4 S9, §6): cache, then Gemini -> OpenRouter -> Groq, never fatal.

For each provider in the configured order: a circuit breaker decides whether to try it, a token
bucket paces it, transient failures are retried with exponential backoff and jitter, an invalid
answer gets exactly one retry with a schema reminder, and a provider that still fails is skipped.
When all of them fail the chain raises `ProviderExhausted`, which S9 turns into flagged fields; it
never raises anything else for a provider problem, so a run cannot be stopped from the cloud side.

An answer becomes a `VisionResponse` only after strict schema validation; the model has no way to
set a confidence. Credentials are only ever referred to by label (`k1`). `LOCAL_ONLY` is checked
here as well as in the container, so no request can leave the machine under it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import random
import time
from typing import Final, Literal

import structlog

from backend.adapters.vision.base import Provider, ProviderReply
from backend.adapters.vision.cache import CACHE_DIRNAME, ResponseCache, cache_key
from backend.adapters.vision.circuit import CircuitBreaker
from backend.adapters.vision.config import (
    DEFAULT_FEWSHOT_DIR,
    ProvidersConfig,
    load_providers_config,
)
from backend.adapters.vision.errors import ProviderRejected, ProviderTimedOut, ProviderUnavailable
from backend.adapters.vision.fewshot import ExampleSet, load_examples
from backend.adapters.vision.gemini import GeminiProvider
from backend.adapters.vision.groq import GroqProvider
from backend.adapters.vision.openrouter import OpenRouterProvider
from backend.adapters.vision.prompt import prompt_fingerprint, render_prompt
from backend.adapters.vision.rate_limit import TokenBucket
from backend.adapters.vision.transport import HttpCaller, Transport, httpx_transport
from backend.core.errors import (
    ProviderBadResponse,
    ProviderExhausted,
    ProviderInvalidKey,
    ProviderRateLimited,
)
from backend.core.models import ProviderHealth, VisionRequest, VisionResponse
from backend.core.vision_schema import (
    ExtractionPayload,
    PagePayload,
    SenderPayload,
    VerifyPayload,
    crop_values,
    page_answers,
    parse_extraction,
    parse_page,
    parse_sender,
    parse_verify,
    to_answers,
)
from backend.settings import Settings

log = structlog.get_logger()

# Every validated answer shape the chain can produce, one per request task.
Payload = ExtractionPayload | SenderPayload | PagePayload | VerifyPayload

# Structural: milliseconds per second, for the duration the response reports.
_MS: Final = 1000.0


class _ProviderFailed(Exception):
    """One provider gave up. `reason` is a short closed label that is safe to log and to report."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Slot:
    provider: Provider
    breaker: CircuitBreaker
    caller: HttpCaller
    with_model: bool = False  # several slots share this provider: name the model too

    @property
    def name(self) -> str:
        """The provider (`gemini`), or provider and model (`gemini/gemini-2.5-flash`) when several
        models of one provider are chained, so a failover between them reads in the report."""
        base = self.provider.name.value
        return f"{base}/{self.provider.model_id}" if self.with_model else base


def _default_providers(
    settings: Settings,
    config: ProvidersConfig,
    clock: Callable[[], float],
    gemini_models: Sequence[str] | None = None,
) -> list[Provider]:
    """The providers named in `config.order`, with ids from the environment first, then the file.
    `gemini_models` puts several Gemini models in the primary slot, in order: each has its own
    free daily quota, so a spent model hands over to the next before any other provider."""
    explicit = settings.model_fields_set
    gemini_model = settings.gemini_model if "gemini_model" in explicit else config.gemini.model
    models = list(gemini_models or [gemini_model])
    built: dict[str, Provider | list[Provider]] = {
        "gemini": [
            GeminiProvider(keys=settings.gemini_keys_list, model=m, config=config, clock=clock)
            for m in models
        ],
        "openrouter": OpenRouterProvider(
            keys=[settings.openrouter_api_key] if settings.openrouter_api_key else [],
            model=settings.openrouter_vision_model or config.openrouter.model,
            config=config,
            clock=clock,
        ),
        "groq": GroqProvider(
            keys=[settings.groq_api_key] if settings.groq_api_key else [],
            model=settings.groq_vision_model or config.groq.vision_model,
            config=config,
            clock=clock,
        ),
    }
    chosen: list[Provider] = []
    for name in config.order:
        entry = built.get(name)
        if entry is None:
            continue
        chosen.extend(entry if isinstance(entry, list) else [entry])
    return chosen


class VisionChainAdapter:
    """VisionLLMPort over the configured providers. Thread-confined: one worker calls it."""

    name = "chain"

    def __init__(
        self,
        settings: Settings,
        *,
        config: ProvidersConfig | None = None,
        providers: Sequence[Provider] | None = None,
        transport: Transport = httpx_transport,
        cache: ResponseCache | None = None,
        prompts_dir: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
        gemini_models: Sequence[str] | None = None,
        cache_scope: str = "",
    ) -> None:
        self._settings = settings
        self._config = config or load_providers_config()
        self._prompts_dir = prompts_dir
        # Solved example pages taught before every page read; none when the directory is absent.
        # Part of every cache key: the blind verifier must never be handed the page reader's
        # cached answer for the same page (that is one reading counted twice, a false level B).
        self._cache_scope = cache_scope
        self._fewshot_dir = (
            Path(settings.vision_fewshot_dir)
            if settings.vision_fewshot_dir
            else DEFAULT_FEWSHOT_DIR
        )
        self._sleep = sleep
        self._rand = rand
        chosen = (
            list(providers)
            if providers is not None
            else _default_providers(settings, self._config, clock, gemini_models)
        )
        self._slots = [
            _Slot(
                provider=p,
                breaker=CircuitBreaker(
                    failure_threshold=self._config.circuit_breaker.failure_threshold,
                    open_seconds=self._config.circuit_breaker.open_duration_seconds,
                    clock=clock,
                ),
                caller=HttpCaller(
                    transport=transport,
                    bucket=TokenBucket(
                        capacity=self._config.token_bucket.capacity,
                        refill_seconds=self._config.token_bucket.refill_seconds,
                        clock=clock,
                    ),
                    timeout=self._config.request_timeout_seconds,
                    sleep=sleep,
                ),
            )
            for p in chosen
        ]
        names = [slot.provider.name for slot in self._slots]
        for slot in self._slots:
            slot.with_model = names.count(slot.provider.name) > 1
        ttl_minutes = settings.vision_cache_ttl_minutes or settings.job_ttl_minutes
        self._cache = cache or ResponseCache(
            Path(settings.data_dir) / CACHE_DIRNAME, ttl_seconds=ttl_minutes * 60.0
        )

    # ------------------------------------------------------------------ port

    def extract(self, request: VisionRequest) -> VisionResponse:
        """Answer `request` from the cache or from the first provider that gives a valid answer.

        Raises ProviderExhausted when `LOCAL_ONLY` is on, when no provider is configured, or after
        every provider failed. Nothing else is raised for a provider problem."""
        if self._settings.local_only:
            raise ProviderExhausted("LOCAL_ONLY is on: no request is made")
        usable = [s for s in self._slots if s.provider.available()]
        if not usable:
            raise ProviderExhausted("no provider is configured", failovers=("none_configured",))
        examples = self._examples(request)
        prompt = self._cache_scope + prompt_fingerprint(
            request, prompts_dir=self._prompts_dir, examples=examples
        )
        for slot in usable:
            hit = self._cache.get(cache_key(request, slot.provider.model_id, prompt))
            if hit is not None:
                return hit
        started = time.perf_counter()
        failovers: list[str] = []
        for index, slot in enumerate(usable):
            if not slot.breaker.allow():
                failovers.append(f"{slot.name}:circuit_open")
            else:
                try:
                    reply, parsed = self._ask(slot, request, examples)
                except _ProviderFailed as exc:
                    slot.breaker.record_failure()
                    failovers.append(f"{slot.name}:{exc.reason}")
                else:
                    slot.breaker.record_success()
                    response = self._response(slot, request, reply, parsed, failovers, started)
                    self._cache.put(cache_key(request, slot.provider.model_id, prompt), response)
                    return response
            if index + 1 < len(usable):
                failovers.append(f"{slot.name}->{usable[index + 1].name}")
        log.warning("providers_exhausted", stage="S9", job_id=request.job_id, hops=len(failovers))
        raise ProviderExhausted("every provider failed", failovers=tuple(failovers))

    def purge_expired(self) -> int:
        """Delete the cached answers past their lifetime; the number removed. Never raises."""
        return self._cache.purge_expired()

    def health(self) -> ProviderHealth:
        """Credential labels and the circuit state. Never a key."""
        labels = [f"{s.name}:{lab}" for s in self._slots for lab in s.provider.ring.labels]
        states = [s.breaker.state for s in self._slots if s.provider.available()]
        state: Literal["ok", "open", "half_open", "exhausted"]
        if not labels or not states:
            state = "exhausted"
        elif all(s == "open" for s in states):
            state = "open"
        elif "half_open" in states or "open" in states:
            state = "half_open"
        else:
            state = "ok"
        return ProviderHealth(name=self.name, credentials=len(labels), state=state, labels=labels)

    # ------------------------------------------------------------- internals

    def _backoff(self, attempt: int) -> float:
        """Seconds to wait after failed attempt number `attempt` (0-based): base * 2^attempt,
        capped, then +-jitter so concurrent callers do not retry in step."""
        retry = self._config.retry
        base = min(retry.max_delay_seconds, retry.base_delay_seconds * 2.0**attempt)
        return base * (1.0 + retry.jitter_factor * (2.0 * self._rand() - 1.0))

    def _examples(self, request: VisionRequest) -> ExampleSet:
        """The solved examples for a page read (re-read only when examples.json changes)."""
        return load_examples(self._fewshot_dir if request.task == "page" else None)

    def _ask(
        self, slot: _Slot, request: VisionRequest, examples: ExampleSet
    ) -> tuple[ProviderReply, Payload]:
        """One provider, with its retries. Returns the reply and its validated payload.
        Raises _ProviderFailed."""
        transient = 0
        reminder = False
        while True:
            prompt = render_prompt(
                request, prompts_dir=self._prompts_dir, reminder=reminder, examples=examples
            )
            try:
                reply = slot.provider.generate(prompt, request.response_schema, caller=slot.caller)
                return reply, _validate(request, reply.text)
            except ProviderBadResponse:
                if reminder:
                    raise _ProviderFailed("bad_response") from None
                reminder = True  # one retry, with the schema reminder
            except ProviderTimedOut:
                raise _ProviderFailed("timed_out") from None
            except ProviderUnavailable:
                transient += 1
                if transient >= self._config.retry.max_attempts:
                    raise _ProviderFailed("unavailable") from None
                self._sleep(self._backoff(transient - 1))
            except ProviderRateLimited:
                raise _ProviderFailed("rate_limited") from None
            except ProviderInvalidKey:
                raise _ProviderFailed("invalid_key") from None
            except ProviderRejected:
                raise _ProviderFailed("rejected") from None

    def _response(
        self,
        slot: _Slot,
        request: VisionRequest,
        reply: ProviderReply,
        parsed: Payload,
        failovers: list[str],
        started: float,
    ) -> VisionResponse:
        """The response for a validated payload. The raw text of a field extraction is not kept:
        its values are in `answers`, and fewer copies of patient data is better."""
        sender = parsed.sender if isinstance(parsed, SenderPayload) else None
        answers = to_answers(parsed) if isinstance(parsed, ExtractionPayload) else {}
        ambiguities = parsed.ambiguities if isinstance(parsed, ExtractionPayload) else []
        return VisionResponse(
            raw_text=sender or "",
            candidates=[sender] if sender else [],
            provider=slot.provider.name,
            model_id=slot.provider.model_id,
            prompt_version=request.prompt_version,
            duration_ms=int((time.perf_counter() - started) * _MS),
            answers=answers,
            ambiguities=[a[:200] for a in ambiguities[:10]],
            pages=page_answers(parsed, len(request.page_images))
            if isinstance(parsed, PagePayload)
            else [],
            crop_values=crop_values(parsed, len(request.crops))
            if isinstance(parsed, VerifyPayload)
            else [],
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            failovers=list(failovers),
        )


def _validate(request: VisionRequest, text: str) -> Payload:
    """The validated payload of `text` for this request's task. Raises ProviderBadResponse."""
    if request.task == "sender":
        return parse_sender(text)
    if request.task == "page":
        return parse_page(text)
    if request.task == "verify":
        return parse_verify(text)
    return parse_extraction(text)
