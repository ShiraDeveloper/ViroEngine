"""OpenRouter (first fallback, PLAN §6.2): a `:free` vision model whose id comes from configuration.

The free model list changes weekly, so no id is pinned in code: `OPENROUTER_VISION_MODEL` or
`config/models.yaml: providers.openrouter.model`. With neither, the provider is unavailable and
the chain skips it.
"""

from __future__ import annotations

from typing import Final

from backend.adapters.vision.chat_completions import ChatCompletionsProvider
from backend.core.models import ReaderName

# Structural: the public OpenRouter chat-completions endpoint.
OPENROUTER_URL: Final = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider(ChatCompletionsProvider):
    name = ReaderName.openrouter
    url = OPENROUTER_URL
