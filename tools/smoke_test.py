"""Post-deploy smoke test for Safe Line (PLAN §11, T28).

    python -m tools.smoke_test --api https://safeline-api.onrender.com --web https://safeline.vercel.app

Checks, in order; any failure makes the exit code 1 and the deploy workflow red:

1. `GET /v1/health` is 200, `status: ok`, and the OCR language list contains `heb`;
2. `GET /v1/senders` without a key is 401 (the guard is live), and 200 with the key when one is given;
3. the web app answers 200 and serves a right-to-left Hebrew document;
4. an upload round trip: a tiny page is uploaded, the job is followed until it ends, then deleted.

The round trip needs `API_KEY` (and `APP_ACCESS_CODE` if the deployment sets one) in the
environment; without `API_KEY` it is reported as skipped, never as passed. Only the standard
library and httpx are used, matching the deploy workflow. No credential is ever printed.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import os
import struct
import sys
import time
import uuid
import zlib

import httpx

# Structural: the slowest the backend is allowed to be before the smoke test calls it broken. A free
# tier cold start is slow, hence a generous first-request timeout.
HEALTH_TIMEOUT = 60.0
JOB_TIMEOUT = 120.0
POLL_INTERVAL = 1.0
_TERMINAL = frozenset({"needs_review", "confirmed", "failed", "purged"})
_GOOD_END = frozenset({"needs_review", "confirmed"})


class SmokeFailure(AssertionError):
    """A smoke check did not hold. The message is safe to print."""


def tiny_png(size: int = 64) -> bytes:
    """A valid white PNG of `size` x `size` pixels, built with the standard library only."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    rows = b"".join(b"\x00" + b"\xff" * (3 * size) for _ in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def check_health(client: httpx.Client, api: str) -> str:
    """The backend is up and can read Hebrew. Raises SmokeFailure."""
    response = client.get(f"{api.rstrip('/')}/v1/health", timeout=HEALTH_TIMEOUT)
    body = (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if response.status_code != 200 or body.get("status") != "ok":
        raise SmokeFailure(f"/v1/health answered {response.status_code} {body.get('status')!r}")
    langs = body.get("ocr_langs", [])
    if "heb" not in langs:
        raise SmokeFailure(f"/v1/health reports no Hebrew OCR pack (languages: {langs})")
    return f"health: ok (tesseract {','.join(langs)})"


def check_auth(client: httpx.Client, api: str, key: str | None) -> str:
    """The key guard is live (401 without a key) and the key opens it. Raises SmokeFailure."""
    url = f"{api.rstrip('/')}/v1/senders"
    bare = client.get(url, timeout=15)
    if bare.status_code != 401:
        raise SmokeFailure(f"/v1/senders without a key answered {bare.status_code}, expected 401")
    if key:
        with_key = client.get(url, headers={"X-API-Key": key}, timeout=15)
        if with_key.status_code != 200:
            raise SmokeFailure(f"/v1/senders with the key answered {with_key.status_code}")
        return "auth: 401 as expected, key accepted"
    return "auth: 401 as expected"


def check_frontend(client: httpx.Client, web: str) -> str:
    """The web app is served as a right-to-left Hebrew document. Raises SmokeFailure."""
    response = client.get(web, timeout=30, follow_redirects=True)
    if response.status_code != 200:
        raise SmokeFailure(f"the web app answered {response.status_code}")
    html = response.text.lower()
    if 'dir="rtl"' not in html:
        raise SmokeFailure('the web app does not declare dir="rtl"')
    if 'lang="he"' not in html:
        raise SmokeFailure('the web app does not declare lang="he"')
    return f'frontend: 200, dir="rtl" lang="he" ({web})'


def check_roundtrip(
    client: httpx.Client,
    api: str,
    key: str,
    access_code: str | None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = JOB_TIMEOUT,
) -> str:
    """Upload a tiny page, follow the job to its end, delete it. Raises SmokeFailure."""
    base = api.rstrip("/")
    headers = {"X-API-Key": key, "Idempotency-Key": f"smoke-{uuid.uuid4()}"}
    if access_code:
        headers["X-Access-Code"] = access_code
    started = time.monotonic()
    created = client.post(
        f"{base}/v1/documents",
        headers=headers,
        files={"file": ("smoke.png", tiny_png(), "image/png")},
        timeout=60,
    )
    if created.status_code != 201:
        raise SmokeFailure(f"the upload answered {created.status_code}")
    job_id = created.json()["job_id"]
    auth = {"X-API-Key": key}
    status = created.json().get("status", "queued")
    waited = 0.0
    job: dict[str, object] = {}
    try:
        while status not in _TERMINAL:
            if waited >= timeout:
                raise SmokeFailure(
                    f"the job did not finish in {int(timeout)} s (last status {status})"
                )
            sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL
            polled = client.get(f"{base}/v1/jobs/{job_id}", headers=auth, timeout=30)
            if polled.status_code != 200:
                raise SmokeFailure(f"polling the job answered {polled.status_code}")
            job = polled.json()
            status = str(job.get("status"))
        if status not in _GOOD_END:
            raise SmokeFailure(f"the job ended as {status!r}")
    finally:
        client.delete(f"{base}/v1/jobs/{job_id}", headers=auth, timeout=30)
    rows = job.get("rows", [])
    pages = job.get("page_count", 0)
    return f"upload roundtrip: {pages} page, {len(rows) if isinstance(rows, list) else '?'} row, {time.monotonic() - started:.1f}s"


def main(argv: list[str] | None = None, *, client: httpx.Client | None = None) -> int:
    """Run every check. Returns 0 when all passed, 1 otherwise."""
    parser = argparse.ArgumentParser(description="Post-deploy smoke test for Safe Line")
    parser.add_argument("--api", required=True, help="public API base URL")
    parser.add_argument("--web", required=True, help="public web app URL")
    args = parser.parse_args(argv)
    key = os.environ.get("API_KEY") or None
    access_code = os.environ.get("APP_ACCESS_CODE") or None

    http = client or httpx.Client()
    checks: list[tuple[str, Callable[[], str]]] = [
        ("health", lambda: check_health(http, args.api)),
        ("auth", lambda: check_auth(http, args.api, key)),
        ("frontend", lambda: check_frontend(http, args.web)),
    ]
    if key:
        checks.append(("roundtrip", lambda: check_roundtrip(http, args.api, key, access_code)))

    failures = 0
    for name, run in checks:
        try:
            print(f"ok   {run()}")
        except (SmokeFailure, httpx.HTTPError, ValueError, KeyError) as exc:
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    if not key:
        print("skip upload roundtrip: API_KEY is not set in the environment")
    if failures:
        print(f"\n{failures} smoke check(s) failed.", file=sys.stderr)
        return 1
    print("\nall smoke checks passed" + ("" if key else " (round trip skipped)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
