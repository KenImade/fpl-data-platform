# FPL API — Development Strategy

**Companion to:** `fpl-api-concept.md`, `fpl-squad-optimiser-spec.md`
**Date:** July 2026

---

## 1. The constraints that actually shape this

Before any tooling decision, three facts dominate:

**You are one person with a full-time job.** Realistic capacity is 8–12 hours a week, in fragments. Every process, tool, and abstraction has to justify itself against that budget. Anything that needs weekly maintenance to keep working will decay, and then you'll distrust it, and then you'll ignore it — which is worse than never having built it.

**You cannot develop this against the live API.** The FPL API serves only the current season, in its current state, and it changes under you. You cannot test a deadline snapshot on a Tuesday, or a double gameweek in a week that doesn't have one, or the season rollover in November. This is the single biggest difference between this project and a normal CRUD backend, and §3 is dedicated to it.

**The football calendar is your release calendar.** There are windows where a deploy is harmless and windows where it's unforgivable. That belongs in CI, not in your head — see the deployment document.

---

## 2. Repository and toolchain

### Monorepo

One repository. You're one person, the dbt models and API response schemas are tightly coupled, and a change to the ruleset config touches the pipeline, the models and the optimiser simultaneously. Splitting repos buys you nothing and costs you atomic commits across those boundaries.

```
fpl-api/
├── justfile                 # the single task interface
├── compose.yaml             # local Postgres, MinIO, Redis, Redpanda
├── pyproject.toml           # uv workspace root
├── .python-version
├── packages/
│   ├── fpl-core/            # shared: rulesets, domain types, identity
│   ├── fpl-ingestion/       # Dagster assets, clients, validators
│   ├── fpl-modelling/       # features, match model, player models
│   ├── fpl-optimiser/       # MILP formulation and solver wrapper
│   └── fpl-api/             # FastAPI service
├── transform/               # dbt project
├── rulesets/                # versioned scoring + optimiser config
├── sdks/                    # generated TypeScript and Python clients
├── infra/                   # Terraform, fly.toml, deploy scripts
├── tests/
│   ├── fixtures/            # recorded API responses
│   └── golden/              # curated mini-season dataset
└── docs/
    ├── adr/                 # architecture decision records
    └── runbooks/
```

`fpl-core` existing separately is the one non-obvious call. The ruleset loader, the position enum, the `person_id` resolution logic and the points calculator are used by ingestion, modelling, the optimiser and the API. Duplicating them is how you end up with an optimiser that scores goals differently from the pipeline.

### Toolchain

| Tool | Purpose | Why |
|---|---|---|
| **mise** | Python, Node, Go version pinning | Single tool, single config file, works cleanly on Arch |
| **uv** | Python packaging, workspace, locking | Fast enough that you'll actually use it; workspace support fits the monorepo |
| **direnv** | Auto-load `.env` on `cd` | You've been through this on the Go project — same pattern here |
| **just** | Task runner | Discoverable (`just --list`) in a way that shell scripts and Makefiles are not |
| **ruff** | Lint + format | One tool replacing four, fast enough for pre-commit |
| **mypy** | Type checking, strict on `fpl-core` and `fpl-api` | Catches the class of bug that hides in data pipelines for weeks |
| **sqlfluff** | dbt SQL linting | |
| **Docker Compose** | Postgres, MinIO, Redis, Redpanda | Mirrors production services |

### The one-command rule

```bash
just bootstrap    # install toolchain, deps, start services, seed golden data, run migrations
just test         # full local suite in under 60 seconds
just dev          # API + Dagster webserver, hot reload
```

If `just bootstrap` doesn't produce a working environment on a clean machine, fix that before writing another feature. You will reformat this laptop, or work from a different one, or come back after a six-week gap having forgotten everything. The bootstrap script is a note to your future self, and it's the highest-leverage hour you'll spend.

---

## 3. The development data problem

This is where most of the thought should go.

### Why you can't just call the API

| Problem | Consequence |
|---|---|
| Only serves the current season | Cannot develop or test historical logic |
| Mutates continuously | Tests are non-deterministic; a test that passed this morning fails this afternoon |
| Undocumented rate limits | A test suite that hammers it gets you blocked |
| No way to summon edge cases | Cannot test a double gameweek, a blank, or a mid-season transfer on demand |
| Season rollover happens once a year | The riskiest code path is the least testable |

