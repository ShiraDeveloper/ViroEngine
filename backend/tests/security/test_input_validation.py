"""Hostile uploads (PLAN §8 T-1..T-5). The cases are the S0 intake tests; they are collected here
too so the security suite alone proves every input control. The HTTP-level size controls
(`test_oversized_upload_is_413`, the body cap) sit in `test_auth.py` and `test_api_contract.py`."""

from __future__ import annotations

from backend.tests.unit.test_intake import (  # noqa: F401  (collected as tests of this module)
    test_rejects_content_that_is_no_accepted_type,
    test_rejects_encrypted_pdf,
    test_rejects_nested_zip,
    test_rejects_oversized_upload,
    test_rejects_pdf_with_javascript,
    test_rejects_pdf_with_other_active_content,
    test_rejects_png_renamed_to_pdf,
    test_rejects_too_many_pages,
    test_rejects_zip_bomb,
    test_rejects_zip_path_traversal,
)
