{{ config(materialized='view') }}

-- FPL's own per-gameweek view. Deliberately NARROW: ten columns out of 87.
--
-- player_gameweek_stats supersedes this for everything scoring-related — it's
-- discrete per gameweek where these are cumulative season totals, so anything
-- taken from here would need differencing and would inherit the retroactive
-- restatement problem (see ADR 0005).
--
-- What it uniquely provides:
--
--   ep_next    FPL's own projection. The baseline any model has to beat,
--              and the reason this model exists at all.
--   now_cost   Price at snapshot. Note this is a DAILY snapshot, so the
--              value is whatever the mirror caught, not the price at the
--              deadline. Deadline-precise prices come from our own captures.
--   news       Injury text, same caveat.
--
-- Everything else is either superseded or derivable.

with source as (
    select * from {{ source('bronze', 'ci_playerstats') }}
),

renamed as (
    select
        _season                                         as season,
        {{ to_int('id') }}                              as player_id,
        {{ to_int('gw') }}                              as gameweek,

        -- the baseline to beat
        {{ to_num('ep_next') }}                         as ep_next,
        {{ to_num('ep_this') }}                         as ep_this,

        -- market, at snapshot precision only
        {{ to_int('now_cost') }}                        as price_tenths,
        {{ to_num('selected_by_percent') }}             as selected_by_percent,
        {{ to_num('form') }}                            as form,

        -- availability, at snapshot precision only
        status,
        nullif(news, '')                                as news,
        {{ to_int('chance_of_playing_next_round') }}    as chance_of_playing_next,

        _source_key                                     as source_key,
        _ingested_at                                    as ingested_at

    from source
)

select * from renamed