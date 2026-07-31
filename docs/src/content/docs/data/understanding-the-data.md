---
title: Understanding the data
description: Identifiers, grain, and competition — the three things that will silently corrupt an analysis if you get them wrong.
sidebar:
  order: 1
---

Three properties of this dataset will quietly produce wrong answers if you
assume otherwise. None is obvious from the endpoint reference, and all three
are worth understanding before you build on the data.

There are three invariants to keep in mind:

1. IDs are season-scoped unless they're codes.
2. Player data is stored per fixture, not per gameweek.
3. Competition matters unless you explicitly filter it out.

:::tip[Check these first]
If a historical query looks plausible but surprisingly good, check these three
things first.
:::

## Player identifiers are not stable

FPL reassigns `player_id` each season. It identifies a slot in that season's
player list, not a person.

In this dataset, `player_id` 3 has belonged to **three different players**
across three seasons. So have 4, 5, 7, 8, 9 and 10.

```sql
-- What a cross-season join on player_id actually does
SELECT
    player_id,
    COUNT(DISTINCT player_code) AS people
FROM players
GROUP BY player_id
HAVING COUNT(DISTINCT player_code) > 1;
```

A query joining a player's 2024/25 history to their 2026/27 form on
`player_id` will quietly blend multiple careers into one and return no error.

**Use `player_code` instead.**

`player_code` is FPL's permanent identifier for the underlying player. It
survives season rollovers and appears on every player-scoped response.

| Field | Scope | Use for |
| --- | --- | --- |
| `player_id` | Single season | Requests within one season |
| `player_code` | Permanent | Anything spanning multiple seasons |

The same distinction applies to clubs.

`team_id` is season-scoped.

`team_code` is permanent.

Fixture data references clubs by `team_code`, not `team_id`.

Club names also change. For example, **Ipswich** in 2024/25 became
**Ipswich Town** in 2026/27. It is the same club with the same `team_code`
of 40. Grouping by name splits one club into two.

## The grain is per fixture, not per gameweek

`fct_player_gw` contains one row per player, per fixture.

Most gameweeks contain a single fixture for each player, so this distinction is
easy to miss. Double gameweeks expose it: a player with two matches has two
rows.

This is deliberate.

Several FPL scoring rules are evaluated per match: the 60-minute appearance
threshold, clean sheets and defensive contribution thresholds.

Evaluating those rules on a gameweek aggregate produces incorrect results.

We measured the difference. Reconciling against gameweek-grain data produced
22 discrepancies, all in a single double gameweek, and every one disappeared
when calculated at fixture grain.

### Which columns are which

Per-fixture columns are true at fixture grain and can be safely aggregated:

`minutes`, `goals`, `assists`, `xg`, `xa`, `tackles`, `saves`, and the rest of
the match statistics.

Gameweek-level columns are prefixed with `gw_`:

- `gw_points`
- `gw_bonus`
- `gw_yellow_cards`
- `gw_own_goals`

These are populated **only on the first fixture** in a gameweek.

Subsequent fixtures contain `NULL`.

`is_gw_primary` identifies which row carries the gameweek totals.

That means both of these are correct:

```sql
SELECT SUM(gw_points)
FROM player_gameweeks;
```

```sql
SELECT SUM(minutes)
FROM player_gameweeks;
```

The `gw_` columns appear only once so a naïve `SUM()` works without
double-counting during double gameweeks.

The trade-off is that, for a double gameweek, you cannot determine which
fixture contributed the bonus points.

**That information does not exist in the source data, so no modelling choice
can recover it.**

## Competition is not optional

The dataset includes every competition played by Premier League clubs, not
just league fixtures:

- Premier League
- Champions League, Europa League and Conference League
- FA Cup, EFL Cup, Community Shield and UEFA Super Cup
- **97 pre-season friendlies in 2026/27**

Those friendlies are assigned numbered gameweeks rather than gameweek 0.

A query that assumes every match is a league fixture will happily treat a July
friendly against third-division opposition as "gameweek 3 form".

Filter on `is_league` for league-only analyses, or on `competition` for finer
control.

Including other competitions is deliberate rather than accidental.

A midweek Champions League tie contributes genuine fatigue and injury risk.

For that reason, features such as `days_since_last_match` and
`matches_prior_14d` are calculated across **all** competitions.

One subtle point: player match statistics do not include a competition column.

Competition comes from the fixture via `match_id`.

A per-90 calculation that skips that join silently includes European minutes
in what appears to be a league-only statistic.

## Coverage

Three seasons are available, and they are not equivalent.

| Season | State | Notes |
| --- | --- | --- |
| 2024/25 | Complete | Nested source layout, fewer columns. No injury news field. |
| 2025/26 | Complete | Full dataset. |
| 2026/27 | In progress | Fixtures published. Results accumulate from 21 August. |

Fields missing from a season return `NULL` rather than being omitted.

Models spanning multiple seasons must distinguish between **unknown** and
**zero**.

For example, 2024/25 has no `news` field. A missing value means the information
was never collected—not that the player had no injury news.

Known defects, together with their scale and consequences, are documented on
the **Data quality** page.

You'll eventually discover them anyway. It's better to understand them before
they surprise you halfway through an analysis.