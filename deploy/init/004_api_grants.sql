-- The fpl_api role reads marts and authenticates against app.api_key. Neither
-- worked in production until 2026-08-18: the role existed and the connection
-- string pointed at it, but nothing had granted it anything, so every
-- authenticated request 500'd while /health stayed green.
--
-- dbt's +post-hook grants select per table as it builds. That is not enough on
-- its own — a table grant is inert without usage on the schema, and it cannot
-- cover tables built before the hook existed.

grant usage on schema app to fpl_api;
grant select on app.api_key to fpl_api;
-- Column-scoped: _lookup updates last_used_at, but a leaked key must not be
-- able to un-revoke itself.
grant update (last_used_at) on app.api_key to fpl_api;

grant usage on schema analytics_marts to fpl_api;
grant select on all tables in schema analytics_marts to fpl_api;
alter default privileges in schema analytics_marts grant select on tables to fpl_api;