### Tiered test data

| Tier | What | Size | Runtime | Runs |
|---|---|---|---|---|
| **1. Hand fixtures** | Small hand-written JSON for unit tests | KB | ms | Every save |
| **2. Golden mini-season** | Curated real data: 1 season, ~50 players, ~6 clubs | ~5 MB, in repo | seconds | Every commit, CI |
| **3. Full historical** | All seasons in a local Postgres | ~500 MB | minutes | Nightly, pre-release |
| **4. Production replay** | Replay real bronze snapshots through the pipeline | GB | tens of min | Before risky releases |

### The golden mini-season

The most valuable artefact in the repository, and worth building deliberately rather than by sampling at random. Curate it to contain every case that has ever broken something:

- A **double gameweek** and a **blank gameweek**
- A player who **transferred between clubs mid-season**
- A player **reclassified** from midfielder to forward
- A player with a **long injury** — flagged, then unflagged
- A **promoted club** with no prior top-flight history
- Two players sharing a **surname at the same club**
- A player whose name is **spelled differently across FPL, FBref and Understat** (accents, name order)
- A **price rise and fall** crossing a threshold, to exercise sell-price arithmetic
- A player who scored **0 minutes all season**
- A **red card** and a **penalty miss**, for the rarer scoring paths

Every time production surprises you, add the case to the golden set and write the test. Over two seasons this becomes a genuinely strong regression suite, built from real failures rather than imagined ones.

### Recorded HTTP fixtures

Use a VCR-style library (`vcrpy`, or `respx` with saved payloads) so ingestion tests replay recorded responses. Record once, commit the cassettes, run offline forever.

Two rules:

1. **Sanitise on record.** Strip anything that identifies a real manager — team names, entry IDs, league names. FPL's public data includes real people.
2. **Re-record deliberately, never automatically.** `just record-fixtures` is a manual command whose diff you read. An auto-refreshing cassette silently absorbs an upstream schema change and your tests go green on data that would break production.

### The schema-drift canary

The one thing that *should* hit the live API on a schedule — and it's not a test, it's a monitor.

A nightly job fetches `bootstrap-static` and `fixtures`, validates against the expected schema, and reports:

- Fields added
- Fields removed
- Fields whose type changed
- Enum values not seen before (new chip names, new player statuses)
- Row-count deltas outside expectation

It never fails the build — it opens an issue or posts to a channel. FPL changes shape between seasons and occasionally mid-season, and finding out from a canary in August beats finding out from a broken pipeline the night before the first deadline.

---

## 4. Testing strategy

A data and ML system needs a different shape of test suite from a normal application. Correctness failures here are silent: nothing crashes, the numbers are just wrong.

| Layer | Tool | What it protects |
|---|---|---|
| **Pure transforms** | pytest + hand fixtures | Ruleset scoring, per-90 arithmetic, rolling windows, sell-price rules |
| **Property tests** | hypothesis | Invariants: points are never negative for a player who didn't play; per-90 rates are undefined at 0 minutes rather than infinite |
| **dbt tests** | dbt + dbt-expectations | Grain uniqueness, referential integrity, non-null keys, accepted ranges, row-count deltas |
| **Ingestion contracts** | pydantic/pandera against cassettes | Upstream shape assumptions |
| **Identity resolution** | pytest against golden set | Unmapped players above a minutes threshold = build failure |
| **API contracts** | schemathesis against OpenAPI | Response shape, status codes, fuzzing |
| **Optimiser** | Independent rule validator + hypothesis | Every returned plan is legal; free transfers can't be manufactured; budget never negative |
| **Model regression** | Custom harness | Metrics on a frozen backtest must not degrade beyond tolerance |
| **End-to-end** | pytest + ephemeral Postgres | Bronze JSON in → correct API response out |

### The three that matter most

**The independent optimiser validator.** Written without reference to the solver code, ideally on a different day, checking any plan against the ruleset from scratch. Run it in production on every solve, not just in tests. An optimiser bug produces a plausible-looking illegal plan, and nothing anywhere will complain.

