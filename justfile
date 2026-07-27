set dotenv-load := true

default:
    @just --list

bootstrap: up _bucket
    uv sync
    @echo "ready"

up:
    docker compose up -d --wait

down:
    docker compose down

_bucket:
    docker compose exec -T minio mc alias set local http://localhost:9000 minioadmin minioadmin
    docker compose exec -T minio mc mb --ignore-existing local/fpl-bronze

test:
    uv run pytest -q

lint:
    uv run ruff check .
    uv run ruff format --check

fmt:
    uv run ruff format .

clean:
    docker compose down -v

typecheck:
    uv run mypy

record-fixtures:
    uv run python scripts/record_fixtures.py