"""Draw assets/architecture.png - the hexagonal diagram of docs/ARCHITECTURE.md section 2.

Run: python -m tools.make_architecture_png [--out assets/architecture.png]
Uses Pillow's built-in font so the output is the same on every machine. Exit 0 on success.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "assets" / "architecture.png"

WIDTH, HEIGHT = 1600, 980  # px; the README shows it at 1200 px wide, so text stays legible
BACKGROUND = (255, 255, 255)
INK = (28, 34, 48)
MUTED = (96, 106, 124)
ARROW = (70, 80, 100)
# (fill, border) per column
CLIENT = ((232, 240, 254), (66, 110, 190))
API = ((236, 244, 232), (74, 140, 70))
CORE = ((255, 244, 224), (200, 130, 20))
PORT = ((246, 236, 252), (130, 70, 170))
ADAPTER = ((238, 238, 238), (110, 110, 110))

CLIENTS = ["React UI (Vercel)", "Future: lab SAP / LIMS", "tools/ and eval/ (CLI)"]
API_BOXES = [
    "Auth: X-API-Key + X-Access-Code",
    "Rate limit, body cap, headers",
    "Routers: documents, batches, jobs,\nreview, export, stress, metrics",
    "Job queue (asyncio, bounded)",
]
CORE_BOXES = [
    "Validators: 2 hard rules,\nthe rest only adjust the score",
    "Combined score + A-D ladder",
    "Grouping + row assembly",
    "Pipeline S0-S11",
]
PORTS = [
    ("OcrPort", "Tesseract heb+eng;\nTrOCR digits"),
    ("BarcodePort", "zxing-cpp, any symbology"),
    ("RegionDetectorPort", "rule detector;\nYOLOv8n only if it won"),
    ("VisionLLMPort", "Gemini, OpenRouter, Groq"),
    ("StoragePort", "temp dir + TTL purge"),
    ("ExporterPort", "xlsx, csv, json"),
    ("LearningStorePort", "SQLite: locations\nand shapes only"),
    ("ClockPort", "system clock"),
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    text: str,
    colours: tuple[tuple[int, int, int], tuple[int, int, int]],
    *,
    size: int = 20,
) -> None:
    draw.rounded_rectangle(xy, radius=10, fill=colours[0], outline=colours[1], width=3)
    x0, y0, x1, y1 = xy
    draw.multiline_text(
        ((x0 + x1) / 2, (y0 + y1) / 2),
        text,
        font=_font(size),
        fill=INK,
        anchor="mm",
        align="center",
        spacing=4,
    )


def _arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int]) -> None:
    draw.line([start, end], fill=ARROW, width=4)
    ex, ey = end
    direction = 1 if end[0] >= start[0] else -1
    draw.polygon(
        [(ex, ey), (ex - 16 * direction, ey - 9), (ex - 16 * direction, ey + 9)], fill=ARROW
    )


def _column(
    draw: ImageDraw.ImageDraw,
    x0: int,
    x1: int,
    title: str,
    items: list[str],
    colours: tuple[tuple[int, int, int], tuple[int, int, int]],
    top: int = 120,
    bottom: int = 900,
) -> list[tuple[int, int]]:
    """Draw a titled column of equal boxes; return the right-edge midpoint of each box."""
    draw.text(((x0 + x1) / 2, top - 40), title, font=_font(26), fill=INK, anchor="mm")
    gap = 24
    height = (bottom - top - gap * (len(items) - 1)) // len(items)
    anchors: list[tuple[int, int]] = []
    for index, text in enumerate(items):
        y0 = top + index * (height + gap)
        _box(draw, (x0, y0, x1, y0 + height), text, colours, size=18)
        anchors.append((x1, y0 + height // 2))
    return anchors


def render() -> Image.Image:
    """Return the diagram as an RGB image."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text(
        (WIDTH / 2, 36),
        "Safe Line - hexagonal architecture: core depends on ports, never on adapters",
        font=_font(30),
        fill=INK,
        anchor="mm",
    )

    _column(draw, 20, 250, "Clients", CLIENTS, CLIENT, top=120, bottom=560)
    api = _column(draw, 330, 640, "API (FastAPI /v1)", API_BOXES, API)
    core = _column(draw, 720, 970, "Domain core", CORE_BOXES, CORE)

    # Ports and adapters share rows: the port box and the adapter that implements it.
    draw.text((1290, 80), "Ports  ->  Adapters", font=_font(26), fill=INK, anchor="mm")
    row_gap = 12
    row_h = (900 - 120 - row_gap * (len(PORTS) - 1)) // len(PORTS)
    port_anchors: list[tuple[int, int]] = []
    for index, (port, adapter) in enumerate(PORTS):
        y0 = 120 + index * (row_h + row_gap)
        _box(draw, (1060, y0, 1260, y0 + row_h), port, PORT, size=18)
        _box(draw, (1300, y0, 1580, y0 + row_h), adapter, ADAPTER, size=16)
        _arrow(draw, (1260, y0 + row_h // 2), (1300, y0 + row_h // 2))
        port_anchors.append((1060, y0 + row_h // 2))

    # Flow arrows between columns, all horizontal.
    _arrow(draw, (250, api[0][1]), (330, api[0][1]))
    _arrow(draw, (640, api[3][1]), (720, core[3][1]))
    # Bus from the pipeline to every port.
    bus_x = 1015
    draw.line([(970, core[3][1]), (bus_x, core[3][1])], fill=ARROW, width=4)
    draw.line([(bus_x, port_anchors[0][1]), (bus_x, port_anchors[-1][1])], fill=ARROW, width=4)
    for _, y in port_anchors:
        _arrow(draw, (bus_x, y), (1060, y))
    draw.text(
        (WIDTH / 2, 940),
        "scripts/check_layering.py fails the build if backend/core imports an adapter or a vendor SDK."
        "  backend/container.py is the only module that constructs adapters.",
        font=_font(18),
        fill=MUTED,
        anchor="mm",
    )
    return image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    render().save(args.out, format="PNG", optimize=True)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
