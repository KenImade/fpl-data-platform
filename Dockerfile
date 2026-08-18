# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

WORKDIR /app

# Lock and workspace members first, so a source-only change doesn't
# invalidate the dependency layer.
COPY pyproject.toml uv.lock ./
COPY packages/ packages/

# dbt-core and dbt-postgres are RUNTIME dependencies here, not dev ones.
# Dagster shells out to the dbt CLI at execution time, so --no-dev must not
# strip them. If they sit in the dev group, move them.
RUN uv sync --frozen --package fpl-ingestion --no-dev


# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm

RUN useradd -m app
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build --chown=app /app /app
COPY --chown=app rulesets/ rulesets/
COPY --chown=app transform/ transform/
COPY --chown=app deploy/dagster.yaml /dagster-home/dagster.yaml

USER app

ENV PATH="/app/.venv/bin:$PATH" \
    DAGSTER_HOME=/dagster-home \
    FPL_RULESETS_DIR=/app/rulesets \
    DBT_PROJECT_DIR=/app/transform \
    DBT_PROFILES_DIR=/app/transform \
    PYTHONUNBUFFERED=1

# Resolve dbt packages (dbt_utils) and generate target/manifest.json.
#
# This is not optional. @dbt_assets reads the manifest at CODE LOCATION LOAD
# time to build the asset graph. DbtProject.prepare_if_dev() regenerates it
# locally but does nothing in production, so without this step the whole
# deployment fails to start — not a degraded service, no service.
#
# --profiles-dir is passed explicitly because parse needs to resolve the
# profile even though it touches no database. The env vars it interpolates
# are absent at build time; dbt tolerates that for parse.
RUN cd /app/transform \
    && dbt deps \
    && dbt parse --profiles-dir /app/transform || \
       (echo "dbt parse failed — the code location will not load" && exit 1)

ARG GIT_SHA
ENV GIT_SHA=$GIT_SHA

# Overridden per process in fly.toml: `web` runs the webserver, `daemon` runs
# the daemon. The daemon must never auto-stop — a stopped daemon means no
# sensor ticks, no captures, and nothing failing.
CMD ["dagster-webserver", "-h", "0.0.0.0", "-p", "8080", "-m", "fpl_ingestion.definitions"]