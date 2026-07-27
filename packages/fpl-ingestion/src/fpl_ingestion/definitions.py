import os
from datetime import UTC, datetime
from pathlib import Path

import boto3
from dagster import (
    Definitions,
    OpExecutionContext,
    RunRequest,
    SkipReason,
    job,
    op,
    sensor,
)

from fpl_ingestion.capture import capture
from fpl_ingestion.client import make_client
from fpl_ingestion.deadlines import read_deadlines
from fpl_ingestion.schedule import decide
from fpl_ingestion.storage import LocalStore, S3Store


def build_store():
    if os.environ.get("DRY_RUN"):
        return LocalStore(Path("local-capture"))
    return S3Store(
        boto3.client(
            "s3",
            endpoint_url=os.environ["S3_ENDPOINT_URL"],
            aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        ),
        os.environ["S3_BUCKET"],
    )


@op
def capture_op(context: OpExecutionContext) -> None:
    store = build_store()
    with make_client(os.environ["USER_AGENT"]) as client:
        result = capture(client, store)

    for name, key in result.stored.items():
        context.log.info("stored %s -> %s", name, key)

    if not result.ok:
        raise RuntimeError(f"capture incomplete: {result.failed}")


@job
def capture_job() -> None:
    capture_op()


@sensor(job=capture_job, minimum_interval_seconds=60)
def fpl_capture_sensor(context):
    store = build_store()
    now = datetime.now(UTC)

    last = datetime.fromisoformat(context.cursor) if context.cursor else None
    decision = decide(now, last, read_deadlines(store))

    if not decision.capture:
        return SkipReason(decision.reason)

    context.update_cursor(now.isoformat())
    return RunRequest(run_key=now.strftime("%Y%m%dT%H%M%S"))


defs = Definitions(jobs=[capture_job], sensors=[fpl_capture_sensor])
