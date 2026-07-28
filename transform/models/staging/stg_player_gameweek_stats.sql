{{ config(materialized='view') }}

-- Discrete per-gameweek FPL fields. Verified against differenced cumulative
-- totals (303/303 on minutes at GW11), so no differencing is needed and
-- retroactive restatements can't appear as phantom events.
--
-- CAVEAT: grained at player x gameweek, NOT per fixture. Double gameweeks
-- are aggregated -- GW26 2025/26 shows 180-minute rows. Per-match rules
-- (the 60-minute threshold, clean sheets, defensive contribution) cannot be
-- evaluated here. See ADR 0005.

with source as (
    select * from {{ source('bronze', 'ci_player_gameweek_stats') }}
),

renamed as (
    select
        _season                                             as season,
        {{ to_int('id') }}                                  as player_id,
        {{ to_int('gw') }}                                  as gameweek,

        -- appearance
        {{ to_int('minutes') }}                             as minutes,
        {{ to_int('starts') }}                              as starts,

        -- attacking
        {{ to_int('goals_scored') }}                        as goals,
        {{ to_int('assists') }}                             as assists,
        {{ to_num('expected_goals') }}                      as xg,
        {{ to_num('expected_assists') }}                    as xa,
        {{ to_num('expected_goal_involvements') }}          as xgi,

        -- defensive
        {{ to_int('clean_sheets') }}                        as clean_sheets,
        {{ to_int('goals_conceded') }}                      as goals_conceded_on_pitch,
        {{ to_num('expected_goals_conceded') }}             as xgc,
        {{ to_int('saves') }}                               as saves,

        -- Defensive contribution components. The published aggregate bundles
        -- recoveries for defenders, who are not credited for them -- verified
        -- across 3,519 defender-gameweeks, every divergent row matching
        -- tackles + cbi + recoveries exactly. Always derive from components.
        {{ to_int('tackles') }}                             as tackles,
        {{ to_int('clearances_blocks_interceptions') }}     as cbi,
        {{ to_int('recoveries') }}                          as recoveries,
        {{ to_int('defensive_contribution') }}              as defcon_published,

        -- discipline
        {{ to_int('yellow_cards') }}                        as yellow_cards,
        {{ to_int('red_cards') }}                           as red_cards,
        {{ to_int('own_goals') }}                           as own_goals,
        {{ to_int('penalties_saved') }}                     as penalties_saved,
        {{ to_int('penalties_missed') }}                    as penalties_missed,

        -- scoring
        {{ to_int('total_points') }}                        as points,
        {{ to_int('bonus') }}                               as bonus,
        {{ to_int('bps') }}                                 as bps,

        -- market
        {{ to_int('now_cost') }}                            as price_tenths,
        {{ to_num('selected_by_percent') }}                 as selected_by_percent,
        {{ to_int('transfers_in_event') }}                  as transfers_in,
        {{ to_int('transfers_out_event') }}                 as transfers_out,

        _source_key                                         as source_key,
        _ingested_at                                        as ingested_at

    from source
)

select * from renamed