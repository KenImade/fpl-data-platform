{{ config(materialized='view') }}

-- Club dimension, one row per team per season.
--
-- `code` is stable across seasons; `id` is the season-scoped identifier and
-- gets reassigned. Matches carry codes, so that's the join key.
--
-- Elo also appears here, but per-match Elo on stg_matches is what modelling
-- should use — that's the rating at kickoff, whereas this is whatever the
-- snapshot happened to catch. Kept for reference, not for features.
--
-- The strength_* ratings are FPL's own, on a 1-5 scale for `strength` and a
-- larger scale for the split ratings. They're crude next to Elo but they're
-- what FPL's own fixture difficulty is built from, so worth having when
-- comparing against ep_next.

with source as (
    select * from {{ source('bronze', 'ci_teams') }}
),

renamed as (
    select
        _season                                     as season,
        {{ to_int('code') }}                        as team_code,
        {{ to_int('id') }}                          as team_id,
        name                                        as team_name,
        short_name                                  as team_short,

        {{ to_int('strength') }}                    as strength,
        {{ to_int('strength_overall_home') }}       as strength_overall_home,
        {{ to_int('strength_overall_away') }}       as strength_overall_away,
        {{ to_int('strength_attack_home') }}        as strength_attack_home,
        {{ to_int('strength_attack_away') }}        as strength_attack_away,
        {{ to_int('strength_defence_home') }}       as strength_defence_home,
        {{ to_int('strength_defence_away') }}       as strength_defence_away,

        {{ to_num('elo') }}                         as elo_snapshot,

        {{ to_int('pulse_id') }}                    as pulse_id,
        fotmob_name,

        _source_key                                 as source_key,
        _ingested_at                                as ingested_at

    from source
)

select * from renamed