---
title: Data quality
description: Every known defect, its scale, and what it means for your analysis.
lastUpdated: 2026-07-30
sidebar:
  order: 2
---

Every dataset has defects. Most API documentation omits them, which means you
find them yourself, halfway through an analysis, and then have to work out
whether the rest of the data is trustworthy.

This page lists what we know is wrong, how large it is, and what it means for
your analysis. Where a defect originates upstream, we say so, along with
whether it has been reported.

---

## Summary

| Issue | Severity | Affects |
| --- | --- | --- |
| Missing scoring records | Medium | Exact player totals |
| Missing fixture scrape | High | 16 player histories |
| Missing kickoff times | Medium | Timing-derived features |
| Placeholder timestamps | Medium | Rest and congestion features |
| Duplicate fixture identifiers | Low | Already handled |
| Unknown positions | None | No active players affected |

---

## Missing scoring records

**Severity:** Medium

**Scale:** 782 points missing out of 34,382 total player points (97.7% complete)
across 2025/26. Affected fixtures typically contain between 2 and 6 affected
players.

**Cause:** The per-match source occasionally omits players who FPL credits
with an appearance. This includes unused substitutes recorded with minutes and
squad members that the upstream scrape did not capture.

**What it means:** A player's season total computed from per-match data can be
lower than FPL's official total. Rate statistics and form calculations are
effectively unaffected; reproducing an exact season total is not.

**Recommended handling:** Use official season totals when exact reconciliation
is required. Use fixture-level data for modelling and analysis where small
point discrepancies are acceptable.

**Status:** Inherent to the source. Not fixable downstream.

---

## One fixture was never scraped

**Severity:** High

**Scale:** Gameweek 29, 2025/26. One fixture, 16 players.

**Cause:** The fixture exists in match data with a correct result, but its
player-level rows were never collected upstream.

**What it means:** Those 16 players are missing one appearance from their
season history. Any feature using minutes, appearances, or per-90 calculations
for those players will be understated.

**Recommended handling:** Exclude affected player histories if exact minutes or
appearance totals are required.

**Status:** Reported upstream.

---

## Missing kickoff times in the 2025/26 run-in

**Severity:** Medium

**Scale:** 48 league fixtures — every match from gameweek 34 to 38.

**Cause:** These fixtures contain results but no kickoff timestamp. Gameweek 33
and earlier are complete.

**What it means:** Anything derived from kickoff timing is null for the final
five gameweeks of 2025/26:

- `days_since_last_match`
- `matches_prior_14d`
- rest and congestion features

Results, scores, and player statistics are unaffected.

**Recommended handling:** Avoid timing-based features for these fixtures.

**Status:** Reported upstream.

---

## Placeholder timestamps on 13 fixtures

**Severity:** Medium

**Scale:** 13 gameweek 33 fixtures in 2025/26, all stamped
`2025-09-25 19:00:00`.

**Cause:** Thirteen matches share one timestamp on a date five months before
the gameweek they belong to. Upstream appears to have populated placeholder
rows with another fixture's metadata.

**What it means:** Rest-day and congestion calculations are meaningless for
clubs involved in these fixtures.

Scores and player statistics are correct — only timing information is affected.

**Recommended handling:** Do not use timing-derived features for these
fixtures.

**Status:** Reported upstream. Surfaced as a warning in our own checks, so we
will detect if the count changes.

---

## Duplicate fixture identifiers

**Severity:** Low

**Scale:** One fixture in 2025/26, which duplicated 16 player rows.

**Cause:** A Conference League fixture appeared under two gameweek directories
upstream, with different scores. One was a knockout placeholder populated with
the real fixture's data.

**What it means:** No current impact.

We deduplicate on ingest, keeping the earlier gameweek. A test fails the build
if this pattern appears again with a different shape.

**Status:** Reported upstream. Handled downstream.

---

## Unclassified positions in 2024/25

**Severity:** None

**Scale:** 20 players.

**Cause:** The 2024/25 source records their position as `Unknown`.

**What it means:** No practical impact. All 20 players recorded zero minutes.
They appear in the player list with a null position and no match data.

**Status:** Inherent to the source.

---

## Checks that pass

These are things we have tested and found sound, so you do not need to verify
them yourself.

- **Points reconcile.** On completed 2025/26 player-gameweeks, our scoring
  calculation matches FPL's official figure on 99.79% of records. The remaining
  divergences are fully explained by the double-gameweek aggregation described
  in [Understanding the data](/data/understanding-the-data/).

- **Goals are symmetric.** Every fixture's home goals-for equals its away
  goals-against, checked on every build.

- **Grain is unique.** One row per player per fixture, one row per team per
  match, and one row per player per season. These constraints are enforced by
  tests rather than assumed.

- **Kickoff times are UTC.** Verified against an independent epoch field on
  every build, so timezone parsing errors cannot go unnoticed.

---

## Reporting something

If you find a defect not listed here, it is genuinely useful to hear [about
it](mailto:kenneth.imade@yahoo.com). Include the request ID from the response, which makes the exact data path
findable.

Most entries above were found by tests that were written expecting to pass.

The most valuable reports are the defects nobody has looked for yet.