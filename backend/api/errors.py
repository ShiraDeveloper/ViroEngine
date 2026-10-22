"""Errors that exist only at the HTTP boundary.

They subclass `SafeLineError` so the application's one handler turns them into
`{"error": CODE, "recoverable": bool}` with the right status. They are deliberately not part of
`core.errors.ALL_ERRORS`, which is the frozen domain contract (T2): nothing in the core raises them.
"""

from __future__ import annotations

from backend.core.errors import SafeLineError


class Unauthorized(SafeLineError):
    """Missing or wrong `X-API-Key` or `X-Access-Code`."""

    code = "UNAUTHORIZED"
    http_status = 401


class RowNotFound(SafeLineError):
    """The job has no such row."""

    code = "ROW_NOT_FOUND"
    http_status = 404


class FieldNotFound(SafeLineError):
    """The row has no such field."""

    code = "FIELD_NOT_FOUND"
    http_status = 404


class PageNotFound(SafeLineError):
    """The job has no such page, or its image is gone."""

    code = "PAGE_NOT_FOUND"
    http_status = 404


class JobNotReady(SafeLineError):
    """The job has not finished, so there is nothing to confirm or export yet."""

    code = "JOB_NOT_READY"
    http_status = 409


class NothingToUndo(SafeLineError):
    """The row has no edit left to undo."""

    code = "NOTHING_TO_UNDO"
    http_status = 409


class InvalidEdit(SafeLineError):
    """The submitted value breaks a hard rule (ID check digit, a date that exists and is not in
    the future) or is not in the closed list. The message never contains the value."""

    code = "EDIT_INVALID"
    http_status = 422


class BadBox(SafeLineError):
    """The crop box is not four numbers in 0..1 describing a non-empty region."""

    code = "BAD_BOX"
    http_status = 422


class BadStress(SafeLineError):
    """The stress request names an operation that does not exist."""

    code = "BAD_STRESS_OP"
    http_status = 422


class StressUnavailable(SafeLineError):
    """No augmenter is installed (Stress Mode ships with T25)."""

    code = "STRESS_UNAVAILABLE"
    http_status = 501
