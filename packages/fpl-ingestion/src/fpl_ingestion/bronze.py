from __future__ import annotations

import io
import json
import logging
from datetime import date, datetime

import polars as pl

from fpl_ingestion.schemas import Element, novel_fields, polars_schema
from fpl_ingestion.storage import Store

log = logging.getLogger(__name__)

BOOTSTRAP_SCHEMA = {"captured_at": pl.Datetime("us", "UTC"), **polars_schema(Element)}
ELEMENT_FIELDS = set(Element.model_fields)


def list_captures(store: Store, endpoint: str, day: date) -> list[str]:
    """Every raw key for one endpoint on one UTC day, oldest first."""
    prefix = f"raw/fpl/{endpoint}/{day:%Y-%m-%d}/"
    return sorted(store.list(prefix))


def parse_capture_time(key: str) -> datetime:
    """raw/fpl/x/2026-07-27/23-37-20Z.json.gz -> aware datetime."""
    day, filename = key.rstrip("/").split("/")[-2:]
    clock = filename.removesuffix(".json.gz").removesuffix("Z")
    return datetime.fromisoformat(f"{day}T{clock.replace('-', ':')}+00:00")


def build_bootstrap_bronze(store: Store, day: date) -> dict[str, int]:
    """Flatten every capture for one UTC day into a parquet partition.

    Every intra-day observation is preserved, stamped with the capture it came
    from. Capturing eight times a day exists to record price and injury-news
    movement, so collapsing to one row per player would discard the point of it.

    Validation raises on a missing or retyped declared field; unknown fields
    are kept in the model but dropped by the explicit schema, and reported so
    a new FPL field gets modelled deliberately rather than found months later.
    """
    keys = list_captures(store, "bootstrap-static", day)
    rows: list[dict] = []
    novel: set[str] = set()

    for key in keys:
        payload = json.loads(store.get(key))
        captured_at = parse_capture_time(key)
        for element in payload["elements"]:
            validated = Element.model_validate(element)
            novel |= novel_fields(validated)
            rows.append(
                {
                    "captured_at": captured_at,
                    **validated.model_dump(include=ELEMENT_FIELDS),
                }
            )

    if not rows:
        raise ValueError(f"no captures for {day}")

    if novel:
        log.warning("unmapped fields in bootstrap elements: %s", sorted(novel))

    buf = io.BytesIO()
    df = pl.DataFrame(rows, schema=BOOTSTRAP_SCHEMA)
    df.write_parquet(buf)
    store.put(
        f"bronze/players/{day:%Y-%m-%d}.parquet",
        buf.getvalue(),
        overwrite=True,
        compress=False,
    )

    return {
        "rows": len(rows),
        "captures": len(keys),
        "novel_fields": len(novel),
    }
