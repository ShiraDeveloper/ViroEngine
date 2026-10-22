"""Structlog configuration for Safe Line (PLAN §8 T-14, T-17).

JSON logging with a redaction processor that is the LAST step before rendering, so nothing a call
site, an exception message or a library puts in an event can reach a sink without passing it:

* keys that hold personal data or credentials are replaced, at any depth of dicts, lists, tuples
  and sets (matching is case-insensitive);
* any other object (a model, a bytes blob, an exception) is replaced by its type name, never by its
  `repr`, because a `repr` is where values hide;
* free text is scrubbed of anything that looks like an API key, and of the exact secrets this
  process was configured with (`add_secret`), so a key echoed inside a message is still removed.

It is a safety net, not a licence: callers must still avoid logging values (code-style rule).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
import logging
import re
import sys
from typing import Any, Final

import structlog

REDACTED: Final = "[REDACTED]"

# Structural: keys whose value is personal data or a credential (PLAN §8.4, T-14). Compared
# case-insensitively, at any nesting depth.
REDACT_KEYS: Final = frozenset(
    {
        "value",
        "values",
        "field_value",
        "display_value",
        "old_value",
        "new_value",
        "text",
        "raw_text",
        "payload",
        "crop",
        "png",
        "image",
        "candidates",
        "alts",
        "answers",
        "filename",
        "first_name",
        "last_name",
        "national_id",
        "central_number",
        "central_numbers",
        "commitment_number",
        "receipt_date",
        "api_key",
        "gemini_api_keys",
        "openrouter_api_key",
        "groq_api_key",
        "access_code",
        "app_access_code",
        "x-api-key",
        "x-access-code",
        "authorization",
        "key",
        "token",
        "secret",
        "password",
    }
)

# Structural: shapes of credentials seen in the wild, scrubbed from free text. Provider-specific
# prefixes first, then generic "api_key=..." / "Bearer ..." forms.
_SECRET_PATTERNS: Final = tuple(
    re.compile(p)
    for p in (
        r"AIza[0-9A-Za-z_\-]{20,}",
        r"\bsk-[A-Za-z0-9_\-]{16,}",
        r"\bgsk_[A-Za-z0-9]{16,}",
        r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}",
        r"(?i)\b(?:api[_-]?key|access[_-]?code|token|secret|password)\s*[=:]\s*\S+",
    )
)
# Structural: a configured secret shorter than this is not scrubbed from text, because replacing
# a 3-letter string everywhere would destroy the log without protecting anything.
_MIN_SECRET_LENGTH: Final = 8
# Structural: recursion bound; anything deeper is replaced whole.
_MAX_DEPTH: Final = 8

_secrets: set[str] = set()
_PRIMITIVES: Final = (str, int, float, bool, type(None))


def add_secret(value: str) -> None:
    """Remember a secret of this process (an API key, the access code) so it is scrubbed from any
    log text. Values shorter than 8 characters are ignored."""
    if len(value) >= _MIN_SECRET_LENGTH:
        _secrets.add(value)


def clear_secrets() -> None:
    """Forget every registered secret (tests)."""
    _secrets.clear()


def scrub_text(text: str) -> str:
    """`text` with registered secrets and credential-looking strings replaced."""
    for secret in _secrets:
        text = text.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact_value(value: Any, depth: int = 0) -> Any:
    """A copy of `value` that is safe to render: sensitive keys replaced at any depth, strings
    scrubbed, and every object that is not a plain container or primitive reduced to its type."""
    if depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, _PRIMITIVES):
        return value
    if isinstance(value, Mapping):
        return {
            k: REDACTED if str(k).lower() in REDACT_KEYS else redact_value(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_value(v, depth + 1) for v in value]
    return f"<{type(value).__name__}>"


def redact(
    logger: Any, method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """The structlog processor: redact the whole event, top-level keys included."""
    for key in list(event_dict):
        if str(key).lower() in REDACT_KEYS:
            event_dict[key] = REDACTED
        else:
            event_dict[key] = redact_value(event_dict[key])
    return event_dict


# Kept for callers and tests written against the earlier name.
_pii_redaction_processor = redact


def configure(log_level: str = "INFO", *, secrets: Iterable[str] = ()) -> None:
    """Configure structlog for the application.

    `secrets` are the credentials of this process; they are scrubbed from any message. Call once at
    start-up from the lifespan; calling again reconfigures and is harmless."""
    for secret in secrets:
        add_secret(secret)
    level = getattr(logging, log_level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact,  # last before rendering: nothing is added after it
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    # Third-party libraries log through the standard library; their messages are plain text.
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
