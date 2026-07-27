# FPL API — Build Roadmap & Checklist

**Companion to:** `fpl-api-concept.md`, `fpl-squad-optimiser-spec.md`, `fpl-api-development-strategy.md`, `fpl-api-deployment-strategy.md`
**Starting:** Late July 2026
**Capacity assumption:** ~10 hours/week, solo, alongside full-time work

---

## 0. The timing problem — read this first

You are starting roughly two to three weeks before the 2026/27 season begins. That timing has one urgent consequence and one liberating one.

**The urgent one: FPL data is perishable.** The live API serves only the current season and overwrites state in place. If you don't capture 2026/27 as it happens, you cannot reconstruct it later — community archives give you end-of-gameweek snapshots at best, never the pre-deadline state that point-in-time correctness requires. Every gameweek you don't capture is a gameweek permanently missing from your best training data.

**The liberating one: you are not launching this season.** Trying to ship predictions for 2026/27 would mean rushing the data layer, skipping the point-in-time work, and producing a model you can't trust. Instead, treat 2026/27 as a **free, high-quality data collection year** — you build against history, validate against a live season in real time, and launch properly for 2027/28.

That reframing is what makes the plan realistic instead of frantic.

**Therefore Phase 0 exists**, it is tiny, and it must ship before the first deadline. Everything else can slip.

---

## Phase 0 — Capture the perishable data
**Hard deadline: Friday 21 August 2026 — GW1. That is 25 days from today.**
**Effort: one evening. Maybe two.**

This is deliberately crude. No Dagster, no dbt, no Postgres, no tests, no architecture. A script and a cron.

- [ ] **Confirm the exact GW1 deadline time.** The date is 21 August 2026; FPL deadlines land roughly 90 minutes before the first kickoff, so expect late afternoon UTC. Verify from `bootstrap-static` once the season loads.
- [ ] Create a Cloudflare R2 bucket (or any S3-compatible store) with object versioning enabled
- [ ] Write a single script that fetches and stores, gzipped, keyed by UTC timestamp:
  - [ ] `/bootstrap-static/`
  - [ ] `/fixtures/`
  - [ ] `/event-status/`
- [ ] Key layout: `raw/{endpoint}/{YYYY-MM-DD}/{HH-MM-SS}.json.gz`
- [ ] Honest `User-Agent` with contact details
- [ ] Retry with exponential backoff; never hammer on failure
- [ ] Deploy to the cheapest thing that runs a schedule reliably (a scheduled function, a tiny VPS cron, a GitHub Actions cron)
- [ ] Schedule: **every 3 hours normally, every 15 minutes in the 6 hours before each deadline**
- [ ] Add a dead-simple failure alert — an email on non-zero exit is enough
- [ ] Verify objects are actually landing, twice, on different days
- [ ] Set a calendar reminder to check it weekly for the first month

**Also in Phase 0 — mirror FPL-Core-Insights.** Five more lines, and it removes your single-maintainer dependency risk:

- [ ] Twice-daily `git pull` of `olbauday/FPL-Core-Insights` (shallow clone; they refresh at 07:30 and 17:30 UTC)
- [ ] Copy `data/{season}/` into your bronze, keyed by fetch timestamp
- [ ] Verify the mirror captures both the master files and the `By Gameweek/` snapshots

The repo is a few tens of megabytes per season. Mirroring means that if it stops updating or disappears, you lose future data but never past data. See `fpl-data-source-assessment.md`.

**Exit criteria:** raw snapshots accumulating in object storage, unattended, with an alert if they stop.

> Resist every urge to make this good. It is a bucket and a cron job. Its entire value is that it exists before GW1, and any hour spent improving it is an hour not spent on Phase 1. You will throw this script away in Phase 3 and that is fine.

**Optional if you have a second evening:** add per-player `/element-summary/{id}/` collection, weekly, spread over an hour to stay polite. Nice to have, not urgent — this data is reconstructible from the per-gameweek `live` endpoint later.

---

