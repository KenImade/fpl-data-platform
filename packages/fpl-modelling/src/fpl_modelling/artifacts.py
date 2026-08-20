"""Versioning and object storage, shared by every model in the package.

Extracted from train.py, which had grown minutes-specific plumbing around a
scheme that was never minutes-specific. Nothing here knows what a model is; it
knows how to name one, how to say what it saw, and how to put it somewhere it
can be found again.

WHAT A VERSION IS. A model version names three things together: the code that
fitted it, the data it saw, and the features it saw. Change any one and the
predictions change, so all three go into the hash. The alternative — a
monotonic counter, or a timestamp — tells you a model is different without
telling you how, which is exactly the information you want when a prediction
looks wrong.

WHAT IS PERSISTED. One gzipped tar per version: the model's own serialised
form, whatever shape that takes, plus a manifest. They are only meaningful
together, and a partial read returning a model without its feature list is
worse than a failed read.

BYTES, NOT PATHS. save takes a mapping of member name to bytes and load returns
the same. Callers do their own serialisation — a LightGBM booster goes through
model_to_string, an sklearn estimator through pickle — which keeps temporary
directories out of this module entirely and means neither side has a file
lifetime to reason about.

THE ARTIFACT DOES NOT GO IN POSTGRES. Model files are megabytes and immutable;
object storage handles that and the database should not. What goes in Postgres
is the version string on every prediction row, which is the pointer.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tarfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from fpl_modelling.data import as_int

log = logging.getLogger(__name__)

ARTIFACT_ROOT = "models"
MANIFEST_MEMBER = "manifest.json"


@dataclass(frozen=True)
class Manifest:
    """Everything needed to say what a model is.

    Written alongside the model and read back at prediction time. The feature
    list in particular is load-bearing: feat_player_form is generated from a
    Jinja loop, so adding a window silently widens the matrix. A model scored
    against a wider frame than it was fitted on must fail rather than quietly
    reorder columns.

    ``model_name`` is both the artifact prefix and the version prefix, so a
    version string is self-describing and the storage layout follows from it.

    ``extras`` carries whatever a particular model needs to be rebuilt that
    every model does not — LightGBM's per-stage best iteration counts, a GLM's
    chosen regularisation strength. Typed loosely on purpose: the alternative
    is a union of every model's bookkeeping in one dataclass, which grows
    monotonically and is wrong for every model but one.

    ``data_version`` is carried explicitly as well as being embedded in
    ``model_version``, so comparing two artifacts does not require parsing a
    string.
    """

    model_name: str
    model_version: str
    trained_at: str
    seasons: list[str]
    train_rows: int
    train_gameweek_min: int
    train_gameweek_max: int
    features: list[str]
    feature_count: int
    params: dict[str, Any]
    metrics: dict[str, float]
    code_version: str
    data_version: str
    extras: dict[str, Any] = field(default_factory=dict)


def code_version(filenames: Sequence[str]) -> str:
    """Hash of the source files that define a model.

    Not the git SHA — the working tree may be dirty, and a model fitted from
    uncommitted code is exactly the one you most need to identify. Hashing the
    files that define the model catches that; a commit hash does not.

    Filenames are hashed alongside their contents, so two files swapping
    contents produces a different version rather than the same one.

    A missing file raises. The previous behaviour skipped it silently, which
    meant a typo in the filename list produced a perfectly stable hash that
    ignored the model source entirely — a version string that cannot detect
    the change it exists to detect.
    """
    here = Path(__file__).parent
    h = hashlib.sha256()
    for name in sorted(filenames):
        path = here / name
        if not path.is_file():
            raise FileNotFoundError(
                f"cannot version {name!r}: no such file in {here}. "
                "A version that silently omits its own source is worse than none."
            )
        h.update(name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def data_version(df: pl.DataFrame, features: Sequence[str]) -> str:
    """Hash of what the model saw.

    Row count, season and gameweek bounds, and the feature list. Not the data
    itself — hashing the matrix on every run is wasteful, and the warehouse is
    rebuilt deterministically from immutable inputs anyway, so the bounds
    identify it.

    The feature list is included because a model fitted on the same rows with a
    different feature set is a different model, and the row bounds alone would
    call them identical.
    """
    payload = json.dumps(
        {
            "rows": len(df),
            "seasons": sorted(df["season"].unique().to_list()),
            "gw_min": as_int(df["gameweek"].min()),
            "gw_max": as_int(df["gameweek"].max()),
            "features": sorted(features),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def make_version(model_name: str, code: str, data: str) -> str:
    """`<name>-<code>-<data>`. Readable, sortable enough, and diagnostic.

    Given two versions you can see at a glance whether the code changed, the
    data changed, or both — which is the first question when predictions move.
    """
    return f"{model_name}-{code}-{data}"


def _store() -> Any:
    """Object storage client.

    S3-compatible, so MinIO locally and whatever the deployment uses in
    production without a code change. Imported lazily because the training path
    is useful without it — evaluation and inspection need no storage at all.
    """
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY", "minioadmin"),
    )


def _bucket() -> str:
    return os.environ.get("MODEL_BUCKET", "fpl-models")


def key_for(model_name: str, model_version: str) -> str:
    return f"{ARTIFACT_ROOT}/{model_name}/{model_version}.tar.gz"


def save(manifest: Manifest, members: dict[str, bytes]) -> str:
    """Write an artifact to object storage, return its key.

    Members are serialised model state, keyed by the name they will carry
    inside the tarball. The manifest is added here rather than by the caller,
    so no model can ship without one.

    Tar entries carry mtime 0 and are written in sorted order, which makes the
    archive byte-identical for identical inputs. That is worth having: an
    artifact whose bytes change on every run cannot be compared, deduplicated,
    or checked for accidental retraining.
    """
    if not members:
        raise ValueError("refusing to save an artifact with no model in it")
    if MANIFEST_MEMBER in members:
        raise ValueError(f"{MANIFEST_MEMBER} is written by this module, not by the caller")

    payload = dict(members)
    payload[MANIFEST_MEMBER] = json.dumps(asdict(manifest), indent=2, sort_keys=True).encode()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, blob in sorted(payload.items()):
            info = tarfile.TarInfo(name=name)
            info.size = len(blob)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(blob))

    key = key_for(manifest.model_name, manifest.model_version)
    _store().put_object(Bucket=_bucket(), Key=key, Body=buf.getvalue())

    log.info("saved %s (%d features, %d members)", key, manifest.feature_count, len(members))
    return key


def load(model_name: str, model_version: str) -> tuple[dict[str, bytes], Manifest]:
    """Read an artifact back, as raw members plus its manifest.

    The prediction path calls this rather than refitting. A prediction that
    retrains is not reproducible and is also slow at exactly the moment it
    needs not to be.

    Manifest parsing is strict: an artifact written by an older manifest shape
    raises rather than being coerced. Silently filling a missing field with a
    default would produce a model that reports features or metrics it does not
    have, which is the one thing this file exists to prevent.
    """
    key = key_for(model_name, model_version)
    obj = _store().get_object(Bucket=_bucket(), Key=key)
    buf = io.BytesIO(obj["Body"].read())

    members: dict[str, bytes] = {}
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is not None:
                members[member.name] = handle.read()

    raw = members.pop(MANIFEST_MEMBER, None)
    if raw is None:
        raise ValueError(f"{key} contains no {MANIFEST_MEMBER}")

    try:
        manifest = Manifest(**json.loads(raw))
    except TypeError as exc:
        raise ValueError(
            f"{key}: manifest does not match the current Manifest shape ({exc}). "
            "Artifacts written before a manifest change must be retrained, not migrated."
        ) from exc

    log.info("loaded %s trained %s", model_version, manifest.trained_at)
    return members, manifest
