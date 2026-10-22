"""Doubles and builders for the provider-layer tests (T18). Nothing here touches the network.

`FakeClock` is shared by the breaker, the buckets and the credential ring so time moves only when
a test says so (or when the code under test sleeps). `FakeTransport` plays the three providers by
URL and records what was sent, so a test can assert on the payload without any socket.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import json
from typing import Any

from backend.adapters.vision.transport import HttpRequest, HttpResponse, TransportError
from backend.core.errors import ProviderExhausted
from backend.core.models import (
    CropPayload,
    FieldKey,
    ProviderHealth,
    ReaderName,
    RegionKind,
    VisionAnswer,
    VisionRequest,
    VisionResponse,
)
from backend.core.vision_schema import EXTRACTION_SCHEMA

GEMINI_HOST = "generativelanguage.googleapis.com"
OPENROUTER_HOST = "openrouter.ai"
GROQ_HOST = "api.groq.com"

PNG = b"\x89PNG\r\n\x1a\nfake-crop-bytes"


class FakeClock:
    """Monotonic seconds that advance only on `advance` or `sleep`."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def ok(status: int = 200, body: dict[str, Any] | None = None) -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps(body or {}).encode("utf-8"))


def status(code: int) -> HttpResponse:
    return HttpResponse(status=code, body=b"{}")


def answer_json(**fields: str | list[str] | None) -> str:
    """A schema-valid extraction answer. A list value means a multi-value field."""
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, list):
            out[key] = {"values": value, "source": "handwritten", "alts": []}
        else:
            out[key] = {"value": value, "source": "handwritten", "alts": []}
    return json.dumps({"fields": out, "ambiguities": []}, ensure_ascii=False)


def gemini_body(text: str, *, tokens: tuple[int, int] = (120, 30)) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"promptTokenCount": tokens[0], "candidatesTokenCount": tokens[1]},
    }


def chat_body(text: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


@dataclass
class FakeTransport:
    """Scripted answers per provider host. Each script is consumed in order; the last item
    repeats. An Exception item is raised. `sent` records (host, header names, body)."""

    scripts: dict[str, list[HttpResponse | Exception]] = field(default_factory=dict)
    sent: list[tuple[str, dict[str, str], dict[str, Any]]] = field(default_factory=list)
    used: dict[str, int] = field(default_factory=dict)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        host = next((h for h in (GEMINI_HOST, OPENROUTER_HOST, GROQ_HOST) if h in request.url), "?")
        self.sent.append((host, dict(request.headers), json.loads(request.body)))
        script = self.scripts.get(host, [status(503)])
        index = self.used.get(host, 0)
        self.used[host] = index + 1
        item = script[min(index, len(script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    def calls(self, host: str) -> int:
        return self.used.get(host, 0)

    @property
    def total(self) -> int:
        return len(self.sent)

    def keys_used(self, host: str) -> list[str]:
        """The credential presented on each call to `host` (test-only introspection)."""
        out: list[str] = []
        for h, headers, _ in self.sent:
            if h != host:
                continue
            key = headers.get("x-goog-api-key") or headers.get("Authorization", "")
            out.append(key.removeprefix("Bearer "))
        return out


def all_down() -> FakeTransport:
    """Every provider answers 503."""
    down: list[HttpResponse | Exception] = [status(503)]
    return FakeTransport({GEMINI_HOST: down, OPENROUTER_HOST: down, GROQ_HOST: down})


def timeout_error() -> TransportError:
    return TransportError("ReadTimeout")


def fields_request(
    *fields: FieldKey,
    task: str = "fields",
    png: bytes = PNG,
    prompt_version: str = "v1",
) -> VisionRequest:
    """A request with one crop per field (never a clinical one)."""
    crops = [
        CropPayload(
            field=f,
            png=png + f.value.encode("ascii"),
            region_kind=RegionKind.patient_box,
            box=(0.2, 0.1, 0.3, 0.5),
        )
        for f in fields
    ]
    return VisionRequest(
        job_id="job-1",
        page_id="page#1",
        task=task,  # type: ignore[arg-type]
        crops=crops,
        prompt_version=prompt_version,
        response_schema=EXTRACTION_SCHEMA,
    )


@dataclass
class ScriptedVision:
    """VisionLLMPort returning scripted answers; raises ProviderExhausted when `down`."""

    answers: dict[FieldKey, VisionAnswer] = field(default_factory=dict)
    down: bool = False
    cached: bool = False
    requests: list[VisionRequest] = field(default_factory=list)
    name: str = "scripted-vision"

    def extract(self, request: VisionRequest) -> VisionResponse:
        self.requests.append(request)
        if self.down:
            raise ProviderExhausted("scripted outage", failovers=("gemini:unavailable",))
        wanted = {c.field for c in request.crops}
        return VisionResponse(
            raw_text="",
            candidates=[],
            provider=ReaderName.gemini,
            model_id="scripted",
            prompt_version=request.prompt_version,
            duration_ms=5,
            answers={k: v for k, v in self.answers.items() if k in wanted},
            input_tokens=0 if self.cached else 100,
            output_tokens=0 if self.cached else 20,
            cache_hit=self.cached,
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, credentials=1, state="ok")


def answer(*values: str, source: str = "handwritten") -> VisionAnswer:
    from backend.core.models import SourceKind

    return VisionAnswer(values=list(values), source=SourceKind(source))


def hosts(sent: Sequence[tuple[str, dict[str, str], dict[str, Any]]]) -> list[str]:
    return [h for h, _, _ in sent]