## Phase 1 — Foundations
**Target: August–September 2026 · ~6 weeks · ~60 hours**

### 1a. Repository and environment
- [ ] Monorepo scaffold per the development doc's layout
- [ ] `mise` config pinning Python, Node, Go versions
- [ ] `uv` workspace with the five packages
- [ ] `compose.yaml`: Postgres, MinIO, Redis
- [ ] `justfile` with `bootstrap`, `test`, `dev`, `lint`
- [ ] `direnv` + committed `.env.example`
- [ ] Pre-commit: ruff, gitleaks
- [ ] **Verify `just bootstrap` works on a clean machine.** Actually test this, don't assume it.
- [ ] `docs/adr/0001-monorepo.md` — start the habit immediately

### 1b. First deployment (do this before there is anything to deploy)
- [ ] `/health` endpoint returning status and commit SHA
- [ ] Dockerfile: multi-stage, slim base, non-root
- [ ] CI on PR: lint, type check, test, build
- [ ] CI on main: build, push image tagged by SHA, deploy
- [ ] Deployed to production behind Cloudflare, on a real domain, with TLS
- [ ] Uptime monitoring pointed at `/health`
- [ ] Sentry wired up; trigger a test error deliberately
- [ ] **Perform a rollback on purpose and confirm it works**

### 1c. `fpl-core`
- [ ] Domain types: `Position`, `Player`, `Team`, `Gameweek`, `Fixture`
- [ ] Ruleset loader with `rulesets/2026-27.yml`
- [ ] Points calculator driven entirely by the ruleset
- [ ] Sell-price calculator (purchase price + half profit, rounded)
- [ ] Unit tests including every rare scoring path: red cards, own goals, penalty saves, penalty misses, goalkeeper saves
- [ ] **Validation test: recomputed points match archived totals** across a full historical season

### 1d. Golden mini-season
- [ ] Pull a historical season from community archives
- [ ] Curate down to ~6 clubs, ~50 players, one full season
- [ ] Verify it contains every edge case in the development doc's list — double gameweek, blank, mid-season transfer, position reclassification, long injury, promoted club, shared surname, cross-source name mismatch, price threshold crossing, red card, penalty miss
- [ ] Commit to `tests/golden/` — target under 10 MB
- [ ] Document in `tests/golden/README.md` which case each fixture covers

**Phase 1 exit criteria:** clean machine → working environment in one command; a health endpoint deployed through real CI/CD with a proven rollback; points calculation verified against a real season; a golden dataset that makes tests fast and deterministic.

---

## Phase 2 — Data layer
**Target: October–November 2026 · ~8 weeks · ~80 hours**

### 2a. Proper ingestion
- [ ] Dagster project with assets for each source
- [ ] Typed FPL client with retries, backoff, rate limiting
- [ ] Pydantic schema validation — **fail loudly, never coerce silently**
- [ ] Bronze writes to R2, partitioned `source/season/ingested_at`
- [ ] VCR cassettes recorded and committed; `just record-fixtures` as a manual command
- [ ] **Migrate the Phase 0 cron into Dagster and backfill everything it collected**
- [ ] Schema-drift canary running nightly, reporting to a channel

### 2b. Identity — substantially reduced by FPL-Core-Insights
Match stats arrive pre-aligned to FPL `player_id`, and `player_code` is stable across seasons. The fuzzy-matching project this used to be is mostly gone.

- [ ] `dim_person` keyed off `player_code` — keep the abstraction, skip the hard part
- [ ] Map Core Insights `player_id` → `person_id` (direct join)
- [ ] Map vaastav historical rows → `person_id` via `player_code`
- [ ] Reconcile any `player_code` values appearing in one source but not the other
- [ ] Small manual mapping seed for genuine residuals only
- [ ] **dbt test that fails the build** on any unmapped player above a minutes threshold
- [ ] Verify against the golden set's deliberately awkward names

