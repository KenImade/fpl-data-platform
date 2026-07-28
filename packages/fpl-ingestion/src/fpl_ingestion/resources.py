"""Dagster resources, and their environment-based equivalents for scripts.

Two ways to get the same things, deliberately kept separate:

  StoreResource / PostgresResource
      For Dagster code. Config is declared, so a missing environment variable
      fails at code-location load — before any sensor ticks or run is
      launched. Reading os.environ inside a sensor instead turns missing
      config into an error every 60 seconds while nothing captures: loud, but
      still no data.

  store_from_env() / postgres_from_env()
      For scripts, diagnostics and tests, which run outside a Dagster run
      where EnvVar cannot resolve.

The duplication between them is intentional. Having the resources delegate to
the *_from_env functions would mean they ignore their own declared config and
read the environment directly, which defeats the point and makes production
fail differently to local.
"""

from __future__ import annotations

import os
from pathlib import Path

import boto3
from dagster import ConfigurableResource, EnvVar

from fpl_ingestion.storage import LocalStore, S3Store, Store

LOCAL_CAPTURE_DIR = Path("local-capture")


def store_from_env() -> Store:
    """Build a Store from the environment. For scripts and one-off work."""
    if os.environ.get("DRY_RUN"):
        return LocalStore(LOCAL_CAPTURE_DIR)
    return S3Store(
        boto3.client(
            "s3",
            endpoint_url=os.environ["S3_ENDPOINT_URL"],
            aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        ),
        os.environ["S3_BUCKET"],
    )


class StoreResource(ConfigurableResource):
    """Object storage for raw and bronze."""

    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str

    def build(self) -> Store:
        # DRY_RUN stays a runtime branch rather than a construction-time one,
        # so `just dev` works without R2 credentials.
        if os.environ.get("DRY_RUN"):
            return LocalStore(LOCAL_CAPTURE_DIR)
        return S3Store(
            boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
            ),
            self.bucket,
        )


def postgres_from_env() -> str:
    """Warehouse connection string. For scripts and tests.

    Distinct from the Postgres backing Dagster's own run storage, which is
    configured separately in dagster.yaml via PGHOST/PGUSER/etc. Locally they
    are different databases on the same server; in production they may be
    different servers entirely.
    """
    return os.environ["DATABASE_URL"]


class PostgresResource(ConfigurableResource):
    """The analytical warehouse — bronze, and later the dbt-owned schemas.

    Exposes a connection string rather than a connection. Loads are batch
    operations opening a connection for their duration, and a long-lived
    pooled connection held across a Dagster run would only add a thing to
    time out.
    """

    url: str

    def connection_string(self) -> str:
        return self.url


STORE = StoreResource(
    endpoint_url=EnvVar("S3_ENDPOINT_URL"),
    access_key_id=EnvVar("S3_ACCESS_KEY_ID"),
    secret_access_key=EnvVar("S3_SECRET_ACCESS_KEY"),
    bucket=EnvVar("S3_BUCKET"),
)

POSTGRES = PostgresResource(url=EnvVar("DATABASE_URL"))
