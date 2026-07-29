
# Production deployment

One host, one compose file. Everything except object storage.

## Host

8GB RAM, 4 vCPU, 80GB disk. Hetzner CX32 or equivalent, ~€10/month.
4GB works if you drop the API to one replica.

Docker Engine with the compose plugin. Not Docker Desktop.

## First deploy

```
git clone <repo> && cd fpl-data-platform
cp .env.production.example .env
$EDITOR .env                      # every value, none optional

export GIT_SHA=$(git rev-parse --short HEAD)
docker compose -f compose.prod.yaml up -d --build
```

Then, in order:

```
# 1. Wait for Postgres, then apply the API schema
docker compose -f compose.prod.yaml exec -T postgres \
    psql -U fpl -d fpl < deploy/api_schema.sql

# 2. Seed the warehouse from an empty bucket
docker compose -f compose.prod.yaml exec dagster-daemon \
    dagster job execute -m fpl_ingestion.definitions -j ci_snapshot_job

# 3. Issue an API key
docker compose -f compose.prod.yaml exec api \
    python -m fpl_api.cli create --name "first key" --type publishable
```

## Verify

```
curl https://$API_DOMAIN/health
curl -H "X-API-Key: pk_..." "https://$API_DOMAIN/v1/teams?season=2026-2027"
docker compose -f compose.prod.yaml ps      # all healthy
```

Then the things that only fail silently:

- **The daemon is ticking.** `docker compose logs -f dagster-daemon | grep Sensor` should show skip reasons with a decrementing countdown.
- **Captures are landing.** Check R2 for objects under today's date.
- **The heartbeat is green** on healthchecks.io.
- **A backup ran.** `docker compose logs backup` within six hours of start.

## Deploying a change

```
git pull
export GIT_SHA=$(git rev-parse --short HEAD)
docker compose -f compose.prod.yaml up -d --build
```

Compose recreates changed containers only. The API has two replicas but
they restart together, so expect a few seconds of 502. That is the main
thing this setup gives up versus a platform with rolling deploys.

**Never deploy inside a deadline window** — six hours before a deadline
through to the last match settling. A restarted daemon loses its in-flight
run, and the capture it was making is not recoverable.

## Restoring

```
aws --endpoint-url $S3_ENDPOINT_URL s3 ls s3://$S3_BUCKET/backups/postgres/
aws --endpoint-url $S3_ENDPOINT_URL s3 cp s3://.../fpl-<stamp>.dump .

docker compose -f compose.prod.yaml stop api dagster-daemon dagster-web
docker compose -f compose.prod.yaml exec -T postgres \
    pg_restore -U fpl -d fpl --clean --if-exists < fpl-<stamp>.dump
docker compose -f compose.prod.yaml start api dagster-daemon dagster-web
```

**Do this once before you need to.** An untested restore is a hypothesis,
and this is the only failure mode with no other recovery path — R2 rebuilds
the warehouse but nothing rebuilds API keys or sensor cursors.

## What this gives up

Worth naming, because none of it is hidden:

- **No rolling deploys.** Seconds of downtime per release.
- **One host.** Its failure is total; recovery is a new host plus a restore.
- **You own the OS.** Unattended-upgrades at minimum.
- **Postgres is yours.** Hence the backup service, which is load-bearing
   rather than optional.

All acceptable for a project with no uptime commitment. Revisit if that
changes.