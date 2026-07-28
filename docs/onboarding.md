# FPL Data Platform — Onboarding Summary

_Written 2026-07-28 as a snapshot of the codebase for a new contributor. Reflects `main` at commit `fd27596` plus the in-progress working-tree changes on top of it (Core Insights → Postgres load pipeline)._

## What this project is

A data platform for Fantasy Premier League (FPL), built by one person part-time. The long-term
vision (see [Product vision docs](#product-vision-docs-recover-these) below) is a public API
serving curated FPL data, expected-points predictions, and a squad/transfer optimiser — targeted
for a **2027/28** public launch. **2026/27 is explicitly a "free data collection year"**: the
current focus is capturing high-quality, deadline-precise historical data so there's something to
train models on later. Nothing here is wasted effort, but don't expect a finished product yet —
you're looking at Phase 0–2 of an 8-phase roadmap.

## Repo layout

```
packages/
  fpl-core/         domain models, scoring rules — the only package that's "done"
  fpl-ingestion/     the Dagster project — where almost all current work is
  fpl-api/           FastAPI service, currently just /health — this is what's deployed
  fpl-modelling/     empty placeholder (future: prediction models)
  fpl-optimiser/     empty placeholder (future: MILP squad optimiser)
rulesets/            versioned YAML scoring config (2025-26.yml, 2026-27.yml)
scripts/             one-off ops scripts (wipe.py, record_fixtures.py, mirror_2024_25.py)
deploy/dagster.yaml  production Dagster instance config
justfile             single entry point for all dev commands
compose.yaml         local Postgres + MinIO
Dockerfile, fly.toml Fly.io deployment
docs/                see note below — several vision docs were deleted from disk
```

A `uv` workspace ties the packages together; Python 3.13, strict mypy across all packages, ruff
for lint/format.

## Getting started

```
just bootstrap   # docker compose up, create dagster DB + MinIO bucket, uv sync --all-packages
just dev         # dagster dev -m fpl_ingestion.definitions  — the main thing you'll run
just test        # pytest -q
just lint        # ruff check + format --check
just typecheck   # mypy
```

Local storage defaults to MinIO (S3-compatible) via `compose.yaml`; `DRY_RUN` env var switches
`StoreResource` to a filesystem-backed `LocalStore` instead, so you can develop without any bucket
at all. Production uses Cloudflare R2 (also S3-compatible).

## fpl-core: domain models & scoring

The stable foundation. `fpl_core.rules` loads a ruleset YAML (`rulesets/2025-26.yml`, etc.) —
points-per-action config (goals by position, clean sheets, defensive-contribution thresholds).
`fpl_core.scoring` computes player gameweek points from that ruleset. This was validated against
29,865 real 2025/26 player-gameweek rows at 99.92% exact match (see the deleted ADR, recoverable —
below). Key gotcha documented there: defensive contribution must be computed from CBIT components
per-position rather than trusting the FPL API's published aggregate, which over-counts defenders.

Also has: `Player`/`Team`/`Gameweek`/`Fixture` models, a `Price` newtype with FPL's sell-price
arithmetic, and typed IDs (`PlayerId`, `PlayerCode`, `TeamId`, etc.).

## fpl-ingestion: the Dagster project (where the action is)

This is a **bronze-only** medallion pipeline today — raw capture → schema-validated Parquet in
object storage → bulk-loaded into Postgres `bronze.*` tables. No silver/gold layer exists yet;
that's dbt's job and hasn't been started. The `bronze` schema is designed to be entirely
Dagster-owned and rebuildable from object storage at any time (see `scripts/wipe.py`).

### Two data sources

1. **FPL's own API** (`fantasy.premierleague.com/api`) — `bootstrap-static`, `fixtures`,
   `event-status`. Captured at variable cadence (see below) because deadline-precise price/injury
   snapshots can't be reconstructed after the fact.
2. **[FPL-Core-Insights](https://github.com/olbauday/FPL-Core-Insights)** — a third-party GitHub
   repo, mirrored as CSVs + a weekly tarball. Richer match/xG stats, already aligned to FPL player
   IDs, but only twice-daily/post-hoc snapshots — it complements but doesn't replace the FPL
   capture. Schema drifts between the 2024/25 season (nested CSV layout) and 2025/26+ (flat
   layout); `core_insights.py` and `tarball.py` handle both.

### Module map (`packages/fpl-ingestion/src/fpl_ingestion/`)

| Module | Responsibility |
|---|---|
| `client.py` | `httpx` client for the FPL API; retries transport/5xx errors, aborts (no retry) on 429 |
| `capture.py` | Fetches all FPL endpoints, writes raw bytes to storage; one endpoint failing doesn't block others |
| `storage.py` | `Store` protocol + `LocalStore`/`S3Store`; raw objects are immutable-by-convention |
| `schemas.py` | Pydantic `Element` model for a player; derives an explicit Polars dtype map so schema is never inferred per-partition |
| `bronze.py` | Builds `bronze/players/{day}.parquet` — one row per (capture-time × player), deliberately not collapsed, since intraday price/injury movement is the point |
| `mirror.py` | Fetches Core Insights CSVs/tarball → raw storage |
| `core_insights.py` | Builds bronze Parquet from the mirrored CSVs (daily + archive shapes); tracks per-season column availability so models never train on a null-filled column |
| `tarball.py` | Extracts gameweek-level tables from the weekly tarball (`player_gameweek_stats`, `matches`, `playermatchstats`) |
| `load.py` | Bulk-loads bronze Parquet into Postgres via ADBC, truncate-and-replace semantics |
| `checks.py` | Asset check: was there actually a capture near each deadline? (catches "succeeded but captured nothing") |
| `deadlines.py` | Reads gameweek deadlines out of the latest bootstrap capture |
| `schedule.py` | Capture cadence: every 3h normally, every 15min within 6h of a deadline |
| `alerting.py` | Routes run-failure severity (PAGE/NOTIFY/DIGEST) by proximity to the next deadline |
| `heartbeat.py` | Dead-man's-switch ping to an external monitor (healthchecks.io-style) |
| `resources.py` | Two parallel resource patterns: Dagster `ConfigurableResource`s vs. plain `*_from_env()` functions for scripts/tests |
| `definitions.py` | The full Dagster `Definitions` — assets, jobs, schedules, sensors (see below) |

### The actual DAG (`definitions.py`)

Everything lives in one code location, `fpl_ingestion.definitions`. Two kinds of Dagster
computations exist side by side, deliberately: **ops/jobs** for the raw fetch-and-store steps
(capture, mirror), and **assets** for everything with a defined output that other things depend
on (bronze parquet, warehouse tables). The ops aren't assets because their "output" is an
unpredictable set of raw keys in storage, not a single named thing to track lineage against.

```
                     ┌─────────────┐         ┌──────────────────┐
   FPL API   ──────▶ │ capture_op  │ ──────▶ │  bootstrap_bronze │  (asset, partitions_def=daily)
 (bootstrap-static,  │ (capture_job)│         │  group: bronze_fpl│
  fixtures,          └─────────────┘         └─────────┬─────────┘
  event-status)        fpl_capture_sensor              │ [check] captured_near_deadline
                       (variable cadence,               │         (non-blocking, ERROR severity)
                        60s poll)                        │
                                                          ▼ _last() — most recent partition only
                                                ┌───────────────────┐
                                                │   fpl_players      │  (asset, group: warehouse_bronze)
                                                │  (load.py, ADBC)   │ ──▶ Postgres bronze.fpl_players
                                                └───────────────────┘

                     ┌──────────────────┐     ┌───────────────────────────┐
 Core Insights ────▶ │ mirror_masters_op │ ──▶ │ ci_{table}_daily          │  table ∈ {players, teams,
 (GitHub CSVs,       │ mirror_tarball_op │     │ (partitions_def=ci_daily) │  playerstats,
  masters + tarball) │ (19:00 / Sun 20:00)│     │ ci_{table}_archive        │  gameweek_summaries}
                     └──────────────────┘     │ (unpartitioned, 2024/25)  │
                                                │ ci_{table}_{scope}s       │  table ∈ {player_gameweek_stats,
                                                │ (unpartitioned, tarball)  │  matches, playermatchstats}
                                                │ group: bronze_core_insights│
                                                └─────────────┬─────────────┘
                                                               │ explicit deps (LOAD_DEPS dict —
                                                               │ Dagster can't infer these, since
                                                               │ load assets read object storage,
                                                               │ not upstream asset outputs)
                                                               ▼
                                                ┌───────────────────────────┐
                                                │ ci_playerstats, ci_players,│  (assets, group:
                                                │ ci_teams, ci_gameweek_     │   warehouse_bronze)
                                                │ summaries, ci_matches,     │ ──▶ Postgres bronze.ci_*
                                                │ ci_player_gameweek_stats,  │
                                                │ ci_playermatchstats        │
                                                └───────────────────────────┘
```

**The explicit `LOAD_DEPS` wiring** (`definitions.py:280-309`) is the one part of the graph that
isn't structurally obvious, and worth reading directly if you're adding a new table — a few
choices stand out:

- Most `ci_*` load assets depend on **both** the daily/tarball source **and** the 2024/25 archive
  (e.g. `ci_playerstats` ← `ci_playerstats_daily` (latest partition) + `ci_playerstats_archive`) —
  the warehouse table spans a season that predates the daily mirror, so both bronze sources have
  to land in the same Postgres table.
- `ci_gameweek_summaries` has **no** archive dependency — that file was never published for
  2024/25 under the old nested layout, so there's nothing to union.
- `ci_matches`/`ci_playermatchstats` depend on the **tournament-scoped** tarball extraction, not
  the gameweek-scoped one — `tarball.py` treats `By Tournament/` as a verified superset of
  `By Gameweek/` by row count, so using it avoids double-counting. The archive dependency here
  also matters more than it looks: it's the only route to 2024/25's CBIT components, which is what
  the defensive-contribution scoring reconstruction (`fpl-core`, ADR 0005) depends on for that
  season.
- `ci_player_gameweek_stats` has **only** a tarball dependency — this table doesn't exist as a
  flat "master" CSV in any season, so it's tarball-or-nothing.
- A partitioned asset feeding an unpartitioned load asset uses `_last()`
  (`AssetDep` + `LastPartitionMapping`) rather than Dagster's default `AllPartitionMapping` —
  otherwise the load asset would declare a dependency on all 365+ partitions of `bootstrap_bronze`
  and sit permanently stale.
- A `LoadSpec` with no `LOAD_DEPS` entry raises `KeyError` at module import (code-location load
  time), not at runtime — a deliberate fail-fast so a newly added load target can't silently ship
  with an empty dependency set.

### Jobs, schedules, sensors — what actually triggers a run

| Job | Selection | Partitions | Trigger |
|---|---|---|---|
| `capture_job` | `capture_op` | — | `fpl_capture_sensor`, polling every 60s |
| `mirror_masters_job` | `mirror_masters_op` | — | `mirror_masters_schedule`, daily 19:00 UTC |
| `mirror_tarball_job` | `mirror_tarball_op` | — | `mirror_tarball_schedule`, Sundays 20:00 UTC |
| `fpl_bronze_job` | `bootstrap_bronze` | `daily` | `fpl_bronze_schedule`, daily 19:30 UTC |
| `ci_bronze_job` | 4 `ci_*_daily` assets | `ci_daily` | `ci_bronze_schedule`, daily 19:35 UTC |
| `snapshot_job` | all `ci_*_archive` + tarball assets | — | `snapshot_schedule`, Sundays 20:30 UTC (after the tarball mirror) |
| `load_job` | all `load.SPECS` assets | — | `load_schedule`, daily 21:00 UTC |

Two things worth knowing so a schedule change doesn't surprise you:

- **The cron times encode an intended ordering** (mirror → bronze → snapshot → load), but that's
  *only* a convenience — the asset graph and its `deps`/`LOAD_DEPS` are the actual source of
  truth for what depends on what. Nothing stops `load_job` from running against stale bronze
  parquet if it fires before `ci_bronze_job` finishes; there's no cross-schedule barrier.
- `load_job` runs daily even though `snapshot_job` (tarball/archive) only runs weekly on Sundays —
  so six days a week it reloads byte-identical tarball-derived tables into Postgres. Harmless
  under truncate-and-replace semantics at current volume, called out as an accepted tradeoff in
  the code comments rather than an oversight.
- `fpl_capture_sensor`'s cadence isn't a fixed cron at all — it calls `schedule.decide()` every
  tick and self-adjusts: every 3h normally, every 15min within 6h of a deadline. The decision is
  gated on **elapsed time since the last capture** (a Dagster-managed cursor, survives daemon
  restarts), not wall-clock alignment, so a delayed sensor tick still captures late instead of
  skipping the interval entirely.
- `failure_alert_sensor` isn't tied to any one job — `monitor_all_code_locations=True` means it
  fires on **any** run failure anywhere in the deployment, then classifies severity by proximity
  to the next deadline (`alerting.failure_severity()`) rather than by which job failed.

### Asset groups, as they'd appear in the Dagster UI

- `bronze_fpl` — just `bootstrap_bronze`
- `bronze_core_insights` — all `ci_*_daily`, `ci_*_archive`, and `ci_*_{scope}s` tarball assets
- `warehouse_bronze` — the 7 Postgres load assets (`fpl_players`, `ci_playerstats`, `ci_players`,
  `ci_teams`, `ci_gameweek_summaries`, `ci_matches`, `ci_playermatchstats`, `ci_player_gameweek_stats`)

Dependencies: **Polars** is the only dataframe library (no pandas/duckdb); Postgres writes go
through **ADBC**, not SQLAlchemy/psycopg. This is self-hosted Dagster OSS — no `dagster-cloud`.

## Deployment & ops

- **Fly.io**, region `jnb`. Two processes: `web` (dagster-webserver, auto-suspends when idle) and
  `daemon` (dagster-daemon, deliberately **has no `http_service` block so it never auto-stops** —
  this is the single load-bearing design point that keeps schedules/sensors alive).
- **CI** (`.github/workflows/ci.yml`): check job (sync, lint, format, mypy, pytest) → deploy job
  (main only, serialized) → smoke test polling `/health` and asserting the deployed git SHA
  matches.
- **Dead-man's-switch**: `heartbeat.py` pings an external monitor on every successful capture, and
  immediately on failure — this is the only alert that catches the daemon being silently dead
  rather than actively failing.
- `scripts/wipe.py`: nukes and rebuilds local bucket/Postgres schema/Dagster DB for a clean dev
  slate (confirmation required unless `--yes`).

## Known gaps / rough edges worth knowing about

- `scripts/mirror_2024_25.py` imports `build_store` from `resources.py`, but that function no
  longer exists there (only `store_from_env()` does) — looks like a stale reference from before a
  refactor. Will break if run as-is.
- `Dockerfile` only runs `uv sync --package fpl-api`, but the container's `CMD` runs
  `dagster-webserver -m fpl_ingestion.definitions` — worth double-checking this actually works,
  since `fpl-ingestion` isn't explicitly synced in that build stage.
- `tests/test_mirror.py` is empty — `mirror.py` has no test coverage yet.
- Two GitHub Actions deploy workflows exist (`ci.yml`'s deploy job and a separate
  `fly-deploy.yml`) that look redundant — probably worth consolidating.
- No Terraform/IaC yet, though the deployment-strategy doc (recoverable, see below) recommends one
  for R2/DB provisioning.
- Silver/gold dbt layer, identity resolution (`dim_person`), the data API beyond `/health`,
  modelling, and the optimiser are all **not started** — `fpl-modelling`/`fpl-optimiser` are empty
  packages.

## Product vision docs — recover these

`git status` shows 8 files under `docs/` as deleted from the working tree, but they're **not
committed as deleted** — they still exist at `HEAD` and are one command away from coming back:

```
git checkout HEAD -- docs/
```

These aren't boilerplate — they're most of the project's recorded reasoning and are genuinely
useful before you touch anything:

- `fpl-api-concept.md` — the overall product concept (data + predictions + optimiser API)
- `fpl-api-roadmap.md` — the 8-phase build roadmap; explains why the repo looks like it does today, includes the hard **21 Aug 2026** GW1 deadline
- `fpl-squad-optimiser-spec.md` — full MILP formulation for the future optimiser
- `fpl-data-source-assessment.md` — why Core Insights was chosen, and its limits
- `adr/0005-scoring-validation.md` — the scoring-engine validation results and defensive-contribution gotcha
- `deploy-self-hosted.md` — the Fly/Dagster deployment guide (matches current `fly.toml`/`Dockerfile`)
- `fpl-api-deployment-strategy.md` — ops philosophy, including the deploy-freeze-around-deadlines idea (not yet enforced in CI)
- `fpl-api-development-strategy.md` — solo-dev methodology, toolchain rationale, and a tiered test-data strategy with a specific list of edge cases a future "golden mini-season" fixture should cover

I'd restore these to disk (or at least don't let them get committed as deleted) — flag it to
whoever owns this working tree state before it's committed away.

## Suggested reading order for a new contributor

1. This file, then `git checkout HEAD -- docs/` and read `fpl-api-roadmap.md` first for the big
   picture.
2. `packages/fpl-core/src/fpl_core/scoring.py` + the ADR — smallest, most settled piece.
3. `packages/fpl-ingestion/src/fpl_ingestion/definitions.py` — read this like a table of contents
   for the whole ingestion package, then dip into the individual modules it wires together.
4. `just bootstrap && just dev` — open the Dagster UI and look at the actual asset graph while
   cross-referencing `definitions.py`.
