-- deploy/api_role.sql
--
-- The API reads marts. It cannot write anything, anywhere, ever.
--
-- This is not defence against a malicious actor — it's defence against a
-- bug. A stray UPDATE in a query builder, a migration pointed at the wrong
-- schema, an ORM deciding to create a table. None of those can reach the
-- warehouse if the role has no rights to it.

CREATE ROLE fpl_api WITH LOGIN PASSWORD 'change-me';

-- Connect, and nothing else at the database level.
GRANT CONNECT ON DATABASE fpl TO fpl_api;

-- Marts only. Not bronze, not staging, not the Dagster schemas.
GRANT USAGE ON SCHEMA analytics_marts TO fpl_api;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics_marts TO fpl_api;

-- New marts inherit the grant, so adding a model doesn't silently produce
-- a table the API can't read.
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics_marts
    GRANT SELECT ON TABLES TO fpl_api;

-- Explicitly deny the public schema, where Postgres otherwise grants CREATE
-- to everyone by default.
REVOKE ALL ON SCHEMA public FROM fpl_api;