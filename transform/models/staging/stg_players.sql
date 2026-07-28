{{ config(materialized='view') }}

-- Player identity across seasons.
--
-- player_code is FPL's STABLE identifier — the same human keeps it year to
-- year. player_id is the season-scoped element ID and gets reassigned each
-- August, so joining on it across seasons silently attaches one player's
-- history to another. Everything cross-season keys on player_code.
--
-- team_code is likewise stable where team_id is not, which is why matches
-- carries codes rather than ids.
--
-- `position` is a full word ("Midfielder"), not FPL's element_type integer.
-- The mapping is duplicated in fpl_core.models.position_from_name; if either
-- changes, both must.

with source as (
    select * from {{ source('bronze', 'ci_players') }}
    -- The 2024/25 archive carries two entirely blank rows — no code, id,
    -- name or position. Trailing lines in the CSV, not players.
    where nullif(trim(coalesce(player_code, '')), '') is not null
),

renamed as (
    select
        _season                                     as season,
        {{ to_int('player_code') }}                 as player_code,
        {{ to_int('player_id') }}                   as player_id,
        {{ to_int('team_code') }}                   as team_code,

        first_name,
        second_name,
        web_name,
        trim(first_name || ' ' || second_name)      as full_name,

        position                                    as position_name,
        case position
            when 'Goalkeeper' then 1
            when 'Defender'   then 2
            when 'Midfielder' then 3
            when 'Forward'    then 4
        end                                         as element_type,
        case position
            when 'Goalkeeper' then 'GKP'
            when 'Defender'   then 'DEF'
            when 'Midfielder' then 'MID'
            when 'Forward'    then 'FWD'
            -- 20 players in the 2024/25 archive are recorded as 'Unknown'.
            -- Kept rather than dropped: they may have match data, and a
            -- missing player is harder to notice than a null position.
            when 'Unknown'    then null
        end                                         as position,

        _source_key                                 as source_key,
        _ingested_at                                as ingested_at

    from source
)

select * from renamed