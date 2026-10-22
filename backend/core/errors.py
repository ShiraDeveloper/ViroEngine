"""SafeLineError hierarchy (PLAN §3.5). Each subclass has a stable `code`; add a new
subclass rather than overloading an existing one."""

from __future__ import annotations


class SafeLineError(Exception):
    """Base class. `code` is stable and appears in logs, SSE and API errors."""

    code: str = "INTERNAL"
    recoverable: bool = False
    http_status: int = 500

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.code)
        self.detail = detail

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, detail={self.detail!r})"


class UnsupportedMedia(SafeLineError):
    """Upload content is not PDF/PNG/JPEG/ZIP (judged on content, never extension)."""

    code = "UNSUPPORTED_MEDIA"
    http_status = 415


class TooLarge(SafeLineError):
    """Upload exceeds MAX_UPLOAD_MB or MAX_PAGES."""

    code = "TOO_LARGE"
    http_status = 413


class EncryptedPdf(SafeLineError):
    """PDF is encrypted (T-3)."""

    code = "ENCRYPTED_PDF"
    http_status = 415


class ActiveContentPdf(SafeLineError):
    """PDF catalog holds JavaScript or an auto-launch action (T-2)."""

    code = "ACTIVE_CONTENT_PDF"
    http_status = 415


class RenderFailed(SafeLineError):
    """One page failed to render; that page is marked failed and the batch continues."""

    code = "RENDER_FAILED"
    recoverable = True
    http_status = 422


class OcrUnavailable(SafeLineError):
    """Startup failure: Tesseract or its Hebrew data is missing. No jobs are accepted."""

    code = "OCR_UNAVAILABLE"
    http_status = 503


class ProviderRateLimited(SafeLineError):
    """429 from a provider: rotate the credential, then the provider."""

    code = "PROVIDER_RATE_LIMITED"
    recoverable = True
    http_status = 502


class ProviderInvalidKey(SafeLineError):
    """401/403 from a provider: credential is dead for this process."""

    code = "PROVIDER_INVALID_KEY"
    recoverable = True
    http_status = 502


class ProviderBadResponse(SafeLineError):
    """Response failed schema validation; one retry, then the field stays unresolved."""

    code = "PROVIDER_BAD_RESPONSE"
    recoverable = True
    http_status = 502


class ProviderExhausted(SafeLineError):
    """Every credential and provider failed. Fields become level D; the run continues.

    `failovers` lists the "from->to" hops and per-provider failures that led here (provider and
    credential labels only, never a key), so the run report can show them."""

    code = "PROVIDER_EXHAUSTED"
    recoverable = True
    http_status = 502

    def __init__(self, detail: str = "", *, failovers: tuple[str, ...] = ()) -> None:
        super().__init__(detail)
        self.failovers = failovers


class JobPurged(SafeLineError):
    """The job's artefacts were purged after JOB_TTL_MINUTES or by DELETE."""

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


ALL_ERRORS: tuple[type[SafeLineError], ...] = (
    UnsupportedMedia,
    TooLarge,
    EncryptedPdf,
    ActiveContentPdf,
    RenderFailed,
    OcrUnavailable,
    ProviderRateLimited,
    ProviderInvalidKey,
    ProviderBadResponse,
    ProviderExhausted,
    JobPurged,
    JobNotFound,
    EditConflict,
)
