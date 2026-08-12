{{ config(materialized='table', schema='features') }}

/*
    feat_player_gameweek_spine — one row per player per gameweek, across both
    the live and reconstructed paths.

    WHY THIS EXISTS. feat_player_form and feat_player_load are grained per
    gameweek, not per fixture, and both need to know which players existed at
    which deadline. Reading fct_player_snapshot directly — as they did — means
    they produce nothing for seasons that predate our captures, so the
    historical spine's 32k labelled rows would have no features attached.

    This is the single roster both window models join to. Neither of them
    branches on season, and neither knows which path a row came from.

    GRAIN: (snapshot_id, player_id). The fixture-grain spines collapse to this
    because a double gameweek does not change what a manager knew at the
    deadline — form and load are identical across both fixtures, and fanning
    back out happens in feat_training_set.

    WHAT IS DELIBERATELY ABSENT. No capture-sourced state: no price, status,
    news, chance_of_playing_next or ep_next. Those exist on the live path only,
    and emitting them as null for reconstructed rows would blur "this feature
    does not exist for this season" into "this player had no news" — two very
    different things that a model cannot tell apart once they are both null.
    They are joined in at feat_training_set, from fct_player_snapshot, where
    their absence lines up with a season boundary a reader can see.

    is_reconstructed carries the distinction downstream so anything reading
    this can tell which rows are missing those features by construction.
*/

with live as (
    select distinct
        snapshot_id,
        season,
        gameweek,
        deadline_utc,
        player_id,
        player_code,
        team_code,
        position,
        -- The live path has no prior-appearance count of its own. It is
        -- computed here rather than in the spine so both paths derive it
        -- identically — see prior_appearances below.
        false                                           as is_reconstructed
    from {{ ref('feat_player_fixture_spine') }}
),

historical as (
    select distinct
        snapshot_id,
        season,
        gameweek,
        deadline_utc,
        player_id,
        player_code,
        team_code,
        position,
        true                                            as is_reconstructed
    from {{ ref('feat_player_fixture_spine_historical') }}
),

/*
    The two paths are disjoint by construction: the historical spine excludes
    any season appearing in fct_player_snapshot. A union all is therefore
    correct, and a plain union would only hide a regression in that filter.
    The uniqueness test on (snapshot_id, player_id) is what catches it.
*/
combined as (
    select * from live
    union all
    select * from historical
),

/*
    Prior league appearances, strictly before the deadline, computed once for
    both paths.

    The historical spine computes this itself because it needs
    had_appeared_before to explain its inflated negative class. Recomputing it
    here rather than carrying it through keeps the two paths from drifting: one
    definition, one place, applied to live rows as well — where it is just as
    useful, since a fringe squad member in the live roster has the same low
    appearance rate as one in the reconstructed roster.

    count(distinct match_id), not count(*): fct_player_gw is per player per
    fixture, but joining through a team-grain relation would double every
    count. Distinct on the match is the safe formulation either way.
*/
prior as (
    select
        c.snapshot_id,
        c.player_id,
        count(distinct o.match_id)                      as prior_appearances,
        max(o.kickoff_utc)                              as last_appearance_at,
        sum(o.minutes)                                  as prior_minutes
    from combined c
    inner join {{ ref('fct_player_gw') }} o
        on  o.season      = c.season
        and o.player_id   = c.player_id
        and o.is_league
        and o.minutes     > 0
        and o.kickoff_utc is not null
        and o.kickoff_utc < c.deadline_utc
    group by 1, 2
)

select
    c.snapshot_id,
    c.season,
    c.gameweek,
    c.deadline_utc,

    c.player_id,
    c.player_code,
    c.team_code,
    c.position,

    /*
        Prior involvement. Zero is meaningful rather than missing: a player
        with no prior appearance is usually not yet signed, not registered, or
        not in the manager's plans, and their appearance rate is an order of
        magnitude lower than an established starter's.

        On the reconstructed path this also absorbs the roster inflation
        described in feat_player_fixture_spine_historical — the season squad
        includes players who had not yet joined, and this is the feature that
        lets the model discount them without a filter that would have had to
        look forward to know.
    */
    coalesce(p.prior_appearances, 0)                    as prior_appearances,
    coalesce(p.prior_appearances, 0) > 0                as had_appeared_before,
    coalesce(p.prior_minutes, 0)                        as prior_minutes,
    p.last_appearance_at,
    extract(epoch from (c.deadline_utc - p.last_appearance_at)) / 86400
                                                        as days_since_last_appearance,

    -- True where the row was reconstructed from published deadlines rather
    -- than from a capture. Capture-sourced features are structurally
    -- unavailable for these rows, not merely null.
    c.is_reconstructed,

    current_timestamp                                   as built_at

from combined c

left join prior p
    on  p.snapshot_id = c.snapshot_id
    and p.player_id   = c.player_id