### 2c. Transformation
- [ ] dbt project: `staging` → `intermediate` → `marts`
- [ ] SCD Type 2 on `dim_player`
- [ ] `fct_player_gw` grained at **player × gameweek × fixture** (double gameweeks depend on this)
- [ ] `fct_player_price` as a separate fact
- [ ] Ruleset-versioned points recomputation, cross-checked against archived totals
- [ ] dbt tests: uniqueness on grain, referential integrity, non-null keys, accepted ranges
- [ ] Atomic mart swap via schema rename
- [ ] `just rebuild-from-bronze` — and a nightly CI job that proves it works

### 2d. Historical backfill — three tiers, kept explicitly separate
- [ ] **Deep tier:** vaastav 2016/17 → 2024/25. FPL fields only, no match stats. Trains the team-level match model and provides long-run priors.
- [ ] **Rich tier:** FPL-Core-Insights 2024/25 → present. Full match statistics, xG, defensive actions, Elo.
- [ ] **Write a per-season layout adapter.** 2024/25 uses `data/{season}/{table}/{table}.csv`; 2025/26+ uses `data/{season}/{table}.csv` plus `By Gameweek/`. Column counts differ too — `playerstats` 58 vs 87, `matches` 102 vs 115, `playermatchstats` 54 vs 64.
- [ ] Watch the `defensive_contribution` / `defensive_contributions` singular-plural split across tables
- [ ] **Reconstruct defensive contribution for 2024/25** from its CBIT components (all present in `playermatchstats`), doubling training data for the highest-value feature
- [ ] **dbt test:** recompute DefCon from components for 2025/26 and reconcile against the provided aggregate — free validation of the 2024/25 reconstruction
- [ ] **Record feature availability per season as first-class metadata.** 2024/25 has no `news`/`news_added` at all; a model must never train on a column null-filled because the source didn't have it.
- [ ] Add a dbt test asserting no model input is used outside its declared coverage window
- [ ] Reconcile: recomputed points vs archived totals, per season, with a divergence report
- [ ] Investigate every divergence — each is a bug in your ruleset or in the archive
- [ ] Cross-check seasons covered by both sources against each other — free validation of both

**Phase 2 exit criteria:** nine seasons of history in Postgres, all dbt tests green, zero unmapped players, warehouse rebuildable from bronze on demand, and 2026/27 flowing in live through the real pipeline.

---

## Phase 3 — Data API (first public milestone)
**Target: December 2026 · ~4 weeks · ~40 hours**

- [ ] FastAPI service reading gold tables
- [ ] Endpoints: `/players`, `/players/{id}`, `/players/{id}/history`, `/teams`, `/fixtures`, `/gameweeks`
- [ ] Compound `GET /v1/bootstrap` for web app initialisation
- [ ] Sparse fieldsets (`?fields=`) and bulk-by-ID (`?ids=`)
- [ ] Cursor pagination
- [ ] Publishable / secret key split, with origin allowlist enforcement
- [ ] Redis token-bucket rate limiting
- [ ] RFC 9457 problem-details errors, consistently
- [ ] ETag and Cache-Control, with a match-window-aware cache profile
- [ ] Cloudflare caching rules configured and verified
- [ ] OpenAPI spec served; schemathesis in CI
- [ ] Generated TypeScript SDK, published
- [ ] Docs site with runnable examples and a sandbox key
- [ ] Least-privilege database roles enforced
- [ ] Deadline-aware alerting wired up
- [ ] Runbooks written: schema change, pipeline failure, rebuild, rollback

**🎯 MILESTONE 1 — Ship it publicly.** Post it to PyData Lagos, to r/FPL, to the FPL developer community. Real users find real bugs, and a live, boring data API is a genuine portfolio artefact well before any model exists.

---

## Phase 4 — Point-in-time infrastructure
**Target: January 2027 · ~3 weeks · ~30 hours**

The least visible phase and the one that determines whether anything after it is trustworthy. **Do not skip ahead to modelling.**

