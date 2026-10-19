"""Safe Line error hierarchy.

Each subclass carries a stable `code` (used in JSON responses and logs)
and an `http_status`. Add a new subclass rather than overloading an existing one.

A single-page failure must never abort a batch — callers catch the specific
subclass rather than the base class.
"""

from __future__ import annotations


class SafeLineError(Exception):
    """Base class for all domain errors.

    Never raises — callers catch subclasses or this base class.
    """

    code: str = "SAFELINE_ERROR"
    recoverable: bool = False
    http_status: int = 500

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, detail={self.detail!r})"


# ---------------------------------------------------------------------------
# Upload / intake errors (S0)
# ---------------------------------------------------------------------------


class UnsupportedMedia(SafeLineError):
    """Content type is not application/pdf, image/png, image/jpeg, or application/zip."""

    code = "UNSUPPORTED_MEDIA"
    http_status = 415


class TooLarge(SafeLineError):
    """Upload exceeds MAX_UPLOAD_MB or page count exceeds MAX_PAGES."""

    code = "TOO_LARGE"
    http_status = 413


class EncryptedPdf(SafeLineError):
    """PDF is encrypted and cannot be rendered."""

    code = "ENCRYPTED_PDF"
    http_status = 422


class ActiveContentPdf(SafeLineError):
    """PDF contains JavaScript, embedded files, or other active content."""

    code = "ACTIVE_CONTENT_PDF"
    http_status = 422


class RenderFailed(SafeLineError):
    """A single page could not be rendered. Recoverable: the batch continues."""

    code = "RENDER_FAILED"
    http_status = 422
    recoverable = True


# ---------------------------------------------------------------------------
# OCR errors
# ---------------------------------------------------------------------------


class OcrUnavailable(SafeLineError):
    """Tesseract binary not found or Hebrew language pack missing.

    This is a startup failure — /v1/health will report unready and no jobs
    will be accepted until the binary is available.
    """

    code = "OCR_UNAVAILABLE"
    http_status = 503


# ---------------------------------------------------------------------------
# Provider errors (S9)
# ---------------------------------------------------------------------------


class ProviderRateLimited(SafeLineError):
    """A cloud provider returned 429. Rotate to the next credential."""

    code = "PROVIDER_RATE_LIMITED"
    http_status = 429
    recoverable = True


class ProviderInvalidKey(SafeLineError):
    """A credential was rejected (401/403). Mark dead for this process."""

    code = "PROVIDER_INVALID_KEY"
    http_status = 401


class ProviderBadResponse(SafeLineError):
    """A provider returned a response that fails schema validation.

    Retry once with a schema reminder; if it fails again the field is unresolved.
    """

    code = "PROVIDER_BAD_RESPONSE"
    http_status = 422
    recoverable = True


class ProviderExhausted(SafeLineError):
    """All credentials and all providers failed.

    Never fails the run — affected fields move to level D with reason
    'providers_exhausted'.
    """

    code = "PROVIDER_EXHAUSTED"
    http_status = 503
    recoverable = False


# ---------------------------------------------------------------------------
# Job lifecycle errors
# ---------------------------------------------------------------------------


class JobPurged(SafeLineError):
    """Job was purged (TTL expired or explicit DELETE)."""

    code = "JOB_PURGED"
    http_status = 404


class JobNotFound(SafeLineError):
    """Job id does not exist."""

    code = "JOB_NOT_FOUND"
    http_status = 404


class EditConflict(SafeLineError):
    """Attempt to edit a confirmed or purged job."""

    code = "EDIT_CONFLICT"
    http_status = 409
