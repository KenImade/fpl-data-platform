from __future__ import annotations

import gzip
import logging
from datetime import datetime
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

LATEST_BOOTSTRAP = "state/latest-bootstrap.json.gz"


class ObjectExists(RuntimeError):
    """Refusing to overwrite. Raw captures are immutable."""


class ObjectMissing(RuntimeError):
    """Key not found."""


def raw_key(endpoint: str, at: datetime) -> str:
    """raw/fpl/{endpoint}/{YYYY-MM-DD}/{HH-MM-SS}Z.json.gz"""
    if at.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware UTC")
    return f"raw/fpl/{endpoint}/{at:%Y-%m-%d}/{at:%H-%M-%S}Z.json.gz"


class Store(Protocol):
    def put(self, key: str, body: bytes, *, overwrite: bool = False) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


class LocalStore:
    """Filesystem-backed. For tests and DRY_RUN."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, body: bytes, *, overwrite: bool = False) -> None:
        path = self._path(key)
        if path.exists() and not overwrite:
            raise ObjectExists(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(body))
        log.info("stored key=%s bytes=%d", key, len(body))

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise ObjectMissing(key)
        return gzip.decompress(path.read_bytes())

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Store:
    """S3-compatible (Cloudflare R2)."""

    def __init__(self, client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def put(self, key: str, body: bytes, *, overwrite: bool = False) -> None:
        if not overwrite and self.exists(key):
            raise ObjectExists(key)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=gzip.compress(body))
        log.info("stored key=%s bytes=%d", key, len(body))

    def get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise ObjectMissing(key) from exc
            raise
        return gzip.decompress(obj["Body"].read())

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "NotFound", "404"):
                return False
            raise
        return True
