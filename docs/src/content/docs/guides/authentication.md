---
title: Authentication
description: Two key types, and why the distinction matters.
sidebar:
  order: 2
---

Create an API key before making requests. Every request must include your key in
either the `X-API-Key` or `Authorization` header. New integrations should
prefer `X-API-Key`.

```bash
curl \
  -H "X-API-Key: pk_..." \
  "https://api.premierlytics.com/v1/teams?season=2026-2027"
```

The following is also supported:

```bash
curl \
  -H "Authorization: Bearer pk_..." \
  "https://api.premierlytics.com/v1/teams?season=2026-2027"
```

## Two kinds of key

| Key | Intended for | Browser-safe | Notes |
| ---- | ------------ | :----------: | ----- |
| `pk_` | Frontend applications | ✅ | Read-only endpoints, optional origin allowlist, modest rate limits. |
| `sk_` | Backend services | ❌ | Higher rate limits and access to privileged endpoints. Never expose it to client-side code. |

A publishable key is expected to be visible in your JavaScript bundle. If
someone copies it, they consume your quota—and nothing else.

A secret key is different. It grants additional privileges and must remain on
your server.

## Secret keys are rejected from browsers

Requests that appear to originate from a browser cannot use `sk_` keys. If a
valid secret key is sent from frontend code, the request fails with **403
Forbidden**.

```json
{
  "type": "about:blank",
  "title": "Secret keys cannot be used from browsers.",
  "status": 403,
  "request_id": "468a427a0bd94a90a314105cbfcee4bd"
}
```

A secret key reaching browser code is already compromised. Rejecting the request
immediately is safer than allowing it to work.

## Rate limits

Every response includes rate-limit headers:

```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 57
X-RateLimit-Reset: 41
```

`X-RateLimit-Reset` is the number of seconds until your quota resets.

When you exceed your limit, the API responds with **429 Too Many Requests** and
includes a `Retry-After` header. Respect it instead of retrying immediately.

## Request IDs

Every response includes an `X-Request-ID`.

If something looks wrong, include that value when contacting [support](mailto:kenneth.imade@yahoo.com)—it lets us
locate the exact request quickly.

If you send your own `X-Request-ID`, we'll preserve it in the response so you
can correlate requests across your own systems.