FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
WORKDIR /app
COPY pyproject.toml README.md uv.lock ./
COPY packages/ packages/
RUN uv sync --frozen --package fpl-api --no-dev

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