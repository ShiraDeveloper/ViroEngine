"""Application settings — loaded from environment variables and .env file.

All thresholds live here (or in config/models.yaml) so eval/calibrate.py
can sweep them without touching business code (§9 configuration rule).

Validated at import time: a malformed threshold or missing required key
fails startup loudly rather than silently using a wrong default.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Access ------------------------------------------------------------
    app_access_code: str = ""
    api_key: str = ""
    allowed_origins: str = "http://localhost:5173"

    # --- Gemini (primary crop reader) -------------------------------------
    gemini_api_keys: str = ""  # comma-separated; rotated on 429 / invalid key
    gemini_model: str = "gemini-2.5-flash"
    gemini_model_lite: str = "gemini-2.5-flash-lite"

    # --- Fallback providers -----------------------------------------------
    openrouter_api_key: str = ""
    openrouter_vision_model: str = ""
    groq_api_key: str = ""

    # --- Engine thresholds (all from config, never hard-coded) ------------
    local_only: bool = False
    max_upload_mb: int = Field(default=50, ge=0, le=500)
    max_pages: int = Field(default=200, ge=1, le=1000)
    job_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    render_dpi: int = Field(default=300, ge=100, le=600)
    max_concurrency: int = Field(default=2, ge=1, le=16)
    escalation_score_threshold: float = Field(default=0.80, ge=0.0, le=1.0)

    # --- Paths ------------------------------------------------------------
    tesseract_cmd: str = "/usr/bin/tesseract"
    models_dir: str = "./models"
    data_dir: str = "./.tmp"

    # --- Observability ----------------------------------------------------
    log_level: str = "INFO"

    # --- Properties (derived, not env vars) --------------------------------

    @property
    def gemini_keys_list(self) -> list[str]:
        """Split comma-separated Gemini API keys. Empty string → empty list."""
        return [k.strip() for k in self.gemini_api_keys.split(",") if k.strip()]

    @property
    def origins_list(self) -> list[str]:
        """Split comma-separated CORS origins."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got {v!r}")
        return upper
