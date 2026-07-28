from __future__ import annotations

import io
import json
from datetime import date, datetime

import polars as pl

from fpl_ingestion.schemas import Element, polars_schema
from fpl_ingestion.storage import Store


def list_captures(store: Store, endpoint: str, day: date) -> list[str]:
    """Every raw key for one endpoint on one UTC day, oldest first"""
    prefix = f"raw/fpl/{endpoint}/{day:%Y-%m-%d}/"
    return sorted(store.list(prefix))


def parse_capture_time(key: str) -> datetime:
    """raw/fpl/x/2026-07-27/23-37-20Z.json.gz -> aware datetime."""
    day, filename = key.rstrip("/").split("/")[-2:]
    clock = filename.removesuffix(".json.gz").removesuffix("Z")
    return datetime.fromisoformat(f"{day}T{clock.replace("-", ":")}+00:00")


def build_bootstrap_bronze(store: Store, day: date) -> dict[str, int]:
    rows = []
    for key in list_captures(store, "bootstrap-static", day):
        payload = json.loads(store.get(key))
        captured_at = parse_capture_time(key)
        for element in payload["elements"]:
            rows.append({"captured_at": captured_at, **element})

    if not rows:
        raise ValueError(f"no captures for {day}")

    buf = io.BytesIO()
    schema = {"captured_at": pl.Datetime("us", "UTC"), **polars_schema(Element)}
    df = pl.DataFrame(rows, schema=schema)
    df.write_parquet(buf)
    store.put(
        f"bronze/players/{day:%Y-%m-%d}.parquet",
        buf.getvalue(),
        overwrite=True,
        compress=False,
    )
    return {"rows": len(rows), "captures": df["captured_at"].n_unique()}
