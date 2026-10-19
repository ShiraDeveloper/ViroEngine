"""Build config/senders.json from the public hospital list plus the committed seed (T6).

    python -m tools.build_senders --out config/senders.json --seed config/senders.seed.json

Offline tool, never called at runtime. Any fetch failure (filtered network, HTTP 418, a redirect
to another origin, a timeout, unparseable JSON) falls back to the seed and the build still
succeeds. Manual enrichment already present in --out is merged in by `canonical`.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from backend.adapters.senders.loader import SenderIndex, normalize_text
from backend.core.errors import SafeLineError

log = structlog.get_logger()

WIKI_PAGE = "בתי_חולים_בישראל"
WIKI_API = "https://he.wikipedia.org/w/api.php"
USER_AGENT = "SafeLine-build-senders/0.1 (offline list build)"
FETCH_TIMEOUT_S = 10.0  # structural: the build must not hang on a filtered network

# Labels and deny entries every institution gets (PLAN T6 step 6); unioned in by the build.
DEFAULT_COMMITMENT_DENY = ["מקרה", "מס' ביקור", "מס' קליטה", "מספר מבחנה"]
GENERIC_COMMITMENT_LABELS = ["מספר התחייבות", "התחייבות"]
LIST_FIELDS = ("aliases", "keywords", "commitment_labels", "commitment_deny", "pattern_hints")

_LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
_HOSPITAL_WORDS = ("חולים", "רפוא", "הדסה")
_SKIP_PREFIXES = ("קטגוריה:", "קובץ:", "תמונה:", "ויקיפדיה:", "תבנית:", "category:", "file:")


class SendersBuildError(SafeLineError):
    """The seed or the existing output is unreadable or fails SenderSpec validation."""

    code = "SENDERS_BUILD_FAILED"


def parse_hospital_names(wikitext: str) -> list[str]:
    """Hospital names linked from table rows of the article wikitext, in order, de-duplicated.
    Keeps links whose name mentions a hospital word; never raises."""
    names: list[str] = []
    for line in wikitext.splitlines():
        if not line.lstrip().startswith(("|", "!")):
            continue
        for target, label in _LINK.findall(line):
            name = re.sub(r"\s*\([^)]*\)", "", (label or target)).strip()
            if not name or name.lower().startswith(_SKIP_PREFIXES):
                continue
            if any(w in name for w in _HOSPITAL_WORDS) and name not in names:
                names.append(name)
    return names


def fetch_hospitals_from_wikipedia(
    timeout_s: float = FETCH_TIMEOUT_S, *, client: httpx.Client | None = None
) -> list[str] | None:
    """Via the Wikipedia API (action=parse). Returns None on ANY failure
    (including a filtered network) so the caller falls back to the seed."""
    params = {"action": "parse", "page": WIKI_PAGE, "prop": "wikitext", "format": "json"}
    owned = client is None
    http = client or httpx.Client(timeout=timeout_s, headers={"User-Agent": USER_AGENT})
    try:
        response = http.get(WIKI_API, params=params, follow_redirects=False)
        if response.status_code != 200:
            log.warning("senders_fetch_failed", reason="status", status=response.status_code)
            return None
        if urlparse(str(response.url)).netloc != urlparse(WIKI_API).netloc:
            log.warning("senders_fetch_failed", reason="cross_origin")
            return None
        wikitext = response.json()["parse"]["wikitext"]["*"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.warning("senders_fetch_failed", reason=type(exc).__name__)
        return None
    finally:
        if owned:
            http.close()
    names = parse_hospital_names(str(wikitext))
    return names or None


def _union(first: list[str], second: list[str]) -> list[str]:
    return list(dict.fromkeys([*first, *second]))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SendersBuildError(f"cannot read {path}: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("senders"), list):
        raise SendersBuildError(f"{path} is not a senders document")
    return data


def _preserve_manual(entries: list[dict[str, Any]], previous: dict[str, Any] | None) -> None:
    """Union list fields from the previous output into the matching seed entries (by canonical)."""
    if previous is None:
        return
    old = {e["canonical"]: e for e in previous.get("senders", []) if isinstance(e, dict)}
    for entry in entries:
        prior = old.get(entry["canonical"])
        if prior is None:
            continue
        for field in LIST_FIELDS:
            entry[field] = _union(entry.get(field, []), list(prior.get(field, [])))
        if entry.get("notes") is None and prior.get("notes"):
            entry["notes"] = prior["notes"]
        if entry.get("barcode_is_commitment") is None:
            entry["barcode_is_commitment"] = prior.get("barcode_is_commitment")


def _merge_hospitals(doc: dict[str, Any], hospitals: list[str], threshold: int) -> int:
    """Mark seed entries found in the public list as `wikipedia`; append the rest. Returns added."""
    probe = SenderIndex({**doc, "senders": [_with_defaults(e) for e in doc["senders"]]})
    added = 0
    for name in hospitals:
        if not normalize_text(name) or probe.clean(name) == "":
            continue
        hit = probe.match(name, min_ratio=threshold)
        if hit is not None:
            entry = next(e for e in doc["senders"] if e["canonical"] == hit.canonical)
            if entry["source"] == "seed":
                entry["source"] = "wikipedia"
            continue
        doc["senders"].append(
            {
                "canonical": name,
                "family": "hospital",
                "source": "wikipedia",
                "aliases": [name],
                "keywords": [name],
                "commitment_labels": list(GENERIC_COMMITMENT_LABELS),
                "notes": "From the public list; grammar not yet observed, generic labels.",
            }
        )
        added += 1
    return added


def _with_defaults(entry: dict[str, Any]) -> dict[str, Any]:
    out = dict(entry)
    for field in LIST_FIELDS:
        out.setdefault(field, [])
    out.setdefault("barcode_is_commitment", None)
    out.setdefault("notes", None)
    return out


def build(
    seed_path: Path,
    out_path: Path,
    *,
    fetch: Callable[[], list[str] | None] = fetch_hospitals_from_wikipedia,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write `out_path` and return the document. Raises SendersBuildError only when the seed or
    the finished document is invalid; a failed fetch is never an error."""
    doc = _read_json(seed_path)
    previous: dict[str, Any] | None = None
    if out_path.exists():
        try:
            previous = _read_json(out_path)
        except SendersBuildError:
            log.warning("senders_previous_output_ignored")
    entries = [_with_defaults(e) for e in doc["senders"]]
    _preserve_manual(entries, previous)
    doc["senders"] = entries

    doc["fetched_at"] = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    hospitals = fetch()
    status = "live" if hospitals else "seed_fallback"
    added = 0
    if hospitals:
        added = _merge_hospitals(doc, hospitals, int(doc["match"]["min_ratio"]))
    for entry in doc["senders"]:
        entry["commitment_deny"] = _union(entry.get("commitment_deny", []), DEFAULT_COMMITMENT_DENY)
    doc["fetch_status"] = status
    ordered = {k: doc[k] for k in _TOP_ORDER if k in doc}
    try:
        SenderIndex(ordered)  # validates every entry against SenderSpec
    except (ValueError, KeyError) as exc:
        raise SendersBuildError(f"built list is invalid: {type(exc).__name__}: {exc}") from exc
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("senders_built", status=status, total=len(doc["senders"]), added=added)
    return ordered


_TOP_ORDER = (
    "version",
    "source_url",
    "fetched_at",
    "fetch_status",
    "match",
    "destination_suppress",
    "generic_tokens",
    "global_commitment_deny",
    "closed_list_policy",
    "senders",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build config/senders.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        doc = build(args.seed, args.out)
    except SendersBuildError as exc:
        print(f"ERROR [{exc.code}]: {exc.detail}", file=sys.stderr)
        return 1
    print(
        f"wrote {args.out}: {len(doc['senders'])} institutions, fetch_status={doc['fetch_status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
