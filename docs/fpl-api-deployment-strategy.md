# FPL API — Deployment & Operations Strategy

**Companion to:** `fpl-api-concept.md`, `fpl-api-development-strategy.md`
**Date:** July 2026

---

## 1. Operating principle

**Every piece of infrastructure is something that can wake you up.** You are one person with a day job, and the system has hard external deadlines you don't control. Optimise for the smallest operational surface that meets the requirement, not for the most sophisticated architecture you could justify.

Three consequences, stated up front so the rest of the document doesn't have to re-argue them:

- **No Kubernetes.** Not now, not at ten times this scale. It is a full-time job disguised as a deployment target, and nothing here needs it.
- **Managed Postgres, not self-hosted.** Bronze in object storage means you can rebuild the warehouse from scratch — but you cannot rebuild user API keys, job history, or billing records. Pay someone to keep that safe.
- **The CDN does most of your scaling.** This data is extraordinarily cacheable outside match windows. Edge caching will serve the large majority of requests and flatten the deadline spike, letting you run an origin far smaller than the traffic numbers suggest.

---

## 2. Environments

A full permanent staging environment doubles your cost and — more importantly — doubles your maintenance. But deploying pipeline changes straight to production is how you corrupt a warehouse.

The compromise:

| Environment | What runs | Data | Lifetime |
|---|---|---|---|
| **Local** | Everything, via Compose | Golden mini-season + optional full history | Permanent |
| **Preview** | API only, per pull request | Reads production marts **read-only** | Ephemeral, torn down on merge |
| **Staging** | Full pipeline + API | Weekly restored snapshot of production | Permanent but tiny; scaled to zero between uses |
| **Production** | Everything | Live | Permanent |

**Preview environments are cheap and worth it** for API changes, because the API is read-only against marts. A pull request spins up a container pointed at production's read replica, and you get a real URL to click before merging. This covers the large majority of your changes.

**Staging exists for pipeline and migration changes only.** It runs the same dbt project against a restored snapshot. Spin it up before a risky release, verify, spin it down. Automating that as `just staging up` / `just staging down` keeps the cost near zero and means you'll actually use it.

---

## 3. Infrastructure

### Recommended topology

| Component | Choice | Notes |
|---|---|---|
| **API service** | Container on a managed PaaS (Fly.io or Render), 2 instances | Stateless, rolling deploys, autoscale on the deadline schedule |
| **Warehouse + app DB** | Managed Postgres with point-in-time recovery | Separate schemas, separate roles (see §6) |
| **Bronze object storage** | **Cloudflare R2** | S3-compatible, and critically **no egress fees** — you will re-read bronze constantly for rebuilds and backtests |
| **Cache / rate limits** | Managed Redis | Small instance is plenty |
| **Orchestration** | Dagster: webserver + daemon + run workers, containerised | Run workers scale to zero between schedules |
| **Optimiser workers** | Separate worker pool consuming a queue | CPU-bound; must not compete with API request handling |
| **CDN + DNS + WAF** | Cloudflare | Edge caching, DDoS protection, origin shielding |
| **Errors** | Sentry | Free tier is sufficient at this scale |
| **Metrics + logs** | Grafana Cloud or Better Stack | Free tiers are generous; OpenTelemetry from the app |
| **Uptime checks** | External synthetic monitoring | Must be outside your infrastructure to be worth anything |

### On Redpanda

It's in the local stack and it's a reasonable thing to know, but be honest with yourself about production. The live path has **one producer and a handful of consumers**, and the data is ephemeral — nobody replays live gameweek deltas from three weeks ago.

**Redis Streams or Postgres `LISTEN`/`NOTIFY` will do this job**, and both are already in your stack. Deploying and operating a Kafka-compatible broker for a single low-volume topic is real ongoing cost for no current benefit.

Defer Redpanda to production until you have a concrete second consumer that needs independent offsets and replay. Keep it in the local Compose stack if you want the familiarity — that's a legitimate reason to have it locally and not in production, and it's worth writing an ADR saying so, because in eight months you'll wonder why they differ.

### Infrastructure as code

Modest but real. A `fly.toml` (or equivalent) checked in, plus a small Terraform module for the things clicked into existence and then forgotten: R2 buckets and lifecycle rules, Cloudflare DNS and cache rules, database instances, secret placeholders.

