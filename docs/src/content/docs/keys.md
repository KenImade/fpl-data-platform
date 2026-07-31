---
title: Get an API key
description: Request a key for the Premierlytics API.
---

Keys are issued manually at the moment. Send a request and you will hear
back within a day.

## What to tell us

- **What you are building.** A line is enough — it helps to know whether the
  rate limit suits you.
- **Which key type.** [Publishable or secret](/guides/authentication/) — if
  the key will exist in browser code, it must be publishable.
- **Origins**, if publishable and you want it restricted to your domain.
  Recommended: a restricted key that leaks is worth nothing to anyone else.

## Request

[form: name, email, use, key type, origins]

## While you wait

The demo key on the [home page](/) works for everything read-only, at a
lower rate limit. Enough to decide whether this is useful before committing
to anything.

## Why not self-service

An account system is meaningful work, and at current volume manual issuance
costs a few minutes. If that stops being true it will change.

The practical consequence: keys are tied to a person rather than an account,
and there is no dashboard to rotate one yourself. Ask and it will be rotated.