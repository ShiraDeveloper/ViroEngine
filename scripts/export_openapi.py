"""Export the FastAPI OpenAPI schema to docs/openapi.json.

Run: python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import create_app  # noqa: E402

app = create_app(use_fakes=True)
schema = app.openapi()

out = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
out.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

paths_count = len(schema.get("paths", {}))
schemas_count = len(schema.get("components", {}).get("schemas", {}))
print(f"Written {out}  --  {paths_count} paths, {schemas_count} schemas")
