--
-- The API owns `app`. It reads `analytics_marts` and can write nothing there.
--
-- Two schemas rather than one because the guarantees differ. The warehouse
-- is Dagster's and dbt's; a bug in the API must not be able to reach it. But
-- the API needs somewhere to keep its own state, and denying it that would
-- mean either a second service or granting warehouse write access — both
-- worse than a separate schema.
 
CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION fpl_api;
 
CREATE TABLE IF NOT EXISTS app.api_key (
    id              bigserial PRIMARY KEY,
 
    -- SHA-256 of the key. These are 32 bytes of CSPRNG output, not
    -- passwords: there is no dictionary to attack, so a slow hash buys
    -- nothing and costs latency on every single request.
    key_hash        text        NOT NULL UNIQUE,
 
    -- First few characters, in clear. Lets a user identify a key in a
    -- dashboard without us storing anything usable.
    key_prefix      text        NOT NULL,
 
    -- 'publishable' ships in browser bundles and is constrained by origin.
    -- 'secret' stays server-side and may reach expensive endpoints.
    key_type        text        NOT NULL CHECK (key_type IN ('publishable', 'secret')),
 
    name            text        NOT NULL,
    owner_email     text,
 
    -- Publishable keys only. NULL means any origin, which is a deliberate
    -- choice a user has to make rather than a default they inherit.
    allowed_origins text[],
 
    rate_limit_per_minute integer NOT NULL DEFAULT 60,
 
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_used_at    timestamptz,
    revoked_at      timestamptz
);
 
CREATE INDEX IF NOT EXISTS api_key_hash_idx ON app.api_key (key_hash)
    WHERE revoked_at IS NULL;