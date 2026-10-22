"""Groq (second fallback, PLAN §6.2): used only when its console lists a vision model that day.

The model id comes from `GROQ_VISION_MODEL` or `config/models.yaml: providers.groq.vision_model`;
with neither, the provider is unavailable and the chain skips it.
"""

from __future__ import annotations

from typing import Final

from backend.adapters.vision.chat_completions import ChatCompletionsProvider
from backend.core.models import ReaderName

# Structural: Groq's OpenAI-compatible chat-completions endpoint.
GROQ_URL: Final = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(ChatCompletionsProvider):
    name = ReaderName.groq
    url = GROQ_URL
