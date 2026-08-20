{{ config(materialized='view') }}

-- Fixtures across all competitions, from the tarball's By Tournament tree
-- plus the 2024/25 archive.
--
-- Three things to know:
--
-- 1. `tournament` matters. 2026/27 contains 97 friendlies filed under numbered
--    gameweeks, not GW0, alongside the Community Shield and Uefa Super Cup.
--    Anything computing form, fitness or congestion must filter on it or a
--    friendly counts as a league match.
--
-- 2. home_team/away_team are team_code (stable across seasons), not the
--    season-scoped team_id. Values exceed 20. Joining the wrong one produces
--    empty results rather than an error.
--
-- 3. Elo is per-match — the rating at kickoff, not a season attribute. That
--    gives the match model a point-in-time prior with no leakage.

with source as (
    select * from {{ source('bronze', 'ci_matches') }}
),

deduped as (
    select *,
        row_number() over (
            partition by match_id
            order by gameweek asc
        ) as _rn
    from source
),

renamed as (
    select
        match_id                                    as match_id,
        _season                                     as season,
        {{ to_int('gameweek') }}                    as gameweek,
        coalesce(tournament, 'prem')                as competition,
        coalesce(tournament, 'prem') = 'prem'       as is_league,

        nullif(kickoff_time, '')::timestamptz       as kickoff_utc,

        {{ to_int('fotmob_id') }}                   as fotmob_id,
        {{ to_int('home_team') }}                   as home_team_code,
        {{ to_int('away_team') }}                   as away_team_code,
        {{ to_num('home_team_elo') }}               as home_elo,
        {{ to_num('away_team_elo') }}               as away_elo,

        {{ to_int('home_score') }}                  as home_score,
        {{ to_int('away_score') }}                  as away_score,

        _competition                                as source_competition,
        _source_key                                 as source_key,
        _ingested_at                                as ingested_at

    from deduped where _rn = 1
)

select * from renamed