Don't Terraform everything. Do make sure that if the account vanished tomorrow, the path back is documented and mostly automated.

---

## 4. The football calendar is the release calendar

The distinctive operational constraint of this system, and the thing most worth building into automation rather than remembering.

### Deploy freeze windows

| Window | Freeze level | Rationale |
|---|---|---|
| Deadline − 6h → deadline | **Hard freeze.** Nothing ships. | Peak traffic, peak stakes. Users are making decisions right now. |
| Deadline → last match settled | **Hard freeze.** | Live path is active; predictions are being scored |
| Last match settled → next deadline − 6h | Normal | The working window, typically Mon–Thu |
| International breaks | Relaxed | Two-week gaps with no fixtures — the in-season maintenance window |
| June – mid July | Wide open | Breaking changes, migrations, infrastructure moves |
| Mid July – mid Aug | **Change freeze except rollover work** | Highest-risk period of the year (§7) |

**Enforce this in CI, not by memory.** A deploy check queries your own fixtures table:

```
just deploy prod
→ ERROR: Deploy blocked.
  GW12 deadline is in 3h 20m (Fri 18:30 UTC).
  Freeze lifts Mon 23:00 UTC after the last GW12 fixture settles.
  Override with --break-glass (logged, requires a reason).
```

The break-glass path must exist — sometimes the thing you're shipping *is* the fix — but it should be deliberate, logged, and slightly uncomfortable.

### Scheduled scaling

Traffic is spiky in a way you know in advance, which is a luxury most systems don't have. Derive the schedule from the fixture table and scale ahead of it:

| Window | API instances |
|---|---|
| Deadline − 2h → deadline | Scale up |
| Match windows | Elevated, particularly if the live path is running |
| Overnight and midweek | Minimum |

Doing this on a schedule rather than reactively means you're already scaled when the spike arrives, instead of autoscaling into it and serving errors for the first ninety seconds.

---

## 5. Release mechanics

### Pipeline

```
merge to main
  → CI: full suite, build image tagged with commit SHA
  → push image
  → deploy to staging, run smoke tests
  → [freeze check]
  → deploy to production
      → run Alembic migrations (expand-only)
      → rolling restart of API instances
      → health check gate
      → deploy new Dagster code location
  → post-deploy smoke tests against production
  → auto-rollback on failure
```

Images are tagged by commit SHA and never by `latest`. Rollback is redeploying a previous SHA, which should be a single command and should be practised at least once when nothing is wrong.

### Zero-downtime specifics

The API is stateless and read-only against marts, so rolling restarts are trivial. The two things that need care:

**Migrations are expand-only in the deploy path.** Contract steps (dropping columns, removing tables) are separate, deliberate releases in the off-season. This means old and new API code can both run against the same schema during a rolling restart, which is precisely what happens.

**Marts swap atomically.** dbt builds into `marts_build`, then a transactional schema rename swaps it in. A failed dbt run leaves the previous marts serving. Stale data is recoverable; half-migrated data is not, and users will have already acted on it.

### Dagster deployment

Separate the deploy of the orchestrator from the deploy of the pipeline code. Dagster code locations can be updated independently of the daemon and webserver, so a pipeline change doesn't restart in-flight runs. Run workers are ephemeral containers that scale to zero — you're running a handful of jobs a day, not a continuous stream.

**Never deploy a code location while a deadline snapshot job is running.** Add it to the freeze check.

---

## 6. Security

| Control | Implementation |
|---|---|
| **Least-privilege database roles** | API role: `SELECT` on analytical schemas, full rights on `app` schema only. Pipeline role: write on analytical, no access to `app`. Structurally prevents an API bug from corrupting the warehouse. |
| **API key storage** | SHA-256 of a 32-byte random key. Fast hashing is correct here — these are high-entropy secrets, not passwords, and bcrypt buys nothing while costing latency on every request. Store a short prefix in plaintext for user-facing identification. |
| **Publishable vs secret keys** | Enforced at the middleware layer. Reject a secret key presented with a browser `Origin` header, and explain why in the error. |
| **Rate limiting** | Redis token bucket, per key, with much tighter limits on the optimiser |
| **Secrets** | Platform secret store, never in images or repo. `gitleaks` in pre-commit and CI. |
| **Images** | Multi-stage builds, slim base, non-root user, pinned base digests, scanned in CI |
| **Transport** | TLS everywhere, HSTS, no plaintext internal hops |
| **Input validation** | Pydantic at the boundary; explicit caps on `horizon`, `alternatives`, page size |
| **Outbound politeness** | Honest `User-Agent` with contact details on every scrape; conservative rate limits; backoff on 429 |
| **PII** | Minimal by design. If you ingest manager data, treat entry IDs and team names as personal data, restrict retention, and never expose more than FPL already makes public. |

