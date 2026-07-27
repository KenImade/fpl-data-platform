import json
from pathlib import Path

from fpl_ingestion.client import ENDPOINTS, fetch, make_client

OUT = Path("packages/fpl-ingestion/tests/cassettes")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with make_client("fpl-api-project (contact: you@example.com)") as c:
        for name, url in ENDPOINTS.items():
            data = json.loads(fetch(c, url).body)
            path = OUT / f"{name}.json"
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
            print(f"{name}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
