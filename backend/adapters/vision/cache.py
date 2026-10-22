"""Content-addressed cache of validated provider answers (PLAN §4 S9 step 3).

Key: sha256 over every crop's bytes (and its field), the task, the prompt version and the model id.
A changed prompt or model therefore misses, and the same crops on a re-run make no call at all.
What is stored is the VALIDATED answer (values the model read from a crop), never an image, so it
is patient data: every entry expires after `ttl_seconds` and is deleted on the first read after
that. Files live under `data_dir`; the whole directory can be purged with the job artefacts.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import time

from pydantic import ValidationError
import structlog

from backend.core.models import VisionRequest, VisionResponse

log = structlog.get_logger()

# Structural: the cache file name suffix and the directory under data_dir.
_SUFFIX = ".json"
CACHE_DIRNAME = "vision_cache"


def cache_key(request: VisionRequest, model_id: str, prompt: str = "") -> str:
    """Hex digest identifying this request's content under `model_id`. The thumbnail is part of
    the content: the same crops with and without page context are different questions. `prompt`
    is the prompt's own fingerprint (template text, solved examples): editing either misses."""
    digest = hashlib.sha256()
    for part in (request.task, request.prompt_version, model_id, prompt):
        digest.update(part.encode("utf-8") + b"\x00")
    for crop in request.crops:
        digest.update(crop.field.value.encode("utf-8") + b"\x00" + crop.png + b"\x00")
    digest.update(b"thumb\x00" + (request.page_thumb or b""))
    for image in request.page_images:
        digest.update(b"page\x00" + image)
    digest.update(json.dumps(request.sender_options, ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()


class ResponseCache:
    """A directory of one JSON file per answer. Single writer (the worker thread)."""

    def __init__(
        self,
        directory: Path,
        *,
        ttl_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._dir = directory
        self._ttl = ttl_seconds
        self._clock = clock

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}{_SUFFIX}"

    def get(self, key: str) -> VisionResponse | None:
        """The stored answer, or None on a miss, an expired entry (deleted) or a damaged file
        (deleted). Never raises."""
        path = self._path(key)
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if self._clock() - float(stored["stored_at"]) > self._ttl:
                path.unlink(missing_ok=True)
                return None
            return VisionResponse.model_validate(stored["response"]).model_copy(
                update={"cache_hit": True, "failovers": []}
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            log.warning("vision_cache_entry_dropped", stage="S9")
            path.unlink(missing_ok=True)
            return None

    def put(self, key: str, response: VisionResponse) -> None:
        """Store a validated answer. A failure to write only costs a future cache miss."""
        entry = {"stored_at": self._clock(), "response": response.model_dump(mode="json")}
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path(key).with_suffix(".tmp")
            tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path(key))
        except OSError:
            log.warning("vision_cache_write_failed", stage="S9")

    def purge_expired(self) -> int:
        """Delete every expired or unreadable entry; the number removed."""
        removed = 0
        if not self._dir.is_dir():
            return 0
        for path in self._dir.glob(f"*{_SUFFIX}"):
            if self.get(path.stem) is None and not path.exists():
                removed += 1
        return removed

    def clear(self) -> int:
        """Delete every entry (job deletion, shutdown); the number removed."""
        removed = 0
        if self._dir.is_dir():
            for path in self._dir.glob(f"*{_SUFFIX}"):
                path.unlink(missing_ok=True)
                removed += 1
        return removed
