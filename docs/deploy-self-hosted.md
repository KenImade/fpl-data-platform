# Self-Hosted Dagster Deployment

**Step 26.** Target: running in production before Friday 21 August 2026, 17:30 UTC.

---

## What runs where

| Component | Where | Notes |
|---|---|---|
| Dagster webserver | Fly machine, `web` process | UI only. Can auto-stop. |
| Dagster daemon | Fly machine, `daemon` process | Sensors, schedules, run queue. **Must never auto-stop.** |
| Run workers | Subprocesses of the daemon | `DefaultRunLauncher`; adequate at this volume |
| Postgres | Managed (Neon free tier, or Fly Postgres) | Run storage, event logs, **sensor cursors** |
| Object storage | Cloudflare R2 | Already live |
| Liveness | healthchecks.io | The only thing that catches the daemon being dead |

One image, two processes. No persistent volume: with Postgres storage configured, `DAGSTER_HOME` only needs to hold `dagster.yaml`, which is baked in.

---

## 1. Production `dagster.yaml`

Committed at `deploy/dagster.yaml`, baked into the image. Credentials come from the environment — never duplicated in YAML.

```yaml
storage:
  postgres:
    postgres_db:
      username:
        env: PGUSER
      password:
        env: PGPASSWORD
      hostname:
        env: PGHOST
      db_name:
        env: PGDATABASE
      port: 5432
      params:
        sslmode: require

run_coordinator:
  module: dagster.core.run_coordinator
  class: QueuedRunCoordinator
  config:
    max_concurrent_runs: 4

run_launcher:
  module: dagster.core.launcher
  class: DefaultRunLauncher

# Compute logs are ephemeral on Fly machines. Structured logs go to Postgres
# and to `fly logs`, which is what you'll actually read. Persisting stdout to
# R2 is possible via dagster-aws but isn't worth the moving part yet.
compute_logs:
  module: dagster.core.storage.noop_compute_log_manager
  class: NoOpComputeLogManager

telemetry:
  enabled: false

retention:
  schedule:
    purge_after_days: 90
  sensor:
    purge_after_days: 90
```

---

## 2. Dockerfile

One image, both processes. Extends what you built at step 7.

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
RUN uv sync --frozen --package fpl-ingestion --no-dev

FROM python:3.13-slim-bookworm
RUN useradd -m app
WORKDIR /app

COPY --from=build --chown=app /app /app
COPY --chown=app rulesets/ rulesets/
COPY --chown=app deploy/dagster.yaml /dagster-home/dagster.yaml

USER app
ENV PATH="/app/.venv/bin:$PATH" \
    DAGSTER_HOME=/dagster-home \
    FPL_RULESETS_DIR=/app/rulesets \
    PYTHONUNBUFFERED=1

ARG GIT_SHA
ENV GIT_SHA=$GIT_SHA

CMD ["dagster-webserver", "-h", "0.0.0.0", "-p", "8080", "-m", "fpl_ingestion.definitions"]
```

`FPL_RULESETS_DIR` matters — `_rulesets_dir()` walks up four parents from the module, which doesn't hold inside the container.

---

## 3. `fly.toml`

```toml
app = "fpl-ingestion"
primary_region = "lhr"

[build]
  dockerfile = "Dockerfile"

[processes]
  web = "dagster-webserver -h 0.0.0.0 -p 8080 -m fpl_ingestion.definitions"
  daemon = "dagster-daemon run -m fpl_ingestion.definitions"

[http_service]
  internal_port = 8080
  force_https = true
  processes = ["web"]
  auto_stop_machines = "suspend"
  auto_start_machines = true
  min_machines_running = 0

[[vm]]
  processes = ["web"]
  size = "shared-cpu-1x"
  memory = "1gb"

[[vm]]
  processes = ["daemon"]
  size = "shared-cpu-1x"
  memory = "1gb"
