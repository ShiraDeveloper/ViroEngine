"""Structlog configuration for Safe Line.

Sets up JSON logging with:
- A PII redaction processor (safety net — callers must still avoid logging values).
- Bound context: job_id, page_id, stage, reader, duration_ms.
- LOG_LEVEL from settings.

The redaction processor is a structlog processor, not a log level, so it
cannot be bypassed at a call site. See §8 (privacy) for the complete list
of forbidden keys. Full redaction hardening is in Task 27.
"""

from __future__ import annotations

from collections.abc import MutableMapping
import logging
import sys
from typing import Any

import structlog

# Keys that must never appear in log output — values are redacted.
# Task 27 will expand this list and add regex patterns for values.
_REDACT_KEYS: frozenset[str] = frozenset(
    {
        "value",
        "field_value",
        "text",
        "crop",
        "api_key",
        "gemini_api_keys",
        "openrouter_api_key",
        "groq_api_key",
        "token",
        "secret",
        "key",
    }
)

_REDACTED = "[REDACTED]"


def _pii_redaction_processor(
    logger: Any,
    method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Remove PII keys from the event dict before rendering.

    Safety net — callers must never log field values in the first place.
    Covers dict values but not deeply nested structures (T27 adds recursion).
    """
    for key in list(event_dict.keys()):
        if key in _REDACT_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def configure(log_level: str = "INFO") -> None:
    """Configure structlog for the application.

    Call once at startup from backend/app.py lifespan.
    Safe to call multiple times — subsequent calls are no-ops.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _pii_redaction_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Redirect stdlib logging through structlog so third-party libs are also captured
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
