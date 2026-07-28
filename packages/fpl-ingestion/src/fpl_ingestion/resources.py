import os
from pathlib import Path

import boto3

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
