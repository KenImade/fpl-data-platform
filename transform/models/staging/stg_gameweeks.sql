{{ config(materialized='view') }}

/*
    stg_gameweeks — the deadline calendar.

    The most important column here is deadline_time. Everything in the
    point-in-time layer keys off it: a capture is usable as a feature for
    gameweek N if and only if it happened before N's deadline.

    Note `snapshot_time` in the source is NOT a per-gameweek capture time —
    every row carries the same fixed value, so it cannot be used as
    provenance. That was checked when the source was first assessed.
*/

with source as (
    select * from {{ source('bronze', 'ci_gameweek_summaries') }}
),

renamed as (
    select
        _season                                     as season,
        {{ to_int('id') }}                          as gameweek,
        name                                        as gameweek_name,

        nullif(deadline_time, '')::timestamptz      as deadline_utc,
        {{ to_int('deadline_time_epoch') }}         as deadline_epoch,

        finished::boolean                           as is_finished,
        data_checked::boolean                       as is_data_checked,

        {{ to_num('average_entry_score') }}         as average_score,
        {{ to_int('highest_score') }}               as highest_score,
        {{ to_int('most_selected') }}               as most_selected_player_id,
        {{ to_int('most_captained') }}              as most_captained_player_id,
        {{ to_int('most_transferred_in') }}         as most_transferred_in_player_id,
        {{ to_int('transfers_made') }}              as transfers_made,

        _source_key                                 as source_key,
        _ingested_at                                as ingested_at

    from source
)

select * from renamed