# Data Source Assessment — `olbauday/FPL-Core-Insights`

**Assessed:** 27 July 2026
**Verdict:** Adopt as primary match-stats source. Does **not** replace deadline-precise capture. Requires a change to the historical plan.

---

## 1. What it is

A CSV dataset powering fplcore.com, refreshed twice daily at 07:30 and 17:30 UTC via GitHub Actions. 172 stars, 42 forks, 1,617 commits — actively maintained. It fuses three sources: the official FPL API, detailed per-match statistics, and ClubElo team ratings.

Structure per season: master files at `data/{season}/`, plus `By Gameweek/GW{n}/` snapshots and `By Tournament/{competition}/GW{n}/` slices.

---

## 2. What it gives you — and it's a lot

### 2.1 Entity resolution is solved

This is the headline. Every row in `playermatchstats` is keyed by FPL `player_id`. The match statistics are **already aligned to FPL IDs**.

§4a of the concept document — the cross-source name matching between FPL, FBref and Understat that I described as likely to consume more time than the modelling — largely evaporates for the seasons this covers. No accent stripping, no fuzzy matching, no manual mapping seed, no `Son Heung-min` versus `Heung-Min Son`. I over-weighted that risk given this source exists.

### 2.2 Cross-season identity is nearly free too

`players.csv` carries `player_code` (e.g. `232413` for Eze) alongside `player_id`. FPL's `code` is **stable across seasons**, unlike the element ID which gets reassigned. That's precisely the `dim_person` key I proposed building from scratch.

`dim_person` still earns its place as an abstraction — you want your own surrogate, and you'll need it for players who predate your data — but populating it becomes a straightforward mapping rather than a fuzzy-matching project.

### 2.3 Feature coverage against the catalogue

Almost everything in Appendix A of the concept document is present:

| Need | Provided |
|---|---|
| Player xG, xA, xGOT per match | `xg`, `xa`, `xgot` in `playermatchstats` |
| Shot volume and location | `total_shots`, `shots_on_target`, `big_chances_missed` |
| Attacking involvement | `chances_created`, `touches_opposition_box`, `final_third_passes`, `successful_dribbles` |
| **Defensive contribution inputs** | `tackles`, `tackles_won`, `interceptions`, `recoveries`, `blocks`, `clearances`, `headed_clearances` — full CBIT + recoveries |
| Goalkeeper modelling | `saves`, `goals_prevented`, `xgot_faced`, `sweeper_actions`, `high_claim` |
| Team strength for Dixon-Coles | Match-level `home/away_expected_goals_xg`, plus `xg_open_play`, `xg_set_play`, `non_penalty_xg` |
| Set-piece duties | `penalties_order`, `direct_freekicks_order`, `corners_and_indirect_freekicks_order` in `playerstats` |
| Baseline to beat | `ep_next`, `ep_this` |

Three things it provides that I didn't have a source for at all:

**`start_min` / `finish_min`.** Not just minutes played, but *when* a player entered and left. That's materially better input for the minutes model than a scalar — a 60th-minute substitution and a 60-minute start are the same number and completely different signals.

**ClubElo ratings per team, including promoted clubs.** This directly solves the promoted-team cold start I flagged as needing hand-built Championship-adjusted priors. Coventry, Hull and Ipswich arrive in 2026/27 with real Elo already attached.

**Cup, European and pre-season coverage, linked to FPL IDs.** Fixture congestion and European fatigue were features I listed with no available source. Pre-season friendlies under Gameweek 0 also help the GW1 cold start, normally the single worst prediction week of the season.

**`team_goals_conceded`** — goals conceded by the team *only while the player was on the pitch* — is a genuinely thoughtful field for defensive modelling.

---

## 3. The three limitations

### 3.1 Three seasons, across two incompatible layouts

My first probe checked `data/2024-2025/players.csv` and got a 404. That was the wrong path — **2024/25 exists under a nested, table-per-directory layout** that was flattened in later seasons. Corrected picture:

```
data/2024-2025/{table}/{table}.csv      ← nested layout
data/2025-2026/{table}.csv              ← flat, + By Gameweek/ + By Tournament/
data/2026-2027/{table}.csv              ← flat, same as 2025-26
data/2023-2024/                          → does not exist (both layouts probed)
```

**Three seasons, not two.** 2024/25 is complete: 380 matches, 807 players, 27,658 `playerstats` rows and 11,568 `playermatchstats` rows.

What 2024/25 does **not** have: `By Gameweek/` snapshots, `By Tournament/` slices, `gameweek_summaries.csv`, or a `fixtures/` directory. Per-gameweek granularity survives anyway, because `playerstats.csv` carries a `gw` column across all 27,658 stacked rows.

### 3.1a Schema drift between seasons — build an adapter

Column counts diverge, so your staging layer needs a per-season adapter rather than one reader. Measured directly:

