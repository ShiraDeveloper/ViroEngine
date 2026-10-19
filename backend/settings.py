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

    # --- S0 intake safety (T9) --------------------------------------------
    # Largest allowed uncompressed/compressed size ratio of a ZIP entry; real archives of
    # scans are near 1:1, a bomb exceeds this immediately.
    zip_max_ratio: float = Field(default=100.0, ge=1.0, le=10_000.0)
    # Pixel budget of one rendered or decoded page (megapixels). A4 at 300 DPI is ~8.7 MP,
    # A3 ~17.4 MP; the cap bounds memory on a 512 MB host against hostile page sizes.
    max_render_megapixels: float = Field(default=30.0, gt=0.0, le=200.0)

    # --- S1 measurement thresholds (T9) -----------------------------------
    # A correction is flagged only when its measurement crosses its threshold.
    skew_threshold_deg: float = Field(default=0.5, ge=0.0, le=45.0)  # deskew above
    skew_max_deg: float = Field(default=15.0, gt=0.0, le=45.0)  # beyond: unreliable, report 0
    blur_threshold: float = Field(default=100.0, ge=0.0)  # Laplacian variance; sharpen below
    illumination_cv_threshold: float = Field(default=0.18, ge=0.0)  # clahe above
    # Share of ink in specks of <=4 px; denoise above. Measured: real sample pages <=0.03,
    # synthetic fax grain 0.08. (The plan's 0.12 belonged to a different noise definition.)
    noise_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    perspective_threshold: float = Field(default=0.02, ge=0.0, le=1.0)  # of page diagonal
    # Orientation: signals voting for one angle needed before a page is rotated.
    rotation_min_votes: int = Field(default=2, ge=1, le=3)
    ocr_probe_max_side: int = Field(default=1200, ge=300, le=4000)  # px, low-res orientation pass
    ocr_probe_min_words: int = Field(default=5, ge=1)  # Hebrew words needed to cast a vote
    ocr_probe_min_ratio: float = Field(default=1.5, ge=1.0)  # winner vs runner-up word count
    ocr_min_word_confidence: float = Field(default=0.30, ge=0.0, le=1.0)  # 0..1, Tesseract/100

    # --- S2 correction (T9) -----------------------------------------------
    sharpen_scale: float = Field(default=1.5, ge=1.0, le=3.0)  # cubic upscale before unsharp
    sauvola_window: int = Field(default=25, ge=3, le=201)  # px, odd
    sauvola_k: float = Field(default=0.2, ge=0.0, le=1.0)
    thumb_max_side: int = Field(default=512, ge=64, le=2048)  # px, longest side of `thumb`

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

    @field_validator("sauvola_window")
    @classmethod
    def _validate_sauvola_window(cls, v: int) -> int:
        if v % 2 == 0:
            raise ValueError(f"sauvola_window must be odd, got {v}")
        return v

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got {v!r}")
        return upper