---

## 7. Season rollover — the annual high-risk event

Once a year, in roughly a two-week window, most of your assumptions break simultaneously. It deserves a rehearsed runbook rather than improvisation, because it happens exactly often enough to forget how.

**What changes:**

- Element IDs reset and are reassigned to different players
- Promoted clubs appear with no history; relegated clubs vanish mid-model
- Scoring rules may change
- New fields may appear in `bootstrap-static`; old ones may disappear
- Prices reset entirely
- Historical `element-summary` data for departed players becomes unreachable
- Every name-matching mapping needs extending for new arrivals

**Runbook outline:**

1. **Before the rollover:** archive a final complete snapshot of the closing season to bronze. This is the last chance — the live API will not serve it again.
2. Create the new `dim_season` row and its ruleset file. Diff FPL's live scoring configuration against your ruleset and resolve every difference explicitly.
3. Run the schema-drift canary against the new `bootstrap-static`. Handle every reported change deliberately; do not let anything be null-filled silently.
4. Rebuild club dimensions with promoted and relegated sides. Seed cold-start priors for promoted clubs.
5. Extend the name-mapping seed for all new players. Run the identity test; it must be green before anything else proceeds.
6. Backfill `person_id` links for returning players so career history survives.
7. Retrain models on the full history including the closing season.
8. Full pipeline run on staging against the new season's data. Compare outputs against sanity expectations before touching production.
9. Publish a changelog entry for API consumers noting the new season identifier and any response changes.

Give yourself two weekends. The first time will take longer than you expect; write down what actually happened so the second time doesn't.

---

## 8. Observability for one person

The governing question for every alert: **would I want to be woken up for this?** If the answer is no, it is not an alert — it's a dashboard entry or a daily digest. An alerting system that cries wolf gets muted, and then the real one gets missed.

### Alert tiers

| Tier | Examples | Delivery |
|---|---|---|
| **Page** | API down; database unreachable; prediction generation failed with under 6h to a deadline; deadline snapshot job did not run | Phone, immediately |
| **Notify** | Scrape failed after all retries; dbt test failure; identity resolution found unmapped players; error rate above threshold; certificate expiring | Push notification, batched |
| **Digest** | Model metric drift; cache hit rate change; slow query appearance; cost anomaly; dependency advisories | Daily email you read with coffee |

### Deadline-aware severity

The same failure has wildly different urgency depending on where you are in the week. A failed prediction run on a Tuesday is a Notify — you have five days. The identical failure on Friday afternoon is a Page.

Implement severity as a function of hours-to-next-deadline, sourced from your own fixtures table. This is a small amount of code and it's the difference between an alerting setup you trust and one you mute.

### What to instrument

- **RED metrics** on the API: rate, errors, duration, per endpoint
- **Cache hit ratio** at the CDN and at Redis — your primary scaling lever, so watch it
- **Pipeline freshness**: hours since each mart last updated, alerting relative to expected cadence
- **Data quality**: dbt test pass rate, row-count deltas, null rates on critical columns
- **Prediction health**: feature distribution drift versus training, share of players with predictions, mean predicted points versus historical norm
- **Optimiser**: solve time p95, infeasibility rate, timeout rate
- **Cost**: daily spend, alerting on anomalies

The prediction and data-quality signals are the ones nobody sets up and everybody wishes they had. A model quietly degrading over six weeks produces no errors and no alerts — only slowly worse advice.

---

## 9. Backup and recovery

| Asset | Strategy | RPO | RTO |
|---|---|---|---|
| **Bronze (R2)** | Object versioning + lifecycle rules. This is the source of truth. | 0 | n/a |
| **Analytical schemas** | Not backed up — **rebuilt from bronze**. Rebuild must be a tested command. | n/a | Hours |
| **App schema** (keys, jobs, billing) | Managed Postgres PITR + nightly logical dump to R2 | Minutes | < 1 hour |
| **Model artefacts** | Versioned in R2, with training config and metrics | 0 | Minutes |
| **Infrastructure** | Terraform state, remote and versioned | 0 | Hours |

