{{ config(materialized='table', schema='features') }}

/*
    feat_player_fixture_spine_historical — the same shape as
    feat_player_fixture_spine, reconstructed for seasons that predate our own
    captures.

    WHY THIS EXISTS. The live spine takes its roster from fct_player_snapshot,
    which only exists where a deadline capture does. Captures begin in 2026/27,
    so 2025/26 — the only season with COMPLETED fixtures and therefore the only
    source of labels — produces no rows there. Without this model there is no
    supervised training data at all.

    WHAT IS LOST. Everything a capture provides and nothing else can: price,
    ownership, status, news, chance_of_playing_next, ep_next. Those are among
    the strongest predictors of non-appearance, so a model trained here will be
    weaker than one trained on live data once 2026/27 accumulates. Form, load,
    team strength and Elo all derive from fixtures rather than captures and are
    unaffected.

    THE ROSTER IS THE SEASON SQUAD, NOT THE GAMEWEEK SQUAD. stg_players carries
    one row per player per season, so a January signing appears from GW1 and a
    January departure appears through GW38. Those rows are not real selection
    opportunities and they inflate the negative class.

    Bounding the roster by a player's first and last observed appearance would
    remove them, but that decides availability using information from after the
    deadline — precisely the leak this layer exists to prevent. So the rows stay
    and had_appeared_before carries the signal instead: a player with no prior
    appearance this season is usually either not yet signed or not in the
    manager's plans, and the model can learn that from a feature rather than
    from a filter someone had to trust.

    CONSEQUENCE FOR THE APPEARANCE RATE. Expect it well below the 30-60% band
    the live spine targets, because of that inflation. Compare like with like by
    filtering on had_appeared_before before reading anything into it.

    THE DEADLINE IS STILL THE BOUNDARY. dim_gameweek.deadline_utc replaces
    fct_deadline_snapshot.deadline_utc, so every downstream window bounds
    identically to the live path. The point-in-time guarantee is weaker here in
    that it rests on the published deadline rather than on a capture that
    provably predates it — but it is the same boundary.
*/

with gameweeks as (
    select
        season,
        gameweek,
        deadline_utc,
        -- season || '-gw01' matches fct_deadline_snapshot's construction, so
        -- the two spines share a key space and can be unioned or compared
        -- without translating identifiers.
        season || '-gw' || lpad(gameweek::text, 2, '0')  as snapshot_id
    from {{ ref('dim_gameweek') }}
    where deadline_utc is not null
      -- Only seasons WITHOUT captures. 2026/27 onward is served by the live
      -- spine, and producing both would double every row.
      and season not in (
          select distinct season from {{ ref('fct_player_snapshot') }}
      )
),

-- The season squad. One row per player per season, so this is every player
-- registered at any point, not the squad as at any given deadline.
roster as (
    select
        season,
        player_id,
        player_code,
        team_code,
        position
    from {{ ref('stg_players') }}
    where team_code is not null
      -- Position drives every scoring rule, so a null-position row cannot be
      -- scored and would only add an unlabellable negative.
      and position is not null
),

fixtures as (
    select
        season, gameweek, match_id, team_code, opponent_code,
        is_home, kickoff_utc, elo, opponent_elo, elo_diff
    from {{ ref('fct_team_fixture') }}
    where is_league
      and kickoff_utc is not null
),

-- Label side. Left-joined below, so a missing row means did not appear —
-- which is the entire point of a spine.
outcome as (
    select
        season, match_id, player_id,
        minutes, goals, assists, xg, xa,
        goals_conceded_on_pitch, saves, defensive_actions,
        gw_points, gw_bonus, gw_bps, gw_clean_sheets, is_gw_primary
    from {{ ref('fct_player_gw') }}
),

/*
    Prior league appearances, strictly before the deadline. This is what
    distinguishes a player who is simply out of favour from one who had not yet
    signed — without looking forward to find out which.
*/
prior_appearances as (
    select
        g.snapshot_id,
        o.player_id,
        count(distinct f.match_id)                      as prior_appearances,
        max(f.kickoff_utc)                              as last_appearance_at
    from gameweeks g
    inner join fixtures f
        on  f.season      = g.season
        and f.kickoff_utc < g.deadline_utc
    inner join outcome o
        on  o.match_id = f.match_id
        and o.season   = f.season
        and o.minutes  > 0
    group by 1, 2
)

select
    g.snapshot_id,
    g.season,
    g.gameweek,
    g.deadline_utc,

    r.player_id,
    r.player_code,
    r.team_code,
    r.position,

    f.match_id,
    f.kickoff_utc,
    f.opponent_code,
    f.is_home,
    f.elo,
    f.opponent_elo,
    f.elo_diff,

    count(*) over (
        partition by g.snapshot_id, r.player_id
    )                                                   as fixtures_in_gw,

    /*
        Deadline-known state, such as it is. The live spine carries price,
        ownership, status, news, chance_of_playing_next and ep_next here; none
        of them exist for this season. They are deliberately NOT emitted as
        nulls — a column that is null for every row in a whole season is worse
        than an absent one, because it trains the model to ignore a feature
        that will be informative later.

        feat_training_set should therefore select these two spines separately
        rather than unioning them, or union them with the capture-sourced
        columns explicitly cast null and a season indicator alongside.
    */
    coalesce(pa.prior_appearances, 0)                   as prior_appearances,
    coalesce(pa.prior_appearances, 0) > 0               as had_appeared_before,
    pa.last_appearance_at,
    extract(epoch from (g.deadline_utc - pa.last_appearance_at)) / 86400
                                                        as days_since_last_appearance,

    -- Labels. NULL from the join where the player did not appear.
    coalesce(o.minutes, 0)                              as minutes,
    o.minutes is not null                               as did_appear,
    coalesce(o.minutes, 0) >= 60                        as played_60,
    o.goals, o.assists, o.xg, o.xa,
    o.goals_conceded_on_pitch, o.saves, o.defensive_actions,
    o.gw_points, o.gw_bonus, o.gw_bps, o.gw_clean_sheets,

    -- Marks rows built without captures, so anything reading both spines can
    -- tell which features are structurally absent rather than merely missing.
    true                                                as is_reconstructed,

    current_timestamp                                   as built_at

from gameweeks g

inner join roster r
    on r.season = g.season

inner join fixtures f
    on  f.season    = g.season
    and f.gameweek  = g.gameweek
    and f.team_code = r.team_code

left join outcome o
    on  o.season    = g.season
    and o.match_id  = f.match_id
    and o.player_id = r.player_id

left join prior_appearances pa
    on  pa.snapshot_id = g.snapshot_id
    and pa.player_id   = r.player_id