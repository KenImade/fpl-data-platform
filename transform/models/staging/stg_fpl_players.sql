{{ config(materialized='view') }}

/*
    stg_fpl_players — our own captures of bootstrap-static elements.

    This is the ONLY source of deadline-precise state. Core Insights mirrors
    once or twice a day; this captures every three hours, tightening to every
    fifteen minutes in the six hours before a deadline. The difference matters
    for exactly four fields — price, ownership, injury news, and availability
    — which move continuously and determine what was knowable when a manager
    had to decide.
    
    Grain is (captured_at, player_id). Every intra-day observation is kept.
    Collapsing to one row per day would discard the entire point of the
    capture cadence.

    Types are already correct: the ingestion layer validates against a Pydantic
    model and writes parquet with a declared schema, so unlike the Core
    Insights tables there is nothing to cast beyond FPL's three string-encoded
    numerics.

    NOT a substitute for stg_player_gameweek_stats. total_points and minutes
    here are cumulative season totals, and differencing them reintroduces the
    retroactive-restatement problem from ADR 0005.
*/

with source as (
    select * from {{ source('bronze', 'fpl_players') }}
),

renamed as (
    select
        captured_at,
        captured_at::date                           as capture_date,

        id                                          as player_id,
        code                                        as player_code,
        team_code,
        team                                        as team_id,

        web_name,
        trim(first_name || ' ' || second_name)      as full_name,

        element_type,
        case element_type
            when 1 then 'GKP'
            when 2 then 'DEF'
            when 3 then 'MID'
            when 4 then 'FWD'
        end                                         as position,

        -- The volatile four. Everything else in this table exists in
        -- Core Insights at adequate precision; these do not.
        now_cost                                    as price_tenths,
        {{ to_num('selected_by_percent') }}         as selected_by_percent,
        status,
        nullif(news, '')                            as news,
        chance_of_playing_next_round                as chance_of_playing_next,
        chance_of_playing_this_round                as chance_of_playing_this,

        -- FPL's own projection, at capture precision rather than daily.
        {{ to_num('ep_next') }}                     as ep_next,
        {{ to_num('ep_this') }}                     as ep_this,

        -- Cumulative season totals. Present for completeness; do NOT
        -- difference these — use stg_player_gameweek_stats instead.
        total_points                                as cumulative_points,
        event_points                                as gw_points_at_capture,
        minutes                                     as cumulative_minutes

    from source
)

select * from renamed