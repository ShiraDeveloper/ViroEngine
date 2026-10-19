"""Export the FastAPI OpenAPI schema to docs/openapi.json (or a given path).

Run:
    python scripts/export_openapi.py                          # writes docs/openapi.json
    python scripts/export_openapi.py --out /tmp/openapi.json  # writes to a custom path

CI uses the --out form to write to a temp file, then diffs against docs/openapi.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import create_app

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUT = _REPO_ROOT / "docs" / "openapi.json"


def main(argv: list[str] | None = None) -> int:
    """Generate the OpenAPI schema and write it to the output path."""
    parser = argparse.ArgumentParser(description="Export the FastAPI OpenAPI schema to JSON.")
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"Output file path (default: {_DEFAULT_OUT})",
    )
    args = parser.parse_args(argv)

    app = create_app(use_fakes=True)
    schema = app.openapi()

    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

    paths_count = len(schema.get("paths", {}))
    schemas_count = len(schema.get("components", {}).get("schemas", {}))
    print(f"Written {out}  --  {paths_count} paths, {schemas_count} schemas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