**Point-in-time leakage tests.** For a sample of historical gameweeks, assert that regenerating a prediction from its stored `feature_snapshot_id` reproduces the stored prediction exactly. If it doesn't, either you're leaking or your snapshots are incomplete. This single test is what makes your backtest numbers believable — including to yourself, in six months, when the model is doing something surprising.

**Model regression gates.** Every model change runs the full walk-forward backtest and compares against the current production model. Rank correlation, calibration, and simulated squad performance. A pull request that degrades any of them beyond tolerance fails CI. Without this, model development becomes a series of changes you believe helped.

### What not to test

Skip coverage targets. Skip testing that FastAPI routes exist. Skip mocking Postgres — use a real ephemeral one, it's fast and the mocks lie. Your budget is limited; spend it on correctness of numbers, not on ceremony.

---

## 5. Branching, review and cadence

### Trunk-based, short branches

Solo, so long-lived branches are pure cost — you're merging with yourself and losing. Branch, ship within a couple of days, merge. Anything longer goes behind a feature flag (a simple config table or environment variable; you do not need a flag platform).

**Review yourself asynchronously.** Open the PR, walk away, read the diff the next morning as if someone else wrote it. Silly on paper, genuinely effective in practice, and it's the only review you're going to get. Use the PR description as the place to explain *why* — that's the artefact you'll actually reread.

### Vertical slices

Ship thin end-to-end paths rather than complete layers. "Player list endpoint working in production, three fields only" beats "entire silver layer complete, nothing deployed." Each slice must reach production and be observable before the next one starts. WIP limit of one, strictly.

The build order in the concept doc is already sliced this way — Phase 1b deploys a boring data API before any modelling exists, deliberately.

### The seasonal work calendar

The football calendar has more effect on your plan than your sprint cadence does.

| Period | Character | What to do |
|---|---|---|
| **June – mid July** | Off-season. No fixtures, no deadlines, no users depending on freshness. | The **only** safe window for breaking changes, major refactors, database migrations, dependency major bumps, infrastructure moves. Plan the year around this window. |
| **Mid July – mid Aug** | Pre-season. New season data appears, element IDs reset, promoted clubs arrive, rules may change. | Highest-risk period. Run the season-rollover runbook. Verify rulesets. Rebuild identity mappings. No feature work. |
| **Aug – May** | In season. Weekly deadlines, live traffic, users depending on you. | Additive changes only. New endpoints, model improvements behind flags, bug fixes. Nothing that touches the schema of an existing response. |
| **International breaks** | Two-week gaps with no fixtures, several times per season | The in-season equivalent of a maintenance window. Save moderately risky changes for these. |

Put the current window in the repo README and have CI warn on migration files added during the season. It sounds heavy-handed; it will save you at least once.

---

## 6. Schema and migrations

Two distinct categories of database schema, and confusing them causes real damage:

| Category | Owner | Tool | Examples |
|---|---|---|---|
| **Application state** | The API | Alembic | API keys, optimisation jobs, users, rate-limit counters, audit log |
| **Analytical models** | The pipeline | dbt | `dim_player`, `fct_player_gw`, `fct_prediction`, all marts |

**Keep them in separate Postgres schemas with separate database roles.** The API's role has `SELECT` only on the analytical schemas and full rights on its own. This makes it structurally impossible for an API bug to corrupt the warehouse — a guarantee worth far more than the fifteen minutes it costs to set up.

### Expand-contract, always

Never rename or drop a column in one step, even solo, even with no external consumers yet. The API and pipeline deploy independently and there is always a window where old code meets new schema.

1. **Expand** — add the new column, write to both
2. **Migrate** — backfill, switch reads to the new column, deploy
3. **Contract** — stop writing the old column, deploy, then drop it in a later release

Contract steps go in the off-season backlog.

### Atomic mart swaps

A dbt run that fails halfway leaves your marts inconsistent, and the API is reading them live. Build into a staging schema and swap:

```sql
-- dbt builds into marts_build
BEGIN;
ALTER SCHEMA marts       RENAME TO marts_old;
ALTER SCHEMA marts_build RENAME TO marts;
COMMIT;
-- drop marts_old after a grace period
```

