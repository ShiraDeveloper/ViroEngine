"""S6 dispatcher (PLAN §4 S6, §13 ownership split).

Thin by design: it only routes a field key to the module that owns the field.
  - s6_candidates_identity.py (T12, Shira): national_id, first_name, last_name, commitment_number
  - s6_candidates_lab.py      (T13, Ahuba): central_number, receipt_date - registered here when
    that module merges; this file does not implement them.
`sender` is not generated here: it is S5's single classification (PLAN §4 S6, `sender` row).
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.core.models import (
    BarcodeHit,
    Box,
    Candidate,
    FieldKey,
    OcrResult,
    Region,
    SenderSpec,
)
from backend.core.pipeline.s6_candidates_identity import (
    IdentityConfig,
    candidates_commitment,
    candidates_names,
    candidates_national_id,
)

IDENTITY_FIELDS = (
    FieldKey.national_id,
    FieldKey.first_name,
    FieldKey.last_name,
    FieldKey.commitment_number,
)


def generate_candidates(
    ocr: OcrResult,
    barcodes: Sequence[BarcodeHit],
    regions: Sequence[Region],
    *,
    spec: SenderSpec | None,
    page_role: str,
    ink_boxes: Sequence[Box] = (),
    identity: IdentityConfig | None = None,
) -> dict[FieldKey, list[Candidate]]:
    """Candidates for every field this module owns; each key is present, empty meaning
    `not_found`. Never raises."""
    names = candidates_names(ocr, regions, ink_boxes=ink_boxes, config=identity)
    return {
        FieldKey.national_id: candidates_national_id(
            ocr, barcodes, regions, ink_boxes=ink_boxes, config=identity
        ),
        FieldKey.first_name: names.get(FieldKey.first_name, []),
        FieldKey.last_name: names.get(FieldKey.last_name, []),
        FieldKey.commitment_number: candidates_commitment(
            ocr, barcodes, regions, spec, page_role, ink_boxes=ink_boxes, config=identity
        ),
    }