| Table | 2024/25 | 2025/26 | Added in 2025/26 |
|---|---|---|---|
| `players` | 7 | 7 | — identical |
| `teams` | 13 | 14 | `fotmob_name` |
| `playerstats` | 58 | 87 | Names, `news`, `news_added`, raw counting stats, `defensive_contribution`(+`_per_90`), `tackles`, `clearances_blocks_interceptions`, `recoveries`, set-piece text fields, several per-90s |
| `matches` | 102 | 115 | Physical tracking (distance covered, walking/running/sprinting, sprint counts, top speed) and `tournament` |
| `playermatchstats` | 54 | 64 | `defensive_contributions`, physical tracking, `dispossessed`, `corners`, `saves_inside_box` |

Two traps in that table:

**`defensive_contribution` (singular, in `playerstats`) versus `defensive_contributions` (plural, in `playermatchstats`).** Different tables, different pluralisation, same concept. This will bite you at least once.

**2024/25 has no `news` or `news_added` at all.** Your minutes model loses the free-text injury flag for that season. `status` and `chance_of_playing_next_round` are present in both, so it's a partial loss rather than a total one — but declare it in your feature-availability metadata or the model will train on a null-filled column and silently learn that injuries don't exist.

### 3.1b Defensive contribution can be reconstructed for 2024/25 — this is worth real effort

The single most valuable thing in the corrected picture. **2024/25 `playermatchstats` already contains every CBIT component**, verified field by field:

```
tackles YES   tackles_won YES   interceptions YES   recoveries YES
blocks  YES   clearances  YES   headed_clearances YES
xg YES   xa YES   xgot YES   start_min YES   finish_min YES
minutes_played YES   team_goals_conceded YES
```

Only the *aggregated* `defensive_contributions` field is new in 2025/26. The raw inputs were there all along.

So you can **recompute defensive contribution retroactively across 2024/25** using the current ruleset definition, roughly doubling your training data for the component I flagged as the highest-value modelling edge available. Going from one season to two on the feature least well modelled by the rest of the market is a disproportionate gain.

**And you get the validation for free.** In 2025/26 both the components *and* the official aggregate exist. So:

1. Recompute DefCon from components for 2025/26
2. Compare against the provided `defensive_contribution`
3. If they reconcile, your 2024/25 reconstruction is trustworthy; if not, you've found the discrepancy before it reaches a model

Make that a dbt test. It's the kind of check that costs an hour and buys confidence in a whole feature family.

**Revised history estimate.** Rich-tier coverage is 2024/25 and 2025/26 complete, plus 2026/27 accumulating live. By a 2027/28 launch you'd hold **three complete rich seasons** — meaningfully better than the season-and-a-half I estimated before, and enough that the involvement model is defensible rather than merely hopeful. Hierarchical shrinkage still matters, but the ambition ceiling rises.

The deep-thin tier from vaastav (2016/17 onward, verified live) remains worth loading for the team-level match model, where a decade of goals data genuinely helps and no match statistics are needed.

### 3.2 Point-in-time correctness is not solved — confirmed empirically

I checked whether the per-gameweek snapshots could substitute for your own deadline capture. They cannot, and here is the evidence rather than the assumption:

**The `By Gameweek/GW{n}/` snapshots are post-gameweek, not pre-deadline.** `GW11/playerstats.csv` has `event_points` populated — points scored *in* that gameweek. That data only exists after the matches finished. Using those rows as GW11 features would be textbook leakage.

**`snapshot_time` is not usable as provenance.** Every gameweek in `gameweek_summaries.csv` carries the same fixed value (`2025-08-17T04:46:20Z`), not a per-gameweek capture time. You cannot verify when any given row was observed.

**The update cadence doesn't align with deadlines.** Refreshes at 07:30 and 17:30 UTC against 2025/26 deadlines at 11:00, 13:30 and 17:30 UTC means drift ranging from roughly zero to six hours, varying by gameweek, and unverifiable after the fact.

**But there's a precise and useful conclusion**, better than a flat "unusable":

> The **GW(n−1)** snapshot is leak-free as a feature source for GW n, because it demonstrably predates GW n's deadline. What's stale is only the volatile fields.

| Field class | Examples | Verdict |
|---|---|---|
| Performance history | goals, assists, xG, xA, minutes, BPS, defensive actions | **Leak-free and excellent.** These don't change retroactively. |
| Slow-moving status | position, set-piece order, team | Fine |
| **Volatile status** | `now_cost`, `selected_by_percent`, `news`, `chance_of_playing_next_round`, `form` | **Stale by up to six days.** Prices move nightly; injury news moves hourly. |

That volatile bucket is exactly what the minutes model — the highest-leverage component in the whole system — depends on most. A Friday press conference changing a player from doubtful to fit is the single most valuable piece of pre-deadline information, and a twice-daily CSV refresh will not reliably capture it.

**So Phase 0 survives, and is now more sharply justified.** Your own capture is no longer trying to record everything. It exists for one narrow purpose: deadline-precise volatile state. That's a smaller job than originally scoped.

### 3.3 Provenance and licensing

Two findings worth acting on before any commercial step.

**The match stats appear to originate from FotMob.** `teams.csv` carries a `fotmob_name` column. The README describes the stats as "manually curated (Opta-like)" — that's a description of quality, not of licensing. FotMob licenses its underlying data from a provider, and redistribution terms flow downhill.

