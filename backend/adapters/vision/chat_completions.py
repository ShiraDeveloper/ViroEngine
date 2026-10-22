"""The OpenAI-style chat-completions request that OpenRouter and Groq both speak (PLAN §6.2).

These providers do not enforce a response schema across all their models, so the schema is stated
in the system message and the chain validates whatever comes back. The key is sent as a bearer
header, never in the URL.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import time
from typing import Any

from backend.adapters.vision.base import (
    ProviderReply,
    check_model_id,
    image_mime,
    png_b64,
    with_ring,
)
from backend.adapters.vision.config import ProvidersConfig
from backend.adapters.vision.prompt import ImagePart, Part, RenderedPrompt, TextPart
from backend.adapters.vision.ring import Credential, CredentialRing
from backend.adapters.vision.transport import HttpCaller
from backend.core.errors import ProviderBadResponse
from backend.core.models import ReaderName


class ChatCompletionsProvider:
    """Shared body of the two fallbacks; subclasses only name themselves and give a URL."""

    name: ReaderName
    url: str

    def __init__(
        self,
        *,
        keys: Sequence[str],
        model: str | None,
        config: ProvidersConfig,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = check_model_id(model) if model else ""
        self._config = config
        self.ring = CredentialRing(
            keys,
            provider=self.name.value,
            cooldown_seconds=config.credential_cooldown_seconds,
            clock=clock,
        )

    @property
    def model_id(self) -> str:
        return self._model

    def available(self) -> bool:
        return bool(self._model) and bool(self.ring.labels)

    def _body(self, prompt: RenderedPrompt, schema: dict[str, Any]) -> dict[str, Any]:
        # Solved examples first, as real user / assistant turns (page reads only).
        shots: list[dict[str, Any]] = []
        for shot in prompt.shots:
            shots.append({"role": "user", "content": _content(shot.parts)})
            shots.append({"role": "assistant", "content": shot.answer})
        system = (
            f"{prompt.system}\n\nAnswer with ONE JSON object that validates against this JSON "
            f"schema and nothing else:\n{json.dumps(schema, ensure_ascii=False)}"
        )
        return {
            "model": self._model,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_output_tokens,
            "messages": [
                {"role": "system", "content": system},
                *shots,
                {"role": "user", "content": _content(prompt.parts)},
            ],
        }

    def generate(
        self, prompt: RenderedPrompt, schema: dict[str, Any], *, caller: HttpCaller
    ) -> ProviderReply:
        """One chat-completions call. Raises the taxonomy of `HttpCaller.post`, plus
        ProviderBadResponse when the answer holds no message text."""
        body = self._body(prompt, schema)

        def attempt(cred: Credential) -> dict[str, Any]:
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {cred.key}"}
            return caller.post(self.url, headers, body)

        return _reply(with_ring(self.ring, attempt))


def _content(parts: Sequence[Part]) -> list[dict[str, Any]]:
    """Chat-completions content of one user turn: text and data-URL images, in order."""
    content: list[dict[str, Any]] = []
    for part in parts:
        if isinstance(part, TextPart):
            if part.text:
                content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            url = f"data:{image_mime(part.png)};base64,{png_b64(part.png)}"
            content.append({"type": "image_url", "image_url": {"url": url}})
    return content


def _reply(data: dict[str, Any]) -> ProviderReply:
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ProviderBadResponse("answer holds no text") from None
    if not isinstance(text, str) or not text.strip():
        raise ProviderBadResponse("answer holds no text")
    usage = data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    return ProviderReply(
        text=text,
        input_tokens=_count(usage.get("prompt_tokens")),
        output_tokens=_count(usage.get("completion_tokens")),
    )


def _count(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
