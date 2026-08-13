-- A double gameweek's P(60+ in at least one) must exceed either fixture's
-- individual probability. If the log-based product aggregation is wrong, this
-- is what catches it — a range test on 0-1 would not.
select g.season, g.gameweek, g.player_id,
       g.p_minutes_60 as gw_prob,
       max(f.p_minutes_60) as best_fixture_prob
from {{ ref('mart_player_gameweek_predictions') }} g
join {{ ref('mart_player_fixture_predictions') }} f
  using (season, gameweek, player_id)
where g.is_double_gw
group by 1, 2, 3, 4
having g.p_minutes_60 <= max(f.p_minutes_60)