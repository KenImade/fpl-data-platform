"""Route dependencies.

`authenticated` is what routers should depend on. It bundles authentication
and rate limiting so a new endpoint gets both by default — the alternative
being that someone adds a router, remembers auth, forgets the limit, and
nothing complains.
"""

from __future__ import annotations

from fastapi import Depends, Request, Response

from fpl_api import ratelimit
from fpl_api.auth import ApiKey, require_key, require_secret_key


async def authenticated(
    request: Request,
    response: Response,
    key: ApiKey = Depends(require_key),
) -> ApiKey:
    await ratelimit.enforce(request, response, key)
    return key


async def authenticated_secret(
    request: Request,
    response: Response,
    key: ApiKey = Depends(require_secret_key),
) -> ApiKey:
    await ratelimit.enforce(request, response, key)
    return key
