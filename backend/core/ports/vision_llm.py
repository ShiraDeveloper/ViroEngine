"""VisionLLMPort — contract for cloud vision providers.

Only crops leave the machine, never full pages (§5 local-first rule).
RegionKind.clinical areas are never sent — enforced by assertion in S9.
LOCAL_ONLY=true must produce zero calls to this port.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.core.errors import ProviderExhausted  # noqa: F401 (re-exported for callers)
from backend.core.models import ProviderHealth, VisionRequest, VisionResponse


@runtime_checkable
class VisionLLMPort(Protocol):
    """Extract field candidates from a crop using a cloud vision model.

    name: provider label (never the key value) — recorded in ProviderHealth.
    """

    name: str

    def extract(self, request: VisionRequest) -> VisionResponse:
        """Send a crop to the cloud provider and return a schema-validated response.

        Raises ProviderExhausted only after every credential and every provider failed.
        The caller (S9) catches ProviderExhausted and flags the field at level D.
        Never called when LOCAL_ONLY=true.
        """
        ...

    def health(self) -> ProviderHealth:
        """Return current provider health — credential count and circuit state.

        Never exposes key values, only the count and label.
        """
        ...