**There is no licence file.** I checked `LICENSE`, `LICENSE.md` and `LICENSE.txt` — all 404. The README grants informal permission ("feel free to use the data... I'd appreciate a link back"), but absent a licence the legal default is all rights reserved. An informal README grant is genuine goodwill and is fine for a portfolio project. It is not a foundation for a paid tier.

Practical posture:

- Fine to depend on now. Attribute prominently — link the repo in your docs and your README, which is what the maintainer asked for.
- **Serve derived analytics, not their CSVs.** Your API should expose predictions, aggregates and modelled outputs, not act as a mirror. That's both the polite position and the defensible one.
- Consider opening an issue asking the maintainer to add an explicit licence. Costs you nothing, helps everyone downstream, and you'll want the answer before you charge anyone.
- If you go paid, resolve provenance properly — including whether FotMob-derived statistics can sit behind a paywall at all.

### 3.4 Dependency risk

One maintainer, no licence, no formal guarantee. If it stops updating mid-season you have no recourse.

**Mitigation is cheap: mirror it into your own bronze on every refresh.** The full 2025/26 season is a few tens of megabytes. A twice-daily `git pull` and copy into R2 means a disappearance costs you future data but never past data. Add it to Phase 0 — it's five lines.

Also avoid coupling your silver layer tightly to their column names. Adapt at the staging boundary, as you would for any external source.

---

## 4. Net effect on the plan

| Area | Before | After |
|---|---|---|
| Cross-source entity resolution | Multi-week fuzzy matching project, Phase 2b | Largely eliminated — pre-aligned to FPL IDs |
| Cross-season identity | Build `dim_person` from scratch | Key off `player_code`; keep the abstraction, drop the hard part |
| xG / xA ingestion | Scrape FBref and Understat, Phase 5 | Comes free |
| Defensive contribution inputs | No good source | Full CBIT + recoveries |
| Promoted-team cold start | Hand-built Championship priors | ClubElo ratings, ready |
| Fixture congestion / European fatigue | No source | Cup and European fixtures included |
| Historical depth for rich features | Assumed 2017/18+ | **2024/25+** — three seasons, two layouts |
| Deep FPL history | Assumed from archives | Still needed, from vaastav, thin |
| Defensive contribution training data | Assumed 2025/26 only | **Reconstructable back to 2024/25** from CBIT components |
| Point-in-time capture | Phase 0 | **Unchanged — still required**, but narrower in scope |
| Licensing | Not considered | Unresolved; blocks a paid tier until addressed |

**Estimated time saved: three to five weeks**, concentrated in the parts of Phase 2 and Phase 5 that were least enjoyable and most error-prone.

**Estimated cost: your rich-feature training set is three seasons, not nine.** A real constraint on model ambition, though less severe than it first appeared — and still the strongest argument for launching in 2027/28 rather than rushing 2026/27.

---

## 5. Recommended source architecture

Three sources, cleanly separated by what each is authoritative for:

```
┌─ Your own deadline capture (Phase 0) ─────────────────────┐
│  bootstrap-static, fixtures, event-status                 │
│  Every 3h; every 15 min in the 6h before a deadline       │
│  AUTHORITATIVE FOR: deadline-precise volatile state —     │
│  price, ownership, injury news, chance_of_playing         │
│  Irreplaceable. Perishable. Cannot be backfilled.         │
│  MUST BE LIVE BEFORE FRI 21 AUG 2026.                     │
└───────────────────────────────────────────────────────────┘

┌─ FPL-Core-Insights (mirrored to your bronze) ─────────────┐
│  playermatchstats, matches, playerstats, teams+elo        │
│  AUTHORITATIVE FOR: match statistics, xG, defensive       │
│  actions, Elo, cup and European fixtures                  │
│  Coverage: 2024/25 onward — TWO LAYOUTS, needs an adapter │
│    2024/25  data/{season}/{table}/{table}.csv  (nested)   │
│    2025/26+ data/{season}/{table}.csv          (flat)     │
└───────────────────────────────────────────────────────────┘

┌─ vaastav/Fantasy-Premier-League ──────────────────────────┐
│  Historical FPL CSVs                                      │
│  AUTHORITATIVE FOR: deep FPL history, 2016/17 onward      │
│  Thin — no match stats. Trains the team-level match model │
│  and provides long-run priors only.                       │
└───────────────────────────────────────────────────────────┘
```

┌─ vaastav/Fantasy-Premier-League ──────────────────────────┐
│  Historical FPL CSVs                                      │
│  AUTHORITATIVE FOR: deep FPL history, 2016/17 onward      │
│  Thin — no match stats. Trains the team-level match model │
│  and provides long-run priors only.                       │
└───────────────────────────────────────────────────────────┘
```

Record source and coverage as explicit metadata per season, exactly as §2a of the concept document requires. A model must never see a feature that was null-filled because the source didn't exist yet — and with a two-tier history that trap is now much easier to fall into.