```

**The daemon has no `http_service` block, so it never auto-stops.** That is the single most important line in this file, and it's an absence rather than a setting — verify it explicitly after deploying:

```bash
fly machines list
```

The daemon machine must show `started`, always. If it ever shows `stopped`, sensors aren't ticking and nothing is failing.

The web process can suspend freely. Losing the UI for a few seconds costs nothing.

**Memory:** 1 GB each. Dagster is not lean; 512 MB works until a run worker spawns and then gets OOM-killed mid-capture.

---

## 4. Secrets

```bash
fly secrets set \
  PGHOST=... PGUSER=... PGPASSWORD=... PGDATABASE=... \
  S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com \
  S3_ACCESS_KEY_ID=... \
  S3_SECRET_ACCESS_KEY=... \
  S3_BUCKET=fpl-bronze \
  USER_AGENT="fpl-api-project (contact: you@example.com)" \
  HEARTBEAT_URL=https://hc-ping.com/<uuid>
```

`DRY_RUN` must be **absent**, not empty — `os.environ.get("DRY_RUN")` treats `""` as falsy so either works, but absence is unambiguous.

`HEARTBEAT_URL` unset in production is the one place it genuinely matters. Confirm it's there.

---

## 5. Fail fast on missing config

Right now `build_store()` reads `os.environ` at call time inside the sensor. A missing variable becomes a sensor error every 60 seconds while nothing captures — a loud-looking failure that still produces no data.

Convert to a resource so it fails at code-location load, before anything is scheduled:

```python
# fpl_ingestion/resources.py
from dagster import ConfigurableResource, EnvVar
import boto3

from fpl_ingestion.storage import S3Store


class StoreResource(ConfigurableResource):
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str

    def build(self) -> S3Store:
        return S3Store(
            boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
            ),
            self.bucket,
        )


STORE = StoreResource(
    endpoint_url=EnvVar("S3_ENDPOINT_URL"),
    access_key_id=EnvVar("S3_ACCESS_KEY_ID"),
    secret_access_key=EnvVar("S3_SECRET_ACCESS_KEY"),
    bucket=EnvVar("S3_BUCKET"),
)
```

Then `Definitions(..., resources={"store": STORE})`, and ops/sensors take `store: StoreResource` and call `store.build()`.

Keep `DRY_RUN` working locally by branching in `build()` rather than at construction.

---

## 6. Deploy sequence

```bash
# 1. Provision Postgres and create the database
#    (Neon: create project, copy connection details)

# 2. Create the app without deploying
fly launch --no-deploy --name fpl-ingestion

# 3. Secrets, before first deploy
fly secrets set ...

# 4. Deploy
fly deploy --build-arg GIT_SHA=$(git rev-parse --short HEAD)

# 5. Confirm BOTH machines exist and the daemon is started
fly machines list

# 6. Watch it come up
fly logs
```

Dagster creates its own tables on first connection, so no migration step.

---

## 7. Verification — in this order

**a. Daemon is alive and ticking.**
```bash
fly logs | grep SensorDaemon
```
Skip reasons with a decrementing countdown, exactly as locally.

**b. Sensor reads state from R2.** The first tick will log `no bootstrap stored yet` if the production bucket is empty — expected, and it should capture immediately.

**c. Captures land.** Check R2 for `raw/fpl/bootstrap-static/<today>/`.

**d. Heartbeat is green** on healthchecks.io.

**e. Cursor survives a deploy.** Redeploy, and confirm the sensor resumes its countdown rather than capturing immediately. This proves the cursor is in Postgres rather than machine-local — the thing I got wrong initially, so verify rather than assume.

**f. Daemon machine stays started.** `fly machines list` after an hour of idleness.

---

## 8. CI

Extend the deploy job from step 6 to build with `GIT_SHA` and deploy both processes. Smoke test hits the webserver:

```yaml
      - name: Smoke test
        run: |
          for i in {1..10}; do
            curl -fsS https://fpl-ingestion.fly.dev/server_info && break
            sleep 5
          done
```

**Add the deploy freeze check now**, while it costs nothing. It needs the fixtures data you already capture:

```
deadline − 6h → last match settled  =  no deploys
```

Read deadlines from `LATEST_BOOTSTRAP` in R2 and fail the job with a `--break-glass` override. Leave a TODO if it's not ready; add it before 21 August.

---

## 9. Known gaps to close before GW1

- [ ] Confirm the daemon machine has never auto-stopped over a full week
- [ ] Alert routing beyond log levels (`failure_alert_sensor` currently only logs)
- [ ] Deploy freeze check enforced in CI
- [ ] R2 lifecycle rule for the weekly tarballs (~1.7 GB/year, mostly unchanged)
- [ ] A restore drill: rebuild bronze from raw in production, end to end