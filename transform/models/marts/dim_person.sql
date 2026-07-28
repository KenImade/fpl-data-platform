{{ config(materialized='table') }}

/*
    dim_person — one row per human, not per player-season.

    player_code is FPL's stable identifier: the same person keeps it year to
    year. player_id is the season-scoped element ID and gets REASSIGNED each
    August, so joining on it across seasons silently attaches one player's
    history to a different person.

    Name and position come from the most recent season the person appears in,
    because both change: "Ipswich" became "Ipswich Town" at club level, and
    players are reclassified between positions. First/last season bound the
    career as observed in our data, not in reality — a player active before
    2024/25 shows first_season = 2024-2025.
*/

with players as (
    select * from {{ ref('stg_players') }}
),

ranked as (
    select
        *,
        row_number() over (
            partition by player_code
            order by season desc
        ) as recency
    from players
),

latest as (
    select
        player_code,
        web_name,
        full_name,
        first_name,
        second_name,
        position        as current_position,
        team_code       as current_team_code
    from ranked
    where recency = 1
),

career as (
    select
        player_code,
        min(season)                     as first_season,
        max(season)                     as last_season,
        count(*)                        as seasons_observed,
        count(distinct team_code)       as clubs_observed,
        count(distinct position)        as positions_observed
    from players
    group by player_code
)

select
    c.player_code,
    l.web_name,
    l.full_name,
    l.first_name,
    l.second_name,
    l.current_position,
    l.current_team_code,
    c.first_season,
    c.last_season,
    c.seasons_observed,
    c.clubs_observed,
    -- A player reclassified between positions is a modelling hazard: their
    -- historical per-90 rates were accrued under different scoring rules.
    c.positions_observed > 1        as has_changed_position,
    current_timestamp               as built_at

from career c
inner join latest l using (player_code)