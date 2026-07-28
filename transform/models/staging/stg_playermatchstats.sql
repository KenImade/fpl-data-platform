{{ config(materialized='view') }}

-- Per-match player performance. The ONLY per-fixture grain available, and so
-- the basis for anything evaluating per-match scoring rules — the 60-minute
-- threshold, clean sheets, defensive contribution. player_gameweek_stats
-- aggregates double gameweeks and cannot serve that purpose.
--
-- It has no competition column of its own; competition comes from the join to
-- stg_matches. A per-90 calculation that skips that join silently includes
-- Champions League minutes in a league rate.
--
-- What it LACKS versus player_gameweek_stats: cards, own goals, penalties
-- saved, and bonus. Those are gameweek-level only, which is why the fact
-- table needs both sources.

with source as (
    select * from {{ source('bronze', 'ci_playermatchstats') }}
),

matches as (
    select match_id, season, gameweek, competition, is_league, kickoff_utc
    from {{ ref('stg_matches') }}
),

renamed as (
    select
        s.match_id,
        {{ to_int('s.player_id') }}                     as player_id,

        -- minutes_played is occasionally unpopulated for very recent fixtures
        -- the source hasn't backfilled. start_min/finish_min are present, so
        -- derive from them rather than defaulting to zero: these players did
        -- appear, and a zero would corrupt any per-90 rate.
        coalesce(
            {{ to_int('s.minutes_played') }},
            {{ to_int('s.finish_min') }} - {{ to_int('s.start_min') }}
        )                                               as minutes,
        {{ to_int('s.start_min') }}                     as start_min,
        {{ to_int('s.finish_min') }}                    as finish_min,

        -- attacking
        {{ to_int('s.goals') }}                         as goals,
        {{ to_int('s.assists') }}                       as assists,
        {{ to_num('s.xg') }}                            as xg,
        {{ to_num('s.xa') }}                            as xa,
        {{ to_num('s.xgot') }}                          as xgot,
        {{ to_int('s.total_shots') }}                   as shots,
        {{ to_int('s.shots_on_target') }}               as shots_on_target,
        {{ to_int('s.big_chances_missed') }}            as big_chances_missed,
        {{ to_int('s.touches_opposition_box') }}        as touches_opp_box,
        {{ to_int('s.chances_created') }}               as chances_created,

        -- defensive contribution components, per position:
        -- DEF need 10 CBIT; MID/FWD need 12 including recoveries.
        {{ to_int('s.tackles') }}                       as tackles,
        {{ to_int('s.tackles_won') }}                   as tackles_won,
        {{ to_int('s.interceptions') }}                 as interceptions,
        {{ to_int('s.blocks') }}                        as blocks,
        {{ to_int('s.clearances') }}                    as clearances,
        {{ to_int('s.headed_clearances') }}             as headed_clearances,
        {{ to_int('s.recoveries') }}                    as recoveries,
        {{ to_int('s.defensive_contributions') }}       as defcon_published,

        -- goalkeeping
        {{ to_int('s.saves') }}                         as saves,
        {{ to_int('s.saves_inside_box') }}              as saves_inside_box,
        {{ to_num('s.goals_prevented') }}               as goals_prevented,
        {{ to_num('s.xgot_faced') }}                    as xgot_faced,
        {{ to_int('s.sweeper_actions') }}               as sweeper_actions,
        {{ to_int('s.high_claim') }}                    as high_claims,

        -- Goals conceded while THIS PLAYER was on the pitch. A player
        -- substituted before a goal keeps their clean sheet, so this rather
        -- than a team total is what the scoring rule needs.
        {{ to_int('s.team_goals_conceded') }}           as goals_conceded_on_pitch,
        {{ to_int('s.goals_conceded') }}                as goals_conceded,

        {{ to_int('s.penalties_scored') }}              as penalties_scored,
        {{ to_int('s.penalties_missed') }}              as penalties_missed,

        -- from the match
        m.season,
        m.gameweek,
        m.competition,
        m.is_league,
        m.kickoff_utc,

        s._source_key                                   as source_key,
        s._ingested_at                                  as ingested_at

    from source s
    inner join matches m on s.match_id = m.match_id
)

select * from renamed