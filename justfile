set shell := ["bash", "-uc"]

default:
    @just --list

_dagster_db:
    docker compose exec -T postgres psql -U fpl -c "CREATE DATABASE dagster" || true

bootstrap: up _bucket _dagster_db
    uv sync --all-packages
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
    uv run ruff check --fix .

clean:
    docker compose down -v

typecheck:
    uv run mypy

record-fixtures:
    uv run python scripts/record_fixtures.py

dev:
    uv run dagster dev -m fpl_ingestion.definitions

wipe:
    uv run python scripts/wipe.py

wipe-yes:
    uv run python scripts/wipe.py --yes

wipe-derived:
    uv run python scripts/wipe.py --derived --yes

seed:
    uv run dagster job execute -m fpl_ingestion.definitions -j ci_snapshot_job
    uv run dagster job execute -m fpl_ingestion.definitions -j ci_daily_job \
        --tags '{"dagster/partition": "'$(date -u +%F)'"}'
    uv run dagster job execute -m fpl_ingestion.definitions -j fpl_bronze_job \
        --tags '{"dagster/partition": "'$(date -u +%F)'"}'
    uv run dagster job execute -m fpl_ingestion.definitions -j load_job

_api_role:
    docker compose exec -T postgres psql -U fpl -d fpl -f /dev/stdin < deploy/api_role.sql || true