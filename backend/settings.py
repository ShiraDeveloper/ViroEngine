"""Application settings — loaded from environment variables and .env file.

All thresholds live here (or in config/models.yaml) so eval/calibrate.py
can sweep them without touching business code (§9 configuration rule).

Validated at import time: a malformed threshold or missing required key
fails startup loudly rather than silently using a wrong default.
"""

from __future__ import annotations

from typing import Literal

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
    # PLAN §8 T-7: all requests per client per minute, and uploads on top of that.
    rate_limit_per_minute: int = Field(default=60, ge=1, le=100_000)
    upload_rate_limit_per_minute: int = Field(default=10, ge=1, le=10_000)

    # --- Gemini (primary crop reader) -------------------------------------
    gemini_api_keys: str = ""  # comma-separated; rotated on 429 / invalid key
    gemini_model: str = "gemini-2.5-flash"
    gemini_model_lite: str = "gemini-2.5-flash-lite"

    # --- Fallback providers -----------------------------------------------
    openrouter_api_key: str = ""
    # A ":free" vision model id. Empty -> config/models.yaml `providers.openrouter.model`.
    openrouter_vision_model: str = ""
    groq_api_key: str = ""
    # Only if the Groq console lists a vision model that day. Empty -> config/models.yaml
    # `providers.groq.vision_model`; empty there too means Groq is skipped.
    groq_vision_model: str = ""

    # --- Engine thresholds (all from config, never hard-coded) ------------
    local_only: bool = False
    max_upload_mb: int = Field(default=50, ge=0, le=500)
    max_pages: int = Field(default=200, ge=1, le=1000)
    job_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    # S11 (PLAN §4): after the artefacts are purged the job answers `purged` for this long so the
    # UI can show a message instead of a 404; the sweeper looks for expired jobs this often.
    # SSE (PLAN §3.4): a `: ping` comment this often keeps proxies from closing an idle stream; a
    # stream never outlives the ceiling, whatever the job does.
    sse_ping_seconds: int = Field(default=15, ge=1, le=120)
    sse_max_seconds: int = Field(default=600, ge=1, le=3600)
    # Learning layer (PLAN §7.3): a location or shape is used once confirmed this many times. One
    # correction is an anecdote, three is a pattern.
    learning_promotion_hits: int = Field(default=3, ge=1, le=100)
    # Stress Mode (PLAN §10.3): the seed that makes a degradation reproducible across machines.
    stress_seed: int = 20261018
    purged_visible_minutes: int = Field(default=10, ge=0, le=1440)
    purge_sweep_minutes: int = Field(default=5, ge=1, le=60)
    render_dpi: int = Field(default=300, ge=100, le=600)
    max_concurrency: int = Field(default=2, ge=1, le=16)
    escalation_score_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    # S9 (T18): false removes the redacted 512 px context thumbnail from every request, so a
    # provider receives crops alone. Accuracy with and without it is an ablation row (PLAN §10.5).
    escalation_send_thumbnail: bool = True
    # S9 response cache lifetime. A cached response holds the values read from a crop, so it is
    # patient data: None keeps it for JOB_TTL_MINUTES like every other artefact; raise it
    # deliberately for development runs on the adapted sample forms only.
    vision_cache_ttl_minutes: int | None = Field(default=None, ge=1, le=10_080)
    # Orchestrator (T15): pages between forced garbage collections. One page is held in memory
    # at a time; collecting every 10 keeps the 512 MB host budget (PLAN §13 T15 step 3).
    gc_interval_pages: int = Field(default=10, ge=1, le=1000)

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

    # --- S3 page OCR and barcode sweep (T11) ------------------------------
    ocr_langs: str = "heb+eng"  # Hebrew letterheads carry English test names on the same page
    tesseract_oem: int = Field(default=1, ge=0, le=3)  # 1 = LSTM engine
    # 3 = fully automatic, mixed blocks. psm 6 (one block) loses the stickers and the sideways
    # "מרכזי" caption.
    tesseract_psm: int = Field(default=3, ge=0, le=13)
    ocr_timeout_seconds: int = Field(default=120, ge=1, le=600)  # bounds a hostile page
    # Tesseract's orientation confidence below which OSD abstains from the S1 vote.
    osd_min_confidence: float = Field(default=2.0, ge=0.0)
    # Width of the y window that puts words on one line, in median word heights.
    line_height_tolerance: float = Field(default=1.2, gt=0.0, le=5.0)
    # Neighbourhood of a sticker, a caption or a label, as a share of the page diagonal. The
    # sideways caption sits this close to its barcode; the next unrelated label does not.
    proximity_radius: float = Field(default=0.06, gt=0.0, le=1.0)
    bars_retry_scale: float = Field(default=2.0, ge=1.0, le=4.0)  # retry of an undecoded barcode
    # The number printed under a barcode is read again on its own band (T13): it is the
    # independent printed reading that confirms a barcode (level A). Band extent in bar
    # heights past the bars, and overlap/end margin in bar sizes.
    readable_line_reach: float = Field(default=1.2, gt=0.0, le=5.0)
    readable_line_margin: float = Field(default=0.15, ge=0.0, le=1.0)
    # Receipt-stamp re-read (T13): the page pass reads at most the stamp's small lab-name lines.
    # The area around them, this share of the page diagonal on every side, is read again; the
    # stamp's date sits above those lines (A1: ~0.08 diagonal above "המרכזית").
    stamp_reread_reach: float = Field(default=0.10, gt=0.0, le=1.0)
    stamp_reread_max: int = Field(default=2, ge=0, le=10)  # crops per page; 0 disables
    # Patient-block re-read: the page pass is unstable on the block's small bold labels beside
    # blue handwriting (A1: "פרטי המבוטח/ת" and 'ת"ז' are lost, "שם" reads as "DW"), while a
    # crop of the block reads them. The area around each group of patient-label words found,
    # this share of the page diagonal on every side, is read again (A1: the header sits ~0.03
    # diagonal above "שם").
    patient_reread_reach: float = Field(default=0.06, gt=0.0, le=1.0)
    # Crops per page; 0 disables. 3, because the provider box ("שם", "כתובת") and a footer line
    # ("המבוטח/ת") anchor groups of their own (A1).
    patient_reread_max: int = Field(default=3, ge=0, le=10)

    # --- S4 regions (T11) -------------------------------------------------
    # Printed sideways, so OCR is imperfect: one wrong letter of five scores 80. Measured on the
    # 48 sample pages (41 central stickers, 97 barcodes): 80 finds 33 stickers and 85 finds 30,
    # with no false caption at either (S4 additionally requires a sideways caption to be tall).
    central_caption_fuzz: int = Field(default=80, ge=0, le=100)
    # The plan's value, for the caption on a page with no barcode: ordinary horizontal text,
    # where 80 admits "המרכזית" and "ומרכז" (both seen on the sample SAP pages).
    upright_caption_fuzz: int = Field(default=85, ge=0, le=100)
    receipt_stamp_fuzz: int = Field(default=80, ge=0, le=100)  # faint and overprinted
    label_fuzz: int = Field(default=85, ge=0, le=100)  # patient, commitment and clinical labels
    # How far a label's value extends past the label, in page widths (reading direction).
    label_value_expand: float = Field(default=0.18, gt=0.0, le=1.0)
    letterhead_fraction: float = Field(default=0.22, gt=0.0, le=1.0)  # top share of the page
    # Learned layout hints compete with detections instead of overriding them.
    learned_region_score: float = Field(default=0.40, ge=0.0, le=1.0)

    # --- S6 candidates: central number and receipt date (T13) -----------
    # A handwritten receipt date counts when it lies this share of the page diagonal from a
    # central sticker; farther dates are the form's own. The plan's 0.10 admitted printed decoys
    # that S8 then closed wrongly (A15's letter date at 0.082, B18 at 0.061); the handwritten
    # notes on the samples sit within 0.01 (B6, B19). Measured on all 48 pages, 2026-10-22 -
    # re-calibrate on the dev split once T7 exists.
    receipt_date_radius: float = Field(default=0.04, gt=0.0, le=1.0)

    # --- TrOCR handwritten-digit reader (T13) -----------------------------
    # Files inside models_dir, written by tools/export_trocr.py (provenance: models/README.md).
    # vision = the image encoder, text = the token decoder; the names avoid "CODE" and "TOKEN",
    # which check_env_example.py treats as secrets.
    trocr_vision_file: str = "trocr_small_encoder_int8.onnx"
    trocr_text_file: str = "trocr_small_decoder_int8.onnx"
    trocr_vocab_file: str = "trocr_small_vocab.json"
    trocr_max_length: int = Field(default=12, ge=1, le=64)  # a digit field is a dozen tokens
    trocr_candidates: int = Field(default=5, ge=1, le=10)  # beam width = candidates returned
    # The pair must fit beside OpenCV and Tesseract on a 512 MB host; larger is refused.
    trocr_max_model_mb: float = Field(default=80.0, gt=0.0, le=1000.0)

    # --- S4 region detector (T16) -------------------------------------------
    # "rule_based" is the default and the baseline; "yolo_onnx" is the experiment, switched on
    # only if ADR-016's pre-declared rule holds. A missing model file falls back to rule_based.
    region_detector: Literal["rule_based", "yolo_onnx"] = "rule_based"
    yolo_model_file: str = "yolov8n_regions_int8.onnx"  # inside models_dir
    yolo_conf: float = Field(default=0.35, ge=0.0, le=1.0)  # detections below are dropped
    yolo_iou: float = Field(default=0.45, ge=0.0, le=1.0)  # NMS overlap that merges two boxes
    yolo_imgsz: int = Field(default=1024, ge=64, le=4096)  # square input side, pixels
    yolo_max_model_mb: float = Field(default=15.0, gt=0.0, le=1000.0)  # larger is refused
    # Classes whose YOLO detections replace the rule-based regions of that kind (per-class
    # choice: YOLO may win on receipt_stamp alone). Comma-separated RegionKind values.
    yolo_kinds: str = "central_sticker,receipt_stamp,patient_box,commitment_label"

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
    def yolo_kinds_list(self) -> list[str]:
        """Split the comma-separated YOLO class list. Empty string → empty list."""
        return [k.strip() for k in self.yolo_kinds.split(",") if k.strip()]

    @property
    def origins_list(self) -> list[str]:
        """Split comma-separated CORS origins."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @field_validator("allowed_origins")
    @classmethod
    def _no_wildcard_origin(cls, v: str) -> str:
        """A wildcard CORS origin is refused at start-up, development included (PLAN §8 T-8): a
        development wildcard is the one that ships."""
        if any(o.strip() == "*" for o in v.split(",")):
            raise ValueError("ALLOWED_ORIGINS must list origins explicitly; '*' is not allowed")
        return v

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
