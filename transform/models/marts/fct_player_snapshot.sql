{{ config(materialized='table') }}

/*
    fct_player_snapshot — player state as at each gameweek deadline.

    THE POINT-IN-TIME BOUNDARY. Every feature the prediction layer uses must
    read from here and nowhere else.

    That is not a convention, it is the guarantee. A row exists here only if
    it was captured strictly before the deadline it is attached to. So a
    feature built from this table CANNOT contain post-deadline information —
    not because someone was careful, but because the schema makes it
    unavailable.

    Reading stg_fpl_players directly bypasses this. So does joining
    stg_player_gameweek_stats for anything predictive: those are outcomes,
    known only after the fact, and belong on the label side.

    snapshot_id is stamped on every row so a prediction can name exactly the
    state it was made from. Reproducibility is then: rebuild the snapshot,
    rebuild the features, assert the prediction is identical.
*/

with snapshots as (
    select season, gameweek, snapshot_id, snapshot_at, deadline_utc,
           hours_before_deadline, is_usable
    from {{ ref('fct_deadline_snapshot') }}
    where has_snapshot
),

players as (
    select * from {{ ref('stg_fpl_players') }}
)

select
    s.snapshot_id,
    s.season,
    s.gameweek,
    s.deadline_utc,
    s.snapshot_at,
    s.hours_before_deadline,
    s.is_usable,

    p.player_id,
    p.player_code,
    p.team_code,
    p.web_name,
    p.position,

    p.price_tenths,
    p.selected_by_percent,
    p.status,
    p.news,
    p.chance_of_playing_next,

    -- Squad membership. NULL in captures predating these fields — absence is
    -- not the same as false, which is why the spine coalesces rather than
    -- filtering on them directly.
    p.removed,
    p.can_select,

    -- Set-piece duty at the deadline. Deadline-known state: a penalty taker's
    -- goal expectation differs materially from a teammate with identical xG.
    p.penalties_order,
    p.direct_freekicks_order,
    p.corners_order,

    -- When the news flag was set. A flag raised at Friday's press conference
    -- means something different from one standing since August.
    p.news_added,
    extract(epoch from (s.deadline_utc - p.news_added)) / 3600
                                                    as hours_since_news_added,

    -- FPL's own projection, as at the deadline. The baseline any model has
    -- to beat, captured at the same instant the model's features were.
    p.ep_next,

    -- Cumulative season-to-date, correct AS AT the snapshot. Safe as a
    -- feature for this exact reason; differencing them is not (ADR 0005).
    p.cumulative_points,
    p.cumulative_minutes,
    p.cumulative_starts,
    -- Pre-season captures carry the PREVIOUS season's totals until FPL rolls
    -- the bootstrap over. A GW1 snapshot may therefore hold last season's
    -- figures rather than zeroes. This is a heuristic on gameweek, not a
    -- detection of the rollover itself — verify against cumulative_points
    -- before relying on it.
    s.gameweek = 1                                  as maybe_prior_season_cumulative,

    current_timestamp                               as built_at

from snapshots s
inner join players p
    on p.captured_at = s.snapshot_at