A broken pipeline run then serves stale data rather than wrong data. Stale is recoverable; wrong is not, because users have already acted on it.

---

## 7. Configuration and secrets

**Rulesets are code**, versioned in `rulesets/`, loaded by `fpl-core`, referenced by ID on every historical row. Never read scoring values from the live API into logic — read them, then *diff them against your ruleset file* and alert on mismatch. That's how you find out FPL changed the rules.

**Secrets never enter the repository.** Local via `direnv` and a gitignored `.env`, with a committed `.env.example` listing every required variable. Production via the platform's secret store. `git-secrets` or `gitleaks` in pre-commit.

**API keys are hashed at rest.** These are high-entropy random strings, not passwords, so a fast hash (SHA-256) is correct — bcrypt or argon2 buys you nothing against a 32-byte random secret and costs latency on every request. Store a short prefix in plaintext so users can identify their own keys in a dashboard.

---

## 8. CI

Keep the PR path under five minutes or you will start skipping it.

**On pull request:**
```
ruff check + format --check
mypy (fpl-core, fpl-api, fpl-optimiser)
sqlfluff lint
pytest -m "not slow"              # tiers 1 and 2
dbt parse + dbt compile
schemathesis against generated OpenAPI
docker build (cache-mounted, not pushed)
gitleaks
```

**On merge to main:**
```
full pytest including integration against ephemeral Postgres
dbt build against golden mini-season
optimiser property + validator suite
build and push image, tagged with commit SHA
deploy to staging, smoke test
```

**Nightly:**
```
FPL schema-drift canary
full historical rebuild from bronze (tier 3) — catches transformation regressions
model regression backtest, metrics posted to dashboard
dependency audit
```

**Weekly:**
```
batched dependency updates (Renovate, grouped into one PR)
```

Batching dependency updates matters more than it sounds. Individual Dependabot PRs will consume your entire weekly budget in review overhead, and you'll start merging them unread — which is worse than not having them.

---

## 9. Documentation you'll actually maintain

Three kinds, and nothing else:

**Architecture Decision Records.** One markdown file per significant decision: context, options, choice, consequences. Ten to twenty lines. You will not remember in March why you chose HiGHS over CP-SAT, or why `dim_person` is separate from `dim_player`, and reconstructing that reasoning costs hours.

**Runbooks.** Notes to yourself at 23:00 when something is broken and a deadline is in four hours. Listed in the deployment document. Write each one the first time you perform the procedure manually, while it's fresh.

**The public API docs.** Generated from OpenAPI, with hand-written guides for the non-obvious parts: authentication, the prediction response shape, what `feature_snapshot_id` means, and how to read the optimiser's `preference_cost`. These are for your consumers and they're part of the product.

Everything else — data dictionaries, model documentation, pipeline lineage — should be **generated** from dbt docs and Dagster's asset graph, not hand-written. Hand-written documentation of a system that changes weekly is a lie with a timestamp on it.

---

## 10. The realistic first eight weeks

Assuming roughly ten hours a week, and noting that you're starting in the off-season, which is the best possible timing.

| Weeks | Focus | Done when |
|---|---|---|
| 1 | Repo scaffold, `just bootstrap`, Compose stack, CI skeleton | Clean machine to running tests in one command |
| 2 | `fpl-core`: rulesets, domain types, points calculator, full unit tests | Recomputed points match archived totals on the golden set |
| 3 | Ingestion + cassettes + schema validation + bronze writes | Raw payloads landing in MinIO, replayable offline |
| 4 | Identity: `dim_person`, name matching, mapping seed, failing test | Zero unmapped players above the minutes threshold |
| 5–6 | dbt staging → intermediate → marts, historical backfill 2017/18+ | Full history in Postgres, all dbt tests green |
| 7 | FastAPI: players, teams, fixtures, gameweeks, `/bootstrap`, auth, OpenAPI | Deployed, reachable, with a docs page |
| 8 | Deadline snapshot job, backtest harness, baselines | Can honestly evaluate a model that doesn't exist yet |

At week eight you have no predictions and no optimiser — and you have a deployed, tested, historically complete data API with an evaluation harness ready. That is the right trade. Every FPL project that starts with the model instead ends up with a notebook and no product.
