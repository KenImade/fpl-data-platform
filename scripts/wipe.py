"""Reset the data platform to empty.

Development only. Removes:

  - every object in the bucket (raw captures, bronze parquet, state)
  - the `bronze` schema in the warehouse
  - Dagster's own database: run history, materialisations, sensor cursors
  - local scratch output

Run from the repo root:

    uv run python scripts/wipe.py              # prompts
    uv run python scripts/wipe.py --yes        # doesn't
    uv run python scripts/wipe.py --derived    # keeps raw/

Note on cursors: dropping the Dagster database resets the capture sensor, so
the next tick captures immediately rather than waiting out its interval.
Harmless, but it explains the unexpected run afterwards.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fpl_ingestion.resources import store_from_env

SCRATCH_GLOBS = ("scratch/*.parquet", "scratch/reconcile*.parquet")
LOCAL_CAPTURE = Path("local-capture")


def wipe_objects(derived_only: bool) -> None:
    store = store_from_env()

    if derived_only:
        for prefix in ("bronze/",):
            n = store.delete_prefix(prefix)
            print(f"  objects  {prefix:<12} {n:>6} deleted")
        return

    n = store.delete_prefix("")
    print(f"  objects  {'(all)':<12} {n:>6} deleted")


def psql(database: str, sql: str) -> None:
    """Run SQL against the local Compose Postgres."""
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "fpl",
            "-d",
            database,
            "-c",
            sql,
        ],
        check=True,
        capture_output=True,
    )


def wipe_warehouse() -> None:
    psql("fpl", "DROP SCHEMA IF EXISTS bronze CASCADE")
    print("  warehouse  bronze schema dropped")


def wipe_dagster() -> None:
    # Terminate stragglers first: DROP DATABASE fails while the daemon or
    # webserver holds a connection.
    psql(
        "postgres",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = 'dagster' AND pid <> pg_backend_pid()",
    )
    psql("postgres", "DROP DATABASE IF EXISTS dagster")
    psql("postgres", "CREATE DATABASE dagster")
    print("  dagster    database recreated (run history and cursors reset)")


def wipe_local() -> None:
    if LOCAL_CAPTURE.exists():
        shutil.rmtree(LOCAL_CAPTURE)
        print(f"  local      {LOCAL_CAPTURE}/ removed")

    removed = 0
    for pattern in SCRATCH_GLOBS:
        for path in Path().glob(pattern):
            path.unlink()
            removed += 1
    if removed:
        print(f"  local      {removed} scratch parquet removed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="skip confirmation")
    ap.add_argument(
        "--derived",
        action="store_true",
        help="keep raw/ captures; wipe bronze, warehouse and dagster only",
    )
    args = ap.parse_args()

    bucket = os.environ.get("S3_BUCKET", "?")
    scope = "DERIVED DATA" if args.derived else "EVERYTHING (including raw captures)"

    print(f"\nWipe {scope}")
    print(f"  bucket:   {bucket}")
    print(f"  endpoint: {os.environ.get('S3_ENDPOINT_URL', '?')}\n")

    if not args.yes:
        if input("type 'wipe' to continue: ").strip() != "wipe":
            print("aborted")
            return 1
        print()

    wipe_objects(derived_only=args.derived)
    wipe_warehouse()
    wipe_dagster()
    if not args.derived:
        wipe_local()

    print("\ndone. `just dev` will start from empty.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
