"""The post-deploy smoke test (T28): each check is proved against a scripted server."""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
from PIL import Image
import pytest

from tools import smoke_test
from tools.smoke_test import (
    SmokeFailure,
    check_auth,
    check_frontend,
    check_health,
    check_roundtrip,
    main,
    tiny_png,
)

API, WEB = "https://api.example.test", "https://web.example.test"
KEY = "TESTKEY_SMOKE_0001"
GOOD_HEALTH = {"status": "ok", "ocr": "ready", "ocr_langs": ["eng", "heb"]}
RTL_HTML = '<!doctype html><html lang="he" dir="rtl"><body>safe line</body></html>'


def client_for(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def json_response(body: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def test_tiny_png_is_a_valid_image() -> None:
    image = Image.open(io.BytesIO(tiny_png(32)))
    image.load()

    assert image.size == (32, 32) and image.format == "PNG"


def test_health_ok_with_hebrew() -> None:
    client = client_for(lambda r: json_response(GOOD_HEALTH))

    assert "heb" in check_health(client, API)


def test_health_without_the_hebrew_pack_fails() -> None:
    client = client_for(lambda r: json_response({**GOOD_HEALTH, "ocr_langs": ["eng"]}))

    with pytest.raises(SmokeFailure, match="Hebrew"):
        check_health(client, API)


@pytest.mark.parametrize("status", [503, 500, 404])
def test_unhealthy_backend_fails(status: int) -> None:
    client = client_for(lambda r: json_response({"status": "unready"}, status))

    with pytest.raises(SmokeFailure):
        check_health(client, API)


def test_protected_route_requires_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response([], 200 if request.headers.get("x-api-key") == KEY else 401)

    client = client_for(handler)

    assert check_auth(client, API, None).startswith("auth: 401")
    assert "key accepted" in check_auth(client, API, KEY)
    with pytest.raises(SmokeFailure, match="with the key"):
        check_auth(client, API, "wrong")


def test_an_open_api_fails_the_auth_check() -> None:
    client = client_for(lambda r: json_response([], 200))

    with pytest.raises(SmokeFailure, match="expected 401"):
        check_auth(client, API, None)


def test_frontend_serves_rtl_document() -> None:
    client = client_for(lambda r: httpx.Response(200, text=RTL_HTML))

    assert "dir=" in check_frontend(client, WEB)


@pytest.mark.parametrize(
    "html",
    ['<html lang="he"><body></body></html>', '<html dir="rtl"><body></body></html>'],
    ids=["no dir", "no lang"],
)
def test_frontend_without_rtl_hebrew_fails(html: str) -> None:
    client = client_for(lambda r: httpx.Response(200, text=html))

    with pytest.raises(SmokeFailure):
        check_frontend(client, WEB)


def test_frontend_error_status_fails() -> None:
    client = client_for(lambda r: httpx.Response(502))

    with pytest.raises(SmokeFailure, match="502"):
        check_frontend(client, WEB)


class FakeServer:
    """A deployment that accepts one upload and finishes it after `polls` polls."""

    def __init__(
        self, final: str = "needs_review", polls: int = 2, upload_status: int = 201
    ) -> None:
        self.final, self.polls, self.upload_status = final, polls, upload_status
        self.seen: list[tuple[str, str]] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append((request.method, request.url.path))
        self.headers.append(dict(request.headers))
        if request.method == "POST":
            return json_response({"job_id": "job-1", "status": "queued"}, self.upload_status)
        if request.method == "DELETE":
            return httpx.Response(204)
        self.polls -= 1
        status = "running" if self.polls > 0 else self.final
        return json_response({"status": status, "rows": [], "page_count": 1})


def test_upload_roundtrip_small_file() -> None:
    server = FakeServer()

    message = check_roundtrip(client_for(server), API, KEY, "CODE", sleep=lambda s: None)

    assert message.startswith("upload roundtrip: 1 page")
    assert server.seen[0] == ("POST", "/v1/documents")
    assert server.seen[-1] == ("DELETE", "/v1/jobs/job-1"), "the job is cleaned up"
    first = server.headers[0]
    assert first["x-api-key"] == KEY and first["x-access-code"] == "CODE"
    assert first["idempotency-key"].startswith("smoke-")


def test_roundtrip_deletes_the_job_even_when_it_failed() -> None:
    server = FakeServer(final="failed")

    with pytest.raises(SmokeFailure, match="failed"):
        check_roundtrip(client_for(server), API, KEY, None, sleep=lambda s: None)

    assert server.seen[-1][0] == "DELETE"


def test_roundtrip_fails_when_the_upload_is_refused() -> None:
    with pytest.raises(SmokeFailure, match="429"):
        check_roundtrip(
            client_for(FakeServer(upload_status=429)), API, KEY, None, sleep=lambda s: None
        )


def test_roundtrip_gives_up_after_the_timeout() -> None:
    server = FakeServer(polls=10_000)

    with pytest.raises(SmokeFailure, match="did not finish"):
        check_roundtrip(client_for(server), API, KEY, None, sleep=lambda s: None, timeout=3)


def full_server(health: dict[str, Any] = GOOD_HEALTH) -> Any:
    jobs = FakeServer()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/health":
            return json_response(health)
        if path == "/v1/senders":
            return json_response([], 200 if request.headers.get("x-api-key") == KEY else 401)
        if request.url.host == "web.example.test":
            return httpx.Response(200, text=RTL_HTML)
        return jobs(request)

    return handler


def test_main_passes_when_everything_holds(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.setattr(smoke_test.time, "sleep", lambda s: None)

    code = main(["--api", API, "--web", WEB], client=client_for(full_server()))

    out = capsys.readouterr().out
    assert code == 0 and "all smoke checks passed" in out and "upload roundtrip" in out
    assert KEY not in out, "no credential is printed"


def test_main_fails_on_any_failed_check(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.setattr(smoke_test.time, "sleep", lambda s: None)
    broken = {**GOOD_HEALTH, "ocr_langs": ["eng"]}

    code = main(["--api", API, "--web", WEB], client=client_for(full_server(broken)))

    captured = capsys.readouterr()
    assert code == 1 and "FAIL health" in captured.err and KEY not in captured.out + captured.err


def test_main_reports_the_roundtrip_as_skipped_without_a_key(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.delenv("API_KEY", raising=False)

    code = main(["--api", API, "--web", WEB], client=client_for(full_server()))

    out = capsys.readouterr().out
    assert code == 0 and "skip upload roundtrip" in out and "round trip skipped" in out


def test_main_survives_a_network_error(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.delenv("API_KEY", raising=False)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    code = main(["--api", API, "--web", WEB], client=client_for(refuse))

    assert code == 1 and "FAIL health" in capsys.readouterr().err
    assert json.dumps({}) == "{}"
