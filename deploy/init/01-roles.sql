--  Runs once, on first container start, when the data volume is empty.
--  Idempotent anyway, so a re-run after a restore is harmless.

-- The API reads marts. It cannot write anything, anywhere, ever — not
-- because of discipline, but because the grants do not exist.

 CREATE DATABASE dagster;

 DO $$
 BEGIN
     IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fpl_api') THEN
         CREATE ROLE fpl_api WITH LOGIN;
     END IF;
 END $$;

 -- Password comes from the environment at deploy time rather than being
 -- baked into a file in the repository.
 \set api_password `echo "$API_DB_PASSWORD"`
 ALTER ROLE fpl_api WITH PASSWORD :'api_password';

 GRANT CONNECT ON DATABASE fpl TO fpl_api;
 REVOKE ALL ON SCHEMA public FROM fpl_api;

 CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION fpl_api;

 -- analytics_marts does not exist until dbt has run. The grants are
 -- applied by dbt's post-hook instead:
 --     +post-hook: "grant select on {{ this }} to fpl_api"