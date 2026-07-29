{{ config(materialized='table') }}

/*
    fct_deadline_snapshot — which capture is authoritative for each gameweek.

    The original design called for a job firing at deadline minus five
    minutes. That isn't needed: the sensor already captures every 15 minutes
    in the six hours before a deadline, so the point-in-time record exists in
    raw. What was missing is a way to ADDRESS it — given a gameweek, which
    capture was the last one before the deadline.

    Being a query over immutable inputs rather than a scheduled side effect
    means the leakage test is trivial: recompute and assert it matches. A job
    would have produced state you'd have to trust.

    hours_before_deadline is the quality metric. The capture cadence tightens
    to 15 minutes inside the deadline window, so a healthy value is under
    0.25. Anything above 3 means the sensor missed its window and the
    features for that gameweek are staler than intended — which is a fact
    about the data, and belongs in the warehouse rather than in a log.
*/

with gameweeks as (
    select season, gameweek, deadline_utc
    from {{ ref('stg_gameweeks') }}
),

captures as (
    select distinct captured_at
    from {{ ref('stg_fpl_players') }}
),

-- Last capture strictly before each deadline. Strictly: a capture at the
-- deadline instant is already too late, since prices and news are locked
-- the moment it passes.
resolved as (
    select
        g.season,
        g.gameweek,
        g.deadline_utc,
        max(c.captured_at) as snapshot_at
    from gameweeks g
    left join captures c
        on c.captured_at < g.deadline_utc
    group by 1, 2, 3
)

select
    season,
    gameweek,
    deadline_utc,
    snapshot_at,

    -- Deterministic identity. Stamped on every prediction so it can be
    -- reproduced from exactly the inputs that produced it.
    season || '-gw' || lpad(gameweek::text, 2, '0') as snapshot_id,

    extract(epoch from (deadline_utc - snapshot_at)) / 3600
                                                    as hours_before_deadline,

    snapshot_at is not null                         as has_snapshot,

    current_timestamp                               as built_at

from resolved