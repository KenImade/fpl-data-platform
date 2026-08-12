-- The point-in-time guarantee, made checkable. Any row here is a leak.
select snapshot_id, player_id, last_appearance_at, deadline_utc
from {{ ref('feat_player_form') }}
where last_appearance_at >= deadline_utc

union all

select snapshot_id, player_id, last_fixture_at, deadline_utc
from {{ ref('feat_player_load') }}
where last_fixture_at >= deadline_utc