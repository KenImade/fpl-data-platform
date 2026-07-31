"""Export the API OpenAPI schema for the documentation site.

The API application is the source of truth. This exports the same schema
FastAPI exposes at /openapi.json without requiring a running server.

Run from the repo root:
    uv run python scripts/export_openapi.py

Writes:
    docs/src/openapi.json
"""

from __future__ import annotations

import json
from pathlib import Path

from fpl_api.main import app

OUT = Path("docs/src/openapi.json")


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    schema = app.openapi()

    assert schema["paths"], "OpenAPI export contains no paths"
    assert schema["components"]["schemas"], "OpenAPI export contains no schemas"

    OUT.write_text(
        json.dumps(
            schema,
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )

    print(f"wrote {OUT}")
    print(
        f"{len(schema.get('paths', {}))} paths, "
        f"{len(schema.get('components', {}).get('schemas', {}))} schemas"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
