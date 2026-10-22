"""Gemini (primary provider, PLAN §6.2): REST `generateContent` with a credential ring.

`temperature=0`, the response schema enforced by the provider (and validated again by the chain),
`max_output_tokens` from configuration. The key travels in the `x-goog-api-key` header, never in
the URL, so it cannot appear in a logged URL or a proxy's access log.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import time
from typing import Any, Final

from backend.adapters.vision.base import (
    ProviderReply,
    check_model_id,
    image_mime,
    png_b64,
    with_ring,
)
from backend.adapters.vision.config import ProvidersConfig
from backend.adapters.vision.errors import ProviderRejected
from backend.adapters.vision.prompt import ImagePart, RenderedPrompt, TextPart
from backend.adapters.vision.ring import Credential, CredentialRing
from backend.adapters.vision.transport import HttpCaller
from backend.core.errors import ProviderBadResponse
from backend.core.models import ReaderName

# Structural: the public Gemini REST endpoint (versioned path; the model id completes it).
BASE_URL: Final = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider:
    """Gemini over REST. One instance per process; it owns the credential ring."""

    name = ReaderName.gemini

    def __init__(
        self,
        *,
        keys: Sequence[str],
        model: str,
        config: ProvidersConfig,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = check_model_id(model) if model else ""
        self._config = config
        # Set once the model refused our thinking / media settings: send provider defaults.
        self._plain = False
        self.ring = CredentialRing(
            keys,
            provider="gemini",
            cooldown_seconds=config.credential_cooldown_seconds,
            clock=clock,
        )

    @property
    def model_id(self) -> str:
        return self._model

    def available(self) -> bool:
        return bool(self._model) and bool(self.ring.labels)

    def _body(self, prompt: RenderedPrompt, schema: dict[str, Any]) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        for part in prompt.parts:
            if isinstance(part, TextPart):
                if part.text:
                    parts.append({"text": part.text})
            elif isinstance(part, ImagePart):
                parts.append(
                    {"inlineData": {"mimeType": image_mime(part.png), "data": png_b64(part.png)}}
                )
        generation: dict[str, Any] = {
            "temperature": self._config.temperature,
            "maxOutputTokens": self._config.max_output_tokens,
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        }
        gemini = self._config.gemini
        if gemini.media_resolution:
            # Handwriting and faint stamps need the page at full detail, not the 258-token default.
            generation["mediaResolution"] = gemini.media_resolution
        if self._model.startswith("gemini-3"):
            # Gemini 3 replaces the token budget with a level; "minimal" keeps a page near 5 s.
            if gemini.thinking_level:
                generation["thinkingConfig"] = {"thinkingLevel": gemini.thinking_level}
        elif gemini.thinking_budget is not None:
            generation["thinkingConfig"] = {"thinkingBudget": gemini.thinking_budget}
        if self._plain:
            generation = self._plain_generation(generation)
        return {
            "systemInstruction": {"parts": [{"text": prompt.system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation,
        }

    def generate(
        self, prompt: RenderedPrompt, schema: dict[str, Any], *, caller: HttpCaller
    ) -> ProviderReply:
        """One `generateContent` call. Raises the taxonomy of `HttpCaller.post`, plus
        ProviderBadResponse when the answer holds no text (e.g. blocked or truncated)."""
        body = self._body(prompt, schema)
        url = f"{BASE_URL}/{self._model}:generateContent"

        def attempt(cred: Credential) -> dict[str, Any]:
            headers = {"Content-Type": "application/json", "x-goog-api-key": cred.key}
            return caller.post(url, headers, body)

        try:
            return _reply(with_ring(self.ring, attempt))
        except ProviderRejected:
            # Newer models refuse some thinking levels ("MINIMAL is not supported") or a media
            # resolution. Retry once with the provider defaults and keep them for this model.
            generation = body["generationConfig"]
            if self._plain or not ({"thinkingConfig", "mediaResolution"} & set(generation)):
                raise
            self._plain = True
            body = {**body, "generationConfig": self._plain_generation(generation)}
            return _reply(with_ring(self.ring, attempt))

    @staticmethod
    def _plain_generation(generation: dict[str, Any]) -> dict[str, Any]:
        return {
            k: v for k, v in generation.items() if k not in ("thinkingConfig", "mediaResolution")
        }


def _reply(data: dict[str, Any]) -> ProviderReply:
    """Text and usage out of a `generateContent` answer, or ProviderBadResponse."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(str(p["text"]) for p in parts if "text" in p)
    except (KeyError, IndexError, TypeError):
        raise ProviderBadResponse("answer holds no text") from None
    if not text.strip():
        raise ProviderBadResponse("answer holds no text")
    usage = data.get("usageMetadata")
    usage = usage if isinstance(usage, dict) else {}
    return ProviderReply(
        text=text,
        input_tokens=_count(usage.get("promptTokenCount")),
        output_tokens=_count(usage.get("candidatesTokenCount")),
    )


def _count(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
