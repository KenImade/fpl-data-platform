from __future__ import annotations

import gzip
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

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
    def put(
        self, key: str, body: bytes, *, overwrite: bool = False, compress: bool = True
    ) -> None: ...
    def get(self, key: str, *, decompress: bool = True) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...
    def delete(self, key: str) -> bool: ...
    def delete_prefix(self, prefix: str) -> int: ...


class LocalStore:
    """Filesystem-backed. For tests and DRY_RUN."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, body: bytes, *, overwrite: bool = False, compress: bool = True) -> None:
        path = self._path(key)
        if path.exists() and not overwrite:
            raise ObjectExists(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(body) if compress else body)
        log.info("stored key=%s bytes=%d", key, len(body))

    def get(self, key: str, *, decompress: bool = True) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise ObjectMissing(key)
        raw = path.read_bytes()
        return gzip.decompress(raw) if decompress else raw

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def list(self, prefix: str) -> list[str]:
        """Keys under prefix, sorted. Mirrors S3 semantics: string prefix
        matching over the whole key, not directory listing."""
        if not self.root.exists():
            return []
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.is_file() and str(p.relative_to(self.root)).startswith(prefix)
        )

    def delete(self, key: str) -> bool:
        """True if something was removed, False if the key was absent."""
        path = self._path(key)
        if not path.exists():
            return False
        path.unlink()
        return True

    def delete_prefix(self, prefix: str) -> int:
        """Delete every key under prefix. Returns the count removed.

        An empty prefix deletes everything — that is deliberate, and the
        caller is expected to mean it.
        """
        keys = self.list(prefix)
        for key in keys:
            self._path(key).unlink()

        # Tidy the directory tree left behind, deepest first.
        if self.root.exists():
            for path in sorted(self.root.rglob("*"), key=lambda p: -len(p.parts)):
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()

        log.info("deleted %d objects under %r", len(keys), prefix)
        return len(keys)


class S3Store:
    """S3-compatible (Cloudflare R2)."""

    DELETE_BATCH = 1000  # S3 API limit per DeleteObjects call

    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def put(self, key: str, body: bytes, *, overwrite: bool = False, compress: bool = True) -> None:
        if not overwrite and self.exists(key):
            raise ObjectExists(key)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=gzip.compress(body) if compress else body,
        )
        log.info("stored key=%s bytes=%d", key, len(body))

    def get(self, key: str, *, decompress: bool = True) -> bytes:
        from botocore.exceptions import ClientError

        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise ObjectMissing(key) from exc
            raise
        raw = obj["Body"].read()
        return gzip.decompress(raw) if decompress else raw

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "NotFound", "404"):
                return False
            raise
        return True

    def list(self, prefix: str) -> list[str]:
        """Paginated. list_objects_v2 truncates at 1000 keys and returns them
        lexicographically, so an unpaginated call silently loses everything
        after the first thousand — and loses the NEWEST keys, since ours sort
        by time."""
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(o["Key"] for o in page.get("Contents", []))
        return sorted(keys)

    def delete(self, key: str) -> bool:
        """True if something was removed, False if the key was absent.

        S3 DeleteObject succeeds on a missing key, so existence is checked
        first to make the return value meaningful.
        """
        if not self.exists(key):
            return False
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def delete_prefix(self, prefix: str) -> int:
        """Delete every key under prefix, in batches. Returns the count.

        An empty prefix deletes everything in the bucket — that is
        deliberate, and the caller is expected to mean it.
        """
        keys = self.list(prefix)
        for i in range(0, len(keys), self.DELETE_BATCH):
            batch = keys[i : i + self.DELETE_BATCH]
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
        log.info("deleted %d objects under %r", len(keys), prefix)
        return len(keys)
