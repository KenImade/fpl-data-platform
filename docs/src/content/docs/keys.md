---
title: Get an API key
description: Request a publishable or secret API key.
sidebar:
  order: 4
---

Premierlytics is currently in **early access**.

API keys are issued manually while the platform grows. That keeps abuse under
control, helps me understand how people are using the API, and lets me shape
new endpoints around real-world use cases.

Most requests receive a reply within **one business day**.

## Which key do you need?

### Publishable (`pk_`)

For browser applications.

Use a publishable key if requests originate from JavaScript running in a user's
browser.

A publishable key:

- is safe to embed in frontend code
- provides read-only access
- can be restricted to specific origins
- has modest rate limits

### Secret (`sk_`)

For servers, scripts and backend services.

Secret keys should never be exposed to browsers or mobile applications.

A secret key:

- has higher rate limits
- is intended for server-side applications
- must be kept private
- is rejected if sent from a browser

If you're unsure which you need, request a publishable key. You can always
upgrade later.

---

## Request a key

Send an email to **kenneth.imade@yahoo.com** with:

- what you're building
- whether you need a publishable (`pk_`) or secret (`sk_`) key
- an estimate of expected usage (optional)

A short email is perfectly fine.

> Hi,
>
> I'm building an FPL research project that predicts player performance from
> historical fixture data. I'd like a publishable API key for development.
>
> Thanks!

Most requests are approved within one business day.

---

## Why are keys issued manually?

Premierlytics is still in its early stages.

Issuing keys manually lets me:

- understand how people are using the API
- prioritise new endpoints around real use cases
- keep rate limits sensible while the platform grows
- provide direct support during integration

Self-service registration will come later. The API itself is designed to remain
stable, so your integration will not need to change when that happens.

---

## Before you request a key

You can explore the documentation without one:

- Read the **Quickstart** to see your first request.
- Learn about **Point-in-time queries** and how snapshots prevent historical leakage.
- Browse the **Endpoint reference** to see every available endpoint.
- Read **Understanding the data** before building historical features.

If you're unsure whether Premierlytics fits your project, include a brief
description in your email. I'm happy to recommend the most appropriate
endpoints or discuss your use case.