"""tools/smoke_test.py — Post-deploy smoke test for Safe Line.

Run:
    python -m tools.smoke_test --api https://api.example.com --web https://app.example.com

Checks:
  1. GET /v1/health returns {"status": "ok"}
  2. GET /v1/senders without a key returns 401 (auth guard is live)
  3. GET <web_url> returns a 2xx response

Exits 0 when all checks pass, 1 on any failure.
Only stdlib + httpx are used — matches the deploy workflow pip install.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

import httpx


def check_health(api_url: str) -> None:
    """GET /v1/health → must return {"status": "ok"}.

    Raises on non-2xx or unexpected body.
    """
    url = f"{api_url.rstrip('/')}/v1/health"
    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != "ok":
        raise ValueError(f"/v1/health returned unexpected body: {body}")
    print(f"✓ health: {url}")


def check_auth_required(api_url: str) -> None:
    """GET /v1/senders without a key → must return 401.

    Verifies the auth middleware is active in the deployed instance.
    Raises on any other status code.
    """
    url = f"{api_url.rstrip('/')}/v1/senders"
    resp = httpx.get(url, timeout=10)
    if resp.status_code != 401:
        raise ValueError(
            f"/v1/senders without auth key: expected 401, got {resp.status_code}"
        )
    print(f"✓ auth guard: {url}")


def check_web(web_url: str) -> None:
    """GET <web_url> → must return a 2xx response.

    Follows redirects. Raises on 4xx/5xx.
    """
    resp = httpx.get(web_url, timeout=30, follow_redirects=True)
    if resp.status_code >= 400:
        raise ValueError(
            f"web URL returned {resp.status_code}: {web_url}"
        )
    print(f"✓ web: {web_url}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the post-deploy smoke test."""
    parser = argparse.ArgumentParser(description="Post-deploy smoke test for Safe Line")
    parser.add_argument("--api", required=True, help="Public API base URL (e.g. https://...)")
    parser.add_argument("--web", required=True, help="Public web app URL (e.g. https://...)")
    args = parser.parse_args(argv)

    checks: list[tuple[Callable[[str], None], str]] = [
        (check_health, args.api),
        (check_auth_required, args.api),
        (check_web, args.web),
    ]

    failures: list[str] = []
    for fn, arg in checks:
        try:
            fn(arg)
        except Exception as exc:
            msg = f"{fn.__name__}: {exc}"
            failures.append(msg)
            print(f"✗ {msg}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} smoke check(s) failed.", file=sys.stderr)
        return 1

    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
