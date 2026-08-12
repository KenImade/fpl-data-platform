{{ config(materialized='table', schema='features') }}

/*
    feat_player_fixture_spine — every player who COULD have played, crossed
    with the fixtures they could have played in.

    This exists because fct_player_gw is appearance-driven. A player left out
    of the squad has no row there at all, so non-appearance is a missing row
    rather than a zero — and non-appearance is the majority of the signal for
    P(play). Training on the fact alone means training only on players who
    played, which is the outcome being predicted.

    Squad membership comes from fct_player_snapshot, so it is point-in-time
    correct by construction: a player signed after the deadline is not here,
    and neither is one whose club had no fixture that gameweek.

    Club comes from the snapshot's capture, not from stg_players, so mid-season
    transfers resolve correctly without needing team_join_date. That field is
    needed for historical rows in fct_player_gw, not here.

    Rows exist only where a usable deadline snapshot does — which means no
    2024/25, since captures begin later. That is the binding constraint on
    training set size, not a filter that can be relaxed.
*/

with snapshot as (
    select
        snapshot_id, season, gameweek, deadline_utc,
        player_id, player_code, team_code, position,
        price_tenths, selected_by_percent,
        status, news, chance_of_playing_next, ep_next
    from {{ ref('fct_player_snapshot') }}
    where is_usable
      and coalesce(removed, false) = false
      and coalesce(can_select, true) = true
),

fixtures as (
    select
        season, gameweek, match_id, team_code, opponent_code,
        is_home, kickoff_utc, elo, opponent_elo, elo_diff
    from {{ ref('fct_team_fixture') }}
    where is_league
),

-- Label side. Left-joined, so a null minutes means did not appear —
-- which is the point of this model.
outcome as (
    select
        season, match_id, player_id,
        minutes, goals, assists, xg, xa,
        goals_conceded_on_pitch, saves, defensive_actions,
        gw_points, gw_bonus, gw_bps, gw_clean_sheets, is_gw_primary
    from {{ ref('fct_player_gw') }}
)

select
    s.snapshot_id,
    s.season,
    s.gameweek,
    s.deadline_utc,

    s.player_id,
    s.player_code,
    s.team_code,
    s.position,

    f.match_id,
    f.kickoff_utc,
    f.opponent_code,
    f.is_home,
    f.elo,
    f.opponent_elo,
    f.elo_diff,

    -- Fixtures this player's club plays in this gameweek. A double gameweek
    -- produces two rows here and both are legitimate targets.
    count(*) over (
        partition by s.snapshot_id, s.player_id
    )                                               as fixtures_in_gw,

    -- Deadline-known state. Everything here predates deadline_utc.
    s.price_tenths,
    s.selected_by_percent,
    s.status,
    s.news is not null                              as has_news,
    s.chance_of_playing_next,
    s.ep_next,

    -- Labels. NULL where the player did not appear.
    coalesce(o.minutes, 0)                          as minutes,
    o.minutes is not null                           as did_appear,
    coalesce(o.minutes, 0) >= 60                    as played_60,
    o.goals, o.assists, o.xg, o.xa,
    o.goals_conceded_on_pitch, o.saves, o.defensive_actions,
    o.gw_points, o.gw_bonus, o.gw_bps, o.gw_clean_sheets,

    current_timestamp                               as built_at

from snapshot s

inner join fixtures f
    on  f.season   = s.season
    and f.gameweek = s.gameweek
    and f.team_code = s.team_code

left join outcome o
    on  o.season    = s.season
    and o.match_id  = f.match_id
    and o.player_id = s.player_id