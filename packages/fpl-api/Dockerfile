FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
RUN uv sync --frozen --package fpl-api --no-dev

FROM python:3.13-slim-bookworm
RUN useradd -m app
WORKDIR /app
COPY --from=build --chown=app /app /app
USER app
ENV PATH="/app/.venv/bin:$PATH"
ARG GIT_SHA
ENV GIT_SHA=$GIT_SHA
CMD ["uvicorn", "fpl-api.main:app", "--host", "0.0.0.0", "--port", "8080"]