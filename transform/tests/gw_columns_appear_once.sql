-- transform/tests/gw_columns_appear_once.sql
--
-- gw_* columns carry gameweek-level values that cannot be split across a
-- double gameweek's fixtures. They are attached to the first fixture only,
-- so that a naive sum is correct. If more than one fixture in a gameweek
-- carries them, every aggregate over those columns double-counts.

select season, player_id, gameweek, count(gw_points) as populated_rows
from {{ ref('fct_player_gw') }}
group by 1, 2, 3
having count(gw_points) > 1