- [ ] Deadline-triggered snapshot job (deadline − 5 min), scheduled from your own fixtures table
- [ ] `feature_snapshot_id` generation and storage
- [ ] Snapshot completeness validation — every model input captured
- [ ] Feature builders read exclusively from snapshots, never live tables
- [ ] **Leakage test:** regenerating a prediction from its snapshot ID reproduces it exactly
- [ ] Walk-forward backtest harness
- [ ] Baselines implemented: FPL's own `ep_next`, trailing points-per-game, ownership-weighted, ICT index
- [ ] Metrics: Spearman by position, NDCG@15, Brier, log loss, calibration curves
- [ ] Backtest results dashboard

**Exit criteria:** you can evaluate a model that doesn't exist yet, and prove your evaluation isn't lying to you.

---

## Phase 5 — Baseline predictions
**Target: February–March 2027 · ~6 weeks · ~60 hours**

Build in this order. Each is independently testable and the ordering is not arbitrary. Note that xG/xA ingestion has already arrived free via FPL-Core-Insights, and ClubElo ratings give the match model a ready-made prior including promoted clubs.

- [ ] **Minutes model** — three-class: no appearance / cameo / 60+. Highest leverage; everything scales by it. Use `start_min`/`finish_min`, not just minutes — a 60th-minute substitution and a 60-minute start are the same scalar and completely different signals.
- [ ] **Match model** — Dixon-Coles on team xG with time decay, producing full score matrices
- [ ] Clean sheet probabilities and goals-conceded distributions from the score matrix
- [ ] **Player involvement** — goal and assist shares with hierarchical shrinkage toward positional means
- [ ] **Bonus** — start with regression on expected involvement; upgrade to BPS simulation later
- [ ] **Defensive contribution model** (verify current rules first)
- [ ] Points assembly through the ruleset config
- [ ] Distributional outputs: p10 / p50 / p90 via Monte Carlo
- [ ] Model registry with versions, training config, metrics
- [ ] **Beat `ep_next` on rank correlation — or find out precisely why you can't**
- [ ] Model regression gate in CI
- [ ] Prediction endpoints with full provenance in `meta`
- [ ] Feature drift monitoring

**🎯 MILESTONE 2 — Predictions live.** Publish your backtest honestly, including where you lose to baselines. That honesty is more persuasive than good numbers.

---

## Phase 6 — Optimiser
**Target: April–May 2027 · ~8 weeks · ~80 hours**

Per the optimiser spec's build order. Note that steps 1–3 are independently shippable.

- [ ] **Build mode, single gameweek** — composition constraints only
- [ ] **Independent rule validator**, written fresh, run on every solve in production
- [ ] **Hard preferences in build mode** — pins, club floors and ceilings
- [ ] **Pre-flight feasibility checks** with specific, actionable error messages
- [ ] **Cost-of-bias report** via repeated solves

**🎯 MILESTONE 3a — "Best £100m squad containing Saka and two Chelsea starters."** Genuinely useful, and shippable months before transfer machinery exists.

- [ ] Build mode over a multi-gameweek horizon (discount factor, bench weighting)
- [ ] Transfer mode: continuity, hits, free-transfer rollover
- [ ] **Property tests for rollover written before the constraint** — the naive formulation manufactures free transfers
- [ ] Sell-price path dependence, verified against a real squad's FPL-reported values
- [ ] Pin auto-relaxation on unavailability, with loud reporting
- [ ] IIS-based infeasibility explanation
- [ ] Alternative plans and margin reporting
- [ ] Async job API with secret-key-only access
- [ ] Candidate pre-filtering, including cheap enablers
- [ ] **Season-long backtest of the optimiser** against no-transfers, greedy, and template baselines

**🎯 MILESTONE 3b — Full optimiser live.**

---

## Phase 7 — Season rollover and hardening
**Target: June–July 2027 · the off-season · ~6 weeks**

The only wide-open window in the calendar. Spend it deliberately.

