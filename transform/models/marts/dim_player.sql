{{ config(materialized='table') }}
 
/*
    dim_player — one row per player per season.
 
    The season-scoped counterpart to dim_person. Use this for anything about
    a player IN a season: their club, position, price. Use dim_person for
    anything spanning seasons, because player_id is reassigned each August.
 
    Price and availability come from the most recent capture, not from a
    deadline snapshot. That is deliberate: this is the CURRENT view an API
    consumer wants. Anything predictive must read fct_player_snapshot, where
    the point-in-time boundary makes post-deadline information structurally
    unavailable.
 
    Season totals are league-only and derived from fct_player_gw, so they
    inherit its ~2% coverage gap — see ADR 0005. They are indicative for
    display, not authoritative for scoring.
*/
 
with players as (
    select * from {{ ref('stg_players') }}
),
 
-- Latest observation per player, from our own captures. asyncpg-friendly:
-- one row per player, no window functions leaking into the API's queries.
latest_state as (
    select distinct on (player_id)
        player_id,
        captured_at,
        price_tenths,
        selected_by_percent,
        status,
        news,
        chance_of_playing_next,
        ep_next
    from {{ ref('stg_fpl_players') }}
    order by player_id, captured_at desc
),
 
season_totals as (
    select
        season,
        player_id,
        count(*)                                    as appearances,
        sum(minutes)                                as minutes,
        sum(goals)                                  as goals,
        sum(assists)                                as assists,
        sum(xg)                                     as xg,
        sum(xa)                                     as xa,
        sum(gw_points)                              as points,
        sum(gw_bonus)                               as bonus
    from {{ ref('fct_player_gw') }}
    where is_league and minutes > 0
    group by 1, 2
)
 
select
    p.season,
    p.player_id,
    p.player_code,
    p.team_code,
 
    p.web_name,
    p.full_name,
    p.first_name,
    p.second_name,
    p.position,
    p.element_type,
 
    t.team_name,
    t.team_short,
 
    -- Current state. Only meaningful for the season in progress; for a
    -- finished season this is whatever was true at the last capture.
    l.price_tenths,
    l.price_tenths / 10.0                        as price,
    l.selected_by_percent,
    l.status,
    l.news,
    l.chance_of_playing_next,
    l.ep_next,
    l.captured_at                                as state_as_of,
 
    -- League totals. Indicative for display; inherits the coverage gap
    -- documented in ADR 0005.
    coalesce(s.appearances, 0)                   as appearances,
    coalesce(s.minutes, 0)                       as minutes,
    coalesce(s.goals, 0)                         as goals,
    coalesce(s.assists, 0)                       as assists,
    s.xg,
    s.xa,
    coalesce(s.points, 0)                        as points,
    coalesce(s.bonus, 0)                         as bonus,
 
    current_timestamp                            as built_at
 
from players p
 
left join {{ ref('dim_team') }} t
    on p.team_code = t.team_code and p.season = t.season
 
-- Only the season in progress has current state. Joining unconditionally
-- would attach today's price to a 2024/25 player row.
left join latest_state l
    on p.player_id = l.player_id
    and p.season = (select max(season) from {{ ref('stg_players') }})
 
left join season_totals s
    on p.season = s.season and p.player_id = s.player_id