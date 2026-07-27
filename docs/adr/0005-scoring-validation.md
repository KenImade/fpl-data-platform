# 0005 — Scoring calculator validated against 2025/26

**Status:** Accepted
**Date:** 2026-07-27

## Context

`fpl_core.scoring` computes FPL points from match statistics. Every number in the
prediction and optimisation layers inherits it, so it needed validating against a
real season before anything was built on top.

Method: difference the cumulative FPL fields in Core Insights `playerstats.csv`
across consecutive gameweeks, score with our calculator, compare against
`event_points`. 29,865 player-gameweeks reconciled from 2025/26.

**Result: 99.92% exact match.** All 23 remaining divergences are understood and
attributable to the differencing method, not to the calculator.

## Decisions

### 1. Defensive contribution is computed from components, per position

The published `defensive_contribution` field bundles recoveries for **all**
positions. FPL only credits recoveries to midfielders and forwards; defenders
need 10 CBIT (tackles + clearances/blocks/interceptions) alone.

Using the aggregate over-counted defenders and produced 269 false +2 awards,
concentrated in ball-playing centre-backs — Arsenal's defence dominated the list.

Eligibility is encoded in the ruleset (`recoveries_count` per position) and the
count is assembled by `DefensiveContribution.actions()`. **The published aggregate
is unusable for defenders in any season.**

This also resolves the open question in step 34: the reconstruction formula for
2024/25 is now derived from data rather than assumed.

### 2. `fct_player_gw` must be grained at player × gameweek × fixture

Confirmed empirically. 22 of 23 residual divergences are gameweek-grain artefacts:

- **GW33 (20 rows):** a double gameweek. Every row short by exactly one appearance
  point; one row short by two. Differencing collapses both fixtures into a single
  row and the calculator scores it as one match.
- **R.Gomes GW26 (+6):** minutes jump by 120 and 165 in single gameweeks, so this
  player's rows span multiple fixtures per gameweek — cup or European matches
  bleeding into the data.

Per-match scoring rules — the 60-minute appearance threshold, clean sheets, and
defensive-contribution thresholds — cannot be evaluated correctly on a gameweek
aggregate. The grain requirement was already specified; this is evidence for it.

### 3. Cards and own goals score at zero minutes

Barnes GW22: zero minutes, FPL awarded −1. Yellow cards can be issued to unused
substitutes or after the final whistle.

The rule is "zero minutes means no *appearance* points", not "zero minutes means
zero points". `score()` computes disciplinary components before the early return.

### 4. Cumulative fields are subject to retroactive restatement

FPL restates cumulative totals when events are reassigned after the fact.
Differencing turns a restatement into a phantom event in whichever gameweek the
snapshot straddles.

Consequence for our own capture: intra-day snapshots must be preserved rather than
collapsed to one per gameweek, so restatements are visible as changes between
observations rather than silently absorbed.

## Consequences

- Ruleset schema gains `defensive_contribution.recoveries_count`
- Staging (step 30) assembles defensive actions from components, never the aggregate
- Step 34's 2024/25 reconstruction uses the formula established here
- Double gameweeks cannot be validated until the per-fixture grain exists (step 32);
  re-run an equivalent reconciliation then
- `scratch/reconcile.py` deleted — superseded by dbt tests