- [ ] Execute the season rollover runbook for 2027/28
- [ ] Archive the final 2026/27 snapshot before the API resets
- [ ] Retrain on the complete 2026/27 season — **the first season you captured properly, point-in-time**
- [ ] All deferred contract migrations (dropped columns, removed tables)
- [ ] Major dependency upgrades
- [ ] Quarterly restore drill
- [ ] Load test against expected deadline-hour traffic
- [ ] Documentation and SDK refresh
- [ ] Decide on a paid tier; if yes, resolve payment processing and entity structure

**🎯 MILESTONE 4 — Full launch for the 2027/28 season**, with a complete data API, a validated prediction model trained on properly captured data, and a working optimiser.

---

## Phase 8 — Deferred
Only after the above, and only if genuinely wanted:

- [ ] Live gameweek path (Redpanda or Redis Streams, SSE fanout, provisional bonus)
- [ ] Chips in the optimiser
- [ ] Scenario-based stochastic optimisation
- [ ] Manager and mini-league endpoints
- [ ] Go extraction of the live service — **only when SSE connection count actually demands it**

The live path is deliberately last. It's the most operationally demanding piece and the least differentiating, since several sites already do live points well.

---

## Cut list

If capacity runs short, cut in this order. Nothing above the line is optional.

| Cut | Consequence |
|---|---|
| Scenario-based optimisation | Slightly worse risk handling |
| Bonus via BPS simulation (keep the regression) | Modestly worse bonus predictions |
| Free Hit chip support | Users plan that one manually |
| Preview environments per PR | Slower feedback |
| Python SDK (keep TypeScript) | Fewer consumers served |
| Historical seasons before 2019/20 | Less training data, faster backfill |
| Defensive contribution model | Loses probably the highest-value edge available |
| ───────────────────────── | |
| Point-in-time infrastructure | **Nothing downstream is trustworthy** |
| Independent optimiser validator | Silent illegal plans in production |
| Identity resolution tests | Silently missing xG, unexplainable model behaviour |
| Model regression gates | Model quality drifts without anyone noticing |

---

## Traps

Each of these has ended an FPL project that had every chance of working.

**Starting with the model.** It's the fun part and it produces a notebook. Data layer first, evaluation harness second, model third. The ordering in this document is deliberate.

**Skipping point-in-time correctness because the model looks great.** It looks great *because* it's leaking. You'll discover this in production, after telling people your numbers.

**Perfecting Phase 0.** It's a bucket and a cron. Ship it ugly, before GW1.

**Building a web app instead of the API.** The API is the product. A client is a distraction until Milestone 1 is live.

**Reaching for bigger infrastructure.** This is a few hundred thousand rows. Any architecture decision justified by data volume is wrong — you have none.

**Deploying during a deadline window.** Encode the freeze in CI in Phase 1b, while it costs nothing.

**Silence.** Ten hours a week over a year is a long stretch with no feedback. The three milestones exist to break that up. Ship at each one.

---

## Weekly operating rhythm

Roughly ten hours, in fragments:

| When | Duration | What |
|---|---|---|
| Weekday evenings ×2 | 90 min each | Focused build on the current slice |
| Weekend block | 4 hours | The work that needs sustained attention |
| Weekend | 30 min | Check pipeline health, triage alerts, read the cost digest |
| Fortnightly | 30 min | Review the roadmap, close or open ADRs, reassess |

**WIP limit of one.** One slice at a time, all the way to production, before the next begins.

**Keep an impact log** alongside the ADRs — what shipped, what broke, what you learned. It's the raw material for a PyData Lagos talk, and writing it down as you go is enormously easier than reconstructing it in a year.

---

## The next four things

Concretely, in order, starting now:

1. **Verify the GW1 deadline date.** Everything is scheduled relative to it.
2. **Write and deploy the Phase 0 capture script.** One evening. Before that deadline.
3. **Confirm it's collecting**, on two separate days.
4. **Then, and only then**, start Phase 1a.

Phase 0 is the only genuinely time-critical item in this entire document. Miss it and you lose a season of point-in-time data that no archive can give you back. Everything after it can slip by weeks without real cost.
