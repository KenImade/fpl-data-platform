{{ config(materialized='table') }}
 
/*
    dim_gameweek — the calendar, with fixture counts.
 
    fixture_count is the number of LEAGUE matches. A value other than 10 is
    a rescheduled round rather than an error: matches move for cup ties and
    European commitments, so 7 and 13 both occur in a normal season.
 
    has_snapshot reports whether a point-in-time capture exists for this
    deadline. Before a gameweek that is expected to be false; after it,
    false means the capture sensor missed and any feature built for that
    gameweek is unreliable.
*/
 
with gameweeks as (
    select * from {{ ref('stg_gameweeks') }}
),
 
fixtures as (
    select
        season,
        gameweek,
        count(distinct match_id)                    as fixture_count,
        min(kickoff_utc)                            as first_kickoff,
        max(kickoff_utc)                            as last_kickoff
    from {{ ref('fct_team_fixture') }}
    where is_league
    group by 1, 2
),
 
snapshots as (
    select season, gameweek, snapshot_id, snapshot_at,
           hours_before_deadline, is_usable
    from {{ ref('fct_deadline_snapshot') }}
)
 
select
    g.season,
    g.gameweek,
    g.gameweek_name,
    g.deadline_utc,
    g.is_finished,
    g.is_data_checked,
 
    f.fixture_count,
    f.first_kickoff,
    f.last_kickoff,
 
    g.average_score,
    g.highest_score,
    g.most_selected_player_id,
    g.most_captained_player_id,
    g.transfers_made,
 
    s.snapshot_id,
    s.snapshot_at,
    s.hours_before_deadline,
    coalesce(s.is_usable, false)                as has_usable_snapshot,
 
    current_timestamp                           as built_at
 
from gameweeks g
left join fixtures f
    on g.season = f.season and g.gameweek = f.gameweek
left join snapshots s
    on g.season = s.season and g.gameweek = s.gameweek