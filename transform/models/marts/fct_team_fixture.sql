{{ config(materialized='table') }}

/*
    fct_team_fixture — one row per TEAM per match, so two rows per fixture.

    Unpivoted from stg_matches because almost everything at team level is
    asymmetric: goals for and against, home advantage, Elo, rest days. A
    match-grain table forces every query to handle home and away separately.

    Includes ALL competitions. That is the point — fixture congestion and
    European fatigue are computed across everything a team played, and the
    source assessment flagged those as having no other available source.
    Filter on is_league for anything scoring-related.

    NULL team_code means a non-Premier League club. Those rows are dropped:
    we have no identity for them, and Ajax's rest days are not a feature.
    The English side of every European tie survives.
*/

with matches as (
    select * from {{ ref('stg_matches') }}
),

unpivoted as (
    select
        match_id, season, gameweek, competition, is_league, kickoff_utc,
        home_team_code                          as team_code,
        away_team_code                          as opponent_code,
        true                                    as is_home,
        home_score                              as goals_for,
        away_score                              as goals_against,
        home_elo                                as elo,
        away_elo                                as opponent_elo
    from matches
    where home_team_code is not null

    union all

    select
        match_id, season, gameweek, competition, is_league, kickoff_utc,
        away_team_code, home_team_code, false,
        away_score, home_score, away_elo, home_elo
    from matches
    where away_team_code is not null
),

/*
    Congestion is computed only over DATED fixtures, then joined back.

    Nulls are peers in a RANGE frame, so undated fixtures all fall inside
    each other's window — 2025/26 GW34-38 have no kickoff time and produced
    counts of 8 to 14 for teams that played five matches. Excluding them
    means an undated fixture gets NULL congestion, which is the honest
    answer: we don't know when it was played, so we can't say what preceded
    it, and we'd also be missing from the counts of whatever followed.

    Across ALL competitions by design. A midweek European tie is precisely
    the fatigue this measures.
*/
scheduled as (
    select
        match_id,
        team_code,

        kickoff_utc - lag(kickoff_utc) over (
            partition by team_code, season
            order by kickoff_utc
        )                                       as time_since_last_match,

        count(*) over (
            partition by team_code, season
            order by kickoff_utc
            range between interval '14 days' preceding
                      and interval '1 second' preceding
        )                                       as matches_prior_14d

    from unpivoted
    where kickoff_utc is not null
)

select
    u.match_id,
    u.season,
    u.gameweek,
    u.competition,
    u.is_league,
    u.kickoff_utc,

    u.team_code,
    t.team_name,
    t.team_short,
    u.opponent_code,
    o.team_name                                 as opponent_name,
    u.is_home,

    u.goals_for,
    u.goals_against,
    u.goals_for - u.goals_against               as goal_difference,
    case
        when u.goals_for > u.goals_against then 'W'
        when u.goals_for < u.goals_against then 'L'
        when u.goals_for is not null        then 'D'
    end                                         as result,

    -- Point-in-time rating: Elo AT KICKOFF, not a season snapshot. No
    -- leakage from future form, which is what makes it usable as a prior
    -- for promoted clubs with no top-flight history.
    u.elo,
    u.opponent_elo,
    u.elo - u.opponent_elo                      as elo_diff,

    -- NULL where kickoff is unknown, and for a team's first fixture of a
    -- season. Both are genuine absences rather than zeroes.
    extract(epoch from s.time_since_last_match) / 86400  as days_since_last_match,
    s.matches_prior_14d,

    current_timestamp                           as built_at

from unpivoted u

left join scheduled s
    on  u.match_id  = s.match_id
    and u.team_code = s.team_code

left join {{ ref('stg_teams') }} t
    on  u.team_code = t.team_code
    and u.season    = t.season

left join {{ ref('stg_teams') }} o
    on  u.opponent_code = o.team_code
    and u.season        = o.season