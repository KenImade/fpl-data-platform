"""API key authentication.

Two key types, because a browser cannot keep a secret.

A publishable key ships inside a JavaScript bundle where anyone can read it.
Treating it as a credential would be pretending otherwise, so it is instead
constrained: read-only data endpoints, an origin allowlist, and a modest rate
limit. Leaking one costs the owner nothing but their quota.

A secret key stays server-side and reaches everything. The one check that
matters most here is the reverse of what you'd expect: a secret key arriving
with a browser Origin header is REJECTED, because that combination means it
has been pasted into frontend code and is already public. Better to break
that developer's build than to let them ship it.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from fastapi import Depends, HTTPException, Request, status

from fpl_api import db

log = logging.getLogger(__name__)

PUBLISHABLE_PREFIX = "pk_"
SECRET_PREFIX = "sk_"
PREFIX_DISPLAY_LENGTH = 11  # "pk_" plus eight characters


class KeyType(StrEnum):
    PUBLISHABLE = "publishable"
    SECRET = "secret"


@dataclass(frozen=True, slots=True)
class ApiKey:
    id: int
    key_type: KeyType
    name: str
    allowed_origins: list[str] | None
    rate_limit_per_minute: int
    last_used_at: datetime | None

    @property
    def is_secret(self) -> bool:
        return self.key_type is KeyType.SECRET


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate(key_type: KeyType) -> tuple[str, str, str]:
    """Return (raw_key, hash, display_prefix).

    The raw key is shown once at creation and never stored. token_urlsafe(32)
    is 256 bits of CSPRNG output — enough that brute force is not a threat
    model worth designing around.
    """
    prefix = PUBLISHABLE_PREFIX if key_type is KeyType.PUBLISHABLE else SECRET_PREFIX
    raw = prefix + secrets.token_urlsafe(32)
    return raw, hash_key(raw), raw[:PREFIX_DISPLAY_LENGTH]


async def _lookup(raw: str) -> ApiKey | None:
    row = await db.fetch_one(
        """
        SELECT id, key_type, name, allowed_origins,
               rate_limit_per_minute, last_used_at
        FROM app.api_key
        WHERE key_hash = $1 AND revoked_at is null
        """,
        hash_key(raw),
    )
    if row is None:
        return None
    return ApiKey(
        id=row["id"],
        key_type=KeyType(row["key_type"]),
        name=row["name"],
        allowed_origins=row["allowed_origins"],
        rate_limit_per_minute=row["rate_limit_per_minute"],
        last_used_at=row["last_used_at"],
    )


async def _touch(key_id: int) -> None:
    """Record use. Best-effort — a failure here must not fail the request."""
    try:
        await db.execute("UPDATE app.api_key SET last_used_at = now() WHERE id = $1", key_id)
    except Exception:
        log.warning("failed to record key usage", exc_info=True)


def _extract(request: Request) -> str | None:
    """Authorization: Bearer <key>, or X-API-Key.

    Bearer is conventional; the header form is easier from a browser and
    avoids consumers reaching for basic auth.
    """
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-API-Key")


async def require_key(request: Request) -> ApiKey:
    """Any valid key. The default for read-only data endpoints."""
    raw = _extract(request)
    if not raw:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "API key required. Send Authorization: Bearer <key> or X-API-Key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    key = await _lookup(raw)
    if key is None:
        # Deliberately identical to the missing-key message. Distinguishing
        # "no key" from "wrong key" tells an attacker which half to work on.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or revoked API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    origin = request.headers.get("Origin")

    if key.is_secret and origin:
        # The check worth having. A secret key with a browser Origin means it
        # is in frontend code and therefore already public. Failing loudly
        # here costs a developer an afternoon; failing silently costs them
        # their key.
        log.warning(
            "secret key presented from a browser origin",
            extra={"key_id": key.id, "origin": origin},
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "A secret key was sent from a browser. Secret keys must never "
            "reach client-side code — anyone can read them there. Use a "
            "publishable key (pk_) in the browser and keep this one on your "
            "server.",
        )

    if (
        not key.is_secret
        and key.allowed_origins is not None
        and (origin is None or origin not in key.allowed_origins)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Origin not permitted for this key. Allowed: " f"{', '.join(key.allowed_origins)}",
        )

    await _touch(key.id)
    request.state.api_key = key
    return key


async def require_secret_key(
    key: ApiKey = Depends(require_key),
) -> ApiKey:
    """Server-side only. For expensive endpoints — optimisation, bulk export.

    Not yet used, but declared now so the distinction exists before the first
    endpoint that needs it, rather than being retrofitted under pressure.
    """
    if not key.is_secret:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This endpoint requires a secret key (sk_). Publishable keys "
            "cannot be used because this operation is expensive enough to "
            "be worth abusing.",
        )
    return key
