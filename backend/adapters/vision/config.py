"""Provider settings read from `config/models.yaml: providers` (project rule §9).

The defaults below equal the file (a test enforces it); the file is authoritative. Model ids live
in configuration because the free model lists change weekly. Environment settings win over the
file for the three model ids, so an operator can switch a model without editing the repository.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field
import yaml  # type: ignore[import-untyped,unused-ignore]

from backend.core.models import Strict

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODELS_PATH = ROOT / "config" / "models.yaml"
DEFAULT_PROMPTS_DIR = ROOT / "config" / "prompts"


class CircuitConfig(Strict):
    failure_threshold: int = Field(default=5, ge=1)  # consecutive failures before opening
    open_duration_seconds: float = Field(default=60.0, ge=0.0)


class RetryConfig(Strict):
    max_attempts: int = Field(default=3, ge=1)
    base_delay_seconds: float = Field(default=0.5, gt=0.0)  # delay n is base * 2**n
    max_delay_seconds: float = Field(default=8.0, gt=0.0)
    jitter_factor: float = Field(default=0.25, ge=0.0, le=1.0)  # +-25%


class BucketConfig(Strict):
    capacity: int = Field(default=10, ge=1)
    refill_seconds: float = Field(default=6.0, gt=0.0)  # one token per 6 s = ~10 req/min


class GeminiConfig(Strict):
    model: str = "gemini-2.5-flash"
    # Gemini 2.5 spends max_output_tokens on hidden reasoning first; 0 keeps the 800-token cap for
    # the JSON answer. null omits the parameter (models that have no thinking mode reject it).
    thinking_budget: int | None = Field(default=0, ge=0)


class OpenRouterConfig(Strict):
    model: str | None = None  # a ":free" vision model id; null leaves the provider unavailable


class GroqConfig(Strict):
    vision_model: str | None = None  # null skips Groq (no vision model listed that day)


class ProvidersConfig(Strict):
    primary: str = "gemini"
    fallback_order: list[str] = Field(default_factory=lambda: ["openrouter", "groq"])
    circuit_breaker: CircuitConfig = Field(default_factory=CircuitConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    token_bucket: BucketConfig = Field(default_factory=BucketConfig)
    credential_cooldown_seconds: float = Field(default=60.0, ge=0.0)
    max_output_tokens: int = Field(default=800, ge=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    request_timeout_seconds: float = Field(default=30.0, gt=0.0)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)
    groq: GroqConfig = Field(default_factory=GroqConfig)

    @property
    def order(self) -> list[str]:
        """Provider names in the order the chain tries them."""
        return [self.primary, *self.fallback_order]


def load_providers_config(path: Path | None = None) -> ProvidersConfig:
    """Parse the `providers:` section of models.yaml; a missing section keeps the defaults.
    Raises FileNotFoundError, yaml.YAMLError or pydantic.ValidationError on a bad file."""
    data: dict[str, Any] = yaml.safe_load((path or DEFAULT_MODELS_PATH).read_text(encoding="utf-8"))
    return ProvidersConfig.model_validate(data.get("providers", {}))
