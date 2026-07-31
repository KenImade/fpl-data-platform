---
title: Point-in-time queries
description: How to build historical features without leaking future information.
sidebar:
  order: 3
---

If you're training or backtesting a prediction model, this page explains how to
avoid data leakage.

## The problem

Fantasy Premier League overwrites state in place. Prices change nightly, injury
news changes hourly, and statistics like `form` and `points_per_game` are
recalculated continuously.

Query the API today and you get today's values — even for gameweeks that
finished months ago.

A feature built from today's data for a historical gameweek contains
information nobody knew before that deadline. A model trained on those features
looks excellent offline and disappoints in production, and the reason is
invisible in the results.

**This is the single most common way FPL prediction projects go wrong.**

## What we do instead

We capture FPL's complete state every three hours, tightening to every fifteen
minutes during the six hours before each deadline. Every capture is stored
immutably and timestamped.

A **snapshot** for a gameweek is the final capture made **strictly before** that
gameweek's deadline.

A capture taken exactly at the deadline is already too late. Teams lock the
instant the deadline passes, so the last valid snapshot is the final capture
before it.

Features derived from a snapshot cannot contain post-deadline information —
not because we are careful, but because the row does not exist unless it was
captured first.

## Using snapshots

Snapshot-scoped endpoints return player state exactly as it existed before a
deadline.

```bash
curl \
  -H "X-API-Key: sk_..." \
  "https://api.premierlytics.com/v1/snapshots/2026-27-gw01/players"
```

Snapshot IDs can be discovered from the Gameweeks endpoints and should be stored
alongside any predictions you generate.

Every row includes snapshot metadata:

| Field | Meaning |
| --- | --- |
| `snapshot_id` | Stable identifier for the snapshot. Store it with every prediction. |
| `snapshot_at` | When the capture was taken. |
| `deadline_utc` | The deadline the snapshot precedes. |
| `hours_before_deadline` | How old the snapshot was when the deadline arrived. |
| `is_usable` | Whether the snapshot is recent enough for historical analysis. |

## Reading the quality fields

`hours_before_deadline` is the first value to check.

During the deadline window, captures occur every fifteen minutes, so a healthy
snapshot is typically **under 0.25 hours (15 minutes)** old.

A larger value means the capture cadence did not hold for that gameweek. The
capture pipeline may have stalled, or the service may have been unavailable.
The data is still genuine, but it is older than intended, and injury news in
particular can change meaningfully over a few hours.

`is_usable` becomes `false` when no capture exists within one week of the
deadline.

Before a future gameweek, that is expected. After a completed gameweek, it means
the snapshot is missing and historical features for that deadline should not be
trusted.

We publish both fields rather than hiding them because a silently stale snapshot
is worse than an obviously missing one.

## Reproducing a prediction

`snapshot_id` is what makes a prediction reproducible.

Store it alongside every prediction you generate, and you can retrieve the exact
inputs that produced it later—not approximately, but the same rows.

If you cannot regenerate a prediction from its snapshot, your feature pipeline
is likely reading data outside the snapshot, or the snapshot itself is
incomplete. Both are worth investigating.

## What not to use

`/v1/players` serves **current** state.

It is the right endpoint for a transfer planner, a price tracker, or anything
concerned with the latest FPL data.

It is the wrong endpoint for building historical features. Every value reflects
today's state, even for gameweeks that finished long ago.

If you're training a model on `/v1/players`, your validation metrics are
optimistic because your features contain information that wasn't available at
prediction time.

Use snapshot-scoped endpoints instead.