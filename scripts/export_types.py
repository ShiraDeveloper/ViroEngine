"""Export TypeScript types from docs/openapi.json.

Run: python scripts/export_types.py [--out frontend/src/api/types.ts]

Used by CI to verify that frontend/src/api/types.ts is in sync with the API.
Whenever backend/core/models.py or any API route changes, regenerate:
    python scripts/export_openapi.py
    python scripts/export_types.py --out frontend/src/api/types.ts
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPO_ROOT / "docs" / "openapi.json"

_HEADER = """\
// AUTO-GENERATED — do not edit manually.
// Run: python scripts/export_openapi.py && python scripts/export_types.py
// Regenerate whenever the API schema changes; CI enforces this.

/* eslint-disable */
/* tslint:disable */
"""


def _resolve_ref(ref: str) -> str:
    """'#/components/schemas/Foo' → 'Foo'."""
    return ref.split("/")[-1]


def _schema_to_ts(schema: dict[str, Any]) -> str:
    """Convert a JSON Schema fragment to a TypeScript type expression.

    Handles $ref, anyOf, oneOf, allOf, enum, and the scalar types.
    Never raises; unknown shapes fall back to 'unknown'.
    """
    if "$ref" in schema:
        return _resolve_ref(schema["$ref"])

    if "anyOf" in schema:
        parts = [_schema_to_ts(s) for s in schema["anyOf"]]
        return " | ".join(parts)

    if "oneOf" in schema:
        parts = [_schema_to_ts(s) for s in schema["oneOf"]]
        return " | ".join(parts)

    if "allOf" in schema:
        parts = [_schema_to_ts(s) for s in schema["allOf"]]
        return " & ".join(parts)

    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])

    t = schema.get("type", "unknown")

    if t == "string":
        return "File" if schema.get("format") == "binary" else "string"
    if t in ("integer", "number"):
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        items = schema.get("items", {})
        inner = _schema_to_ts(items) if items else "unknown"
        return f"Array<{inner}>"
    if t == "object":
        props = schema.get("properties")
        if not props:
            additional = schema.get("additionalProperties")
            if isinstance(additional, dict):
                return f"Record<string, {_schema_to_ts(additional)}>"
            return "Record<string, unknown>"
        required = set(schema.get("required", []))
        # Inline anonymous object — single line
        fields = "; ".join(
            f"{n}{'?' if n not in required else ''}: {_schema_to_ts(ps)}" for n, ps in props.items()
        )
        return f"{{ {fields} }}"

    return "unknown"


def _render_type(name: str, schema: dict[str, Any]) -> str:
    """Render a named OpenAPI schema as a TypeScript interface or type alias."""
    t = schema.get("type", "object")

    if t == "object":
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        lines = [f"export interface {name} {{"]
        for prop_name, prop_schema in props.items():
            opt = "?" if prop_name not in required else ""
            ts = _schema_to_ts(prop_schema)
            lines.append(f"  {prop_name}{opt}: {ts};")
        lines.append("}")
        return "\n".join(lines)

    # Enum string, union, scalar → type alias
    return f"export type {name} = {_schema_to_ts(schema)};"


def generate(openapi: dict[str, Any]) -> str:
    """Return the full TypeScript file content for the given OpenAPI document."""
    schemas = openapi.get("components", {}).get("schemas", {})

    chunks: list[str] = [_HEADER]
    for name, schema in schemas.items():
        chunks.append(_render_type(name, schema))
        chunks.append("")  # blank line between definitions

    return "\n".join(chunks)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — exit 0 on success, 1 on error."""
    parser = argparse.ArgumentParser(description="Generate TypeScript types from docs/openapi.json")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "frontend" / "src" / "api" / "types.ts",
        help="Output file path (default: frontend/src/api/types.ts)",
    )
    args = parser.parse_args(argv)

    if not OPENAPI_PATH.exists():
        print(
            f"::error::openapi.json not found at {OPENAPI_PATH}. "
            "Run scripts/export_openapi.py first.",
            file=sys.stderr,
        )
        return 1

    openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    content = generate(openapi)

    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")

    schema_count = len(openapi.get("components", {}).get("schemas", {}))
    print(f"Written {out}  —  {schema_count} schema(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
