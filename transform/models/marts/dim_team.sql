{{ config(materialized='table') }}
 
/*
    dim_team — one row per club per season.
 
    Season-scoped because clubs are: strength ratings change, team_id gets
    reassigned, and even the name is not stable ("Ipswich" became "Ipswich
    Town"). team_code is the identity that survives.
 
    Exists in marts rather than the API reading stg_teams directly. The rule
    "the API reads marts" is simpler to hold than "the API reads marts and
    some staging views", and it means this shape can change for the API's
    benefit without touching the staging layer.
 
    latest_elo is the rating after a club's most recent PLAYED match — a
    current-strength figure suitable for display. Anything modelling should
    use the per-match elo on fct_team_fixture instead, which is the rating
    at kickoff and carries no hindsight.
*/
 
with teams as (
    select * from {{ ref('stg_teams') }}
),
 
-- Most recent played fixture per club, for a current strength figure.
latest_rating as (
    select distinct on (season, team_code)
        season,
        team_code,
        elo             as latest_elo,
        kickoff_utc     as latest_match_at
    from {{ ref('fct_team_fixture') }}
    where result is not null and elo is not null
    order by season, team_code, kickoff_utc desc
),
 
record as (
    select
        season,
        team_code,
        count(*)                                    as matches_played,
        count(*) filter (where result = 'W')        as wins,
        count(*) filter (where result = 'D')        as draws,
        count(*) filter (where result = 'L')        as losses,
        sum(goals_for)                              as goals_for,
        sum(goals_against)                          as goals_against
    from {{ ref('fct_team_fixture') }}
    where is_league and result is not null
    group by 1, 2
)
 
select
    t.season,
    t.team_code,
    t.team_id,
    t.team_name,
    t.team_short,
 
    t.strength,
    t.strength_overall_home,
    t.strength_overall_away,
    t.strength_attack_home,
    t.strength_attack_away,
    t.strength_defence_home,
    t.strength_defence_away,
 
    r.latest_elo,
    r.latest_match_at,
 
    -- League record. NULL rather than zero before a ball is kicked, so a
    -- pre-season club is distinguishable from one that has played and lost
    -- everything.
    rec.matches_played,
    rec.wins,
    rec.draws,
    rec.losses,
    rec.goals_for,
    rec.goals_against,
    rec.goals_for - rec.goals_against            as goal_difference,
 
    current_timestamp                            as built_at
 
from teams t
left join latest_rating r
    on t.season = r.season and t.team_code = r.team_code
left join record rec
    on t.season = rec.season and t.team_code = rec.team_code