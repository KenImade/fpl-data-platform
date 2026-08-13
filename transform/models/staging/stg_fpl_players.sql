{{ config(materialized='view') }}

/*
    stg_fpl_players — our own captures of bootstrap-static elements.

    This is the ONLY source of deadline-precise state. Core Insights mirrors
    once or twice a day; this captures every three hours, tightening to every
    fifteen minutes in the six hours before a deadline. The difference matters
    for the fields that move continuously and determine what was knowable when
    a manager had to decide: price, ownership, injury news, availability, and
    set-piece duty.

    It is also the only source of two things Core Insights does not carry at
    all — team_join_date, which is the sole direct evidence of a mid-season
    transfer, and news_added, which dates the injury flag.

    Grain is (captured_at, player_id). Every intra-day observation is kept.
    Collapsing to one row per day would discard the entire point of the
    capture cadence.

    Types are already correct: the ingestion layer validates against a Pydantic
    model and writes parquet with a declared schema, so unlike the Core
    Insights tables there is nothing to cast beyond FPL's string-encoded
    numerics and dates.

    NOT a substitute for stg_player_gameweek_stats. total_points, minutes and
    starts here are cumulative season totals, and differencing them
    reintroduces the retroactive-restatement problem from ADR 0005.
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

        -- Club tenure. Any fixture before this date was played for a
        -- different club. stg_players carries one club per season, so this
        -- is the only direct evidence of a mid-season transfer.
        nullif(team_join_date, '')::date            as team_join_date,

        -- Set-piece duty. Order within the club, 1 = first choice,
        -- NULL = not on duty. A penalty taker's goal expectation differs
        -- materially from a teammate with identical xG per 90.
        penalties_order,
        direct_freekicks_order,
        corners_and_indirect_freekicks_order        as corners_order,

        -- When `news` was set. A flag added an hour before a deadline means
        -- something different from one standing since August.
        nullif(news_added, '')::timestamptz         as news_added,

        -- Squad membership. Both NULL in captures predating the fields;
        -- absence is not the same as false.
        removed,
        can_select,

        -- player_code is assumed permanent and is the cross-season join key.
        -- has_temporary_code says it sometimes isn't: a placeholder replaced
        -- once Opta assigns a real one, which splits a career across two
        -- codes in dim_person.
        has_temporary_code,
        opta_code,

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
        --
        -- Note these carry the PREVIOUS season's totals in pre-season
        -- captures, until FPL rolls the bootstrap over at the season start.
        -- A GW1 snapshot therefore holds last season's figures, not zeroes.
        total_points                                as cumulative_points,
        event_points                                as gw_points_at_capture,
        minutes                                     as cumulative_minutes,
        starts                                      as cumulative_starts

    from source
)

select * from renamed