Two things make this real rather than aspirational:

**`just rebuild-from-bronze` must exist and must be tested nightly in CI** against the historical dataset. A rebuild path that has never been run is a theory, and you'll discover its bugs at the worst possible moment.

**Quarterly restore drill.** Restore the app database to a scratch instance, verify API keys resolve, delete it. Put it in the calendar. Untested backups aren't backups, and this is the one failure mode with no recovery path — bronze can rebuild your warehouse but it cannot rebuild your users.

---

## 10. Cost

Realistic monthly running cost, small scale:

| Item | Monthly |
|---|---|
| Managed Postgres | $0–25 (generous free tiers exist; verify current limits) |
| API hosting, 2 small instances | $10–30 |
| Dagster daemon + webserver | $5–15 |
| Optimiser worker (scale to zero) | $0–10 |
| Redis | $0–10 |
| Cloudflare R2 | ~$1 — data is small and egress is free |
| Cloudflare CDN/DNS | $0 |
| Sentry, Grafana Cloud, uptime | $0 on free tiers |
| Domain | ~$1 |
| **Total** | **roughly $20–90** |

The data itself is tiny — hundreds of thousands of rows. Almost all of your cost is compute floor and managed-service baselines, not scale. Resist any architecture decision justified by data volume, because you don't have any.

### Scaling triggers

What breaks first, and what you do about it:

| Trigger | Response |
|---|---|
| Deadline-hour traffic spike | CDN absorbs it. Verify cache hit ratio before adding instances. |
| Optimiser queue backing up | Add worker replicas; they're independent of the API |
| Postgres read load from API | Read replica for the analytical schemas |
| SSE connection count | **This is what justifies extracting the live service into Go**, and nothing before it |
| Backtest compute | Move to a scheduled beefy spot instance rather than upgrading the baseline |

Note what's absent: nothing here calls for re-architecting. Each is an independent, reversible step, which is the property you want when you have ten hours a week.

### If you add a paid tier

Two Nigeria-specific things to verify early rather than late, because they can affect how you structure the offering:

- **Payment processing availability.** Confirm current Stripe availability and requirements for Nigerian entities before designing around it. A merchant-of-record provider such as Paddle or Lemon Squeezy is a common alternative and additionally handles international VAT and sales tax, which is a meaningful burden to offload.
- **Entity and tax structure.** Worth a conversation with an accountant familiar with Nigerian founders selling to international customers before you take the first payment. I'm not able to advise on that.

---

## 11. Runbooks to write

Write each one the first time you do the thing manually, while you still remember which command failed and why.

1. **FPL API schema changed** — how to diagnose, patch validators, backfill affected rows
2. **Season rollover** — the §7 procedure, expanded with actual commands
3. **Prediction job failed with a deadline approaching** — triage tree, how to serve the previous snapshot as a fallback, how to communicate staleness to consumers
4. **Rebuild warehouse from bronze** — full procedure and expected duration
5. **Database restore** — the drill, written down
6. **Leaked API key** — revoke, rotate, notify, audit usage
7. **Rate-limited or blocked by a data source** — backoff posture, how to serve from cache, how to make contact
8. **Rollback a bad release** — including the case where a migration already ran
9. **Live path degraded during matches** — how to shed load, disable SSE, serve polling instead
10. **Cost spike** — where to look first

Each should be ten to thirty lines, written for a tired version of you. Not documentation for others — instructions for yourself, at 23:00, four hours before a deadline, when the thing that broke is something you last touched in March.

---

## 12. The first deployment

Deliberately unambitious, and it should happen in week one or two, before there is anything worth deploying.

1. A `/health` endpoint returning `{"status": "ok"}` and the commit SHA
2. Deployed to production through the full CI/CD pipeline
3. Behind Cloudflare, on a real domain, with TLS
4. Uptime monitoring pointed at it
5. Sentry receiving a deliberately triggered test error
6. A rollback performed, successfully, on purpose

That's the entire deliverable. It gets the deployment path proven while the stakes are zero, and it means every subsequent change ships along a road that already exists. The alternative — building for eight weeks and then discovering your deployment story at the moment you most want to ship — is the single most common way solo projects stall out just before they become real.
