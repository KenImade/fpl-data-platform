{{ config(materialized='table', schema='features') }}

/*
    feat_team_fixture_spine — every league fixture a club could play, with the
    strength features known at that gameweek's deadline and the conceded label
    where one exists.

    GRAIN: (snapshot_id, team_code, match_id). Two rows per fixture, one per
    club, inherited from fct_team_fixture.

    ONE SPINE, NOT TWO. The player layer needs a live and a reconstructed path
    because rosters come from captures. Nothing here does: the fixture list and
    Elo come from fct_team_fixture, and the rolling rates from feat_team_strength,
    which is already keyed on a snapshot_id that spans both paths. Deadlines are
    therefore taken from feat_player_gameweek_spine rather than
    fct_deadline_snapshot — the latter has no rows before 2026-07-30, which would
    make this model empty of labels in exactly the season that has them.

    UNPLAYED FIXTURES STAY. goals_against is NULL for them and is_played says so.
    This is the prediction matrix as well as the source of the training set;
    building the label filter in here would force a second near-identical model
    to score against, with the feature logic duplicated across both.

    is_played reads from result rather than from goals_against, matching
    feat_team_strength. Both are proxies for a status field the source does not
    give us, but result's nullability is one documented decision in
    fct_team_fixture rather than an incidental property of a label column.
*/

with deadlines as (
    select distinct snapshot_id, season, gameweek, deadline_utc
    from {{ ref('feat_player_gameweek_spine') }}
),

fixtures as (
    select
        match_id,
        season,
        gameweek,
        team_code,
        opponent_code,
        is_home,
        kickoff_utc,
        goals_for,
        goals_against,
        elo,
        opponent_elo,
        elo_diff,
        result,
        matches_prior_14d,
        days_since_last_match
    from {{ ref('fct_team_fixture') }}
    where is_league
      -- A fixture with no assigned gameweek cannot join to a deadline. Excluded
      -- by name here rather than dropped silently by the inner join below.
      and gameweek is not null
),

/*
    Saves and shots faced, at team grain. fct_team_fixture carries goals but not
    shot detail, so the same summing pattern feat_team_strength uses for xG
    applies. Saves are almost entirely a keeper column; summing over all players
    is still correct and avoids depending on position being populated.
*/
team_match as (
    select
        season,
        match_id,
        team_code,
        sum(saves)                                  as saves,
        sum(shots_on_target)                        as shots_on_target
    from {{ ref('fct_player_gw') }}
    where is_league
    group by 1, 2, 3
)

select
    d.snapshot_id,
    d.season,
    d.gameweek,
    d.deadline_utc,

    f.match_id,
    f.team_code,
    f.opponent_code,
    f.kickoff_utc,

    -- ================================================================
    -- FEATURES. Everything below is known at deadline_utc.
    -- ================================================================
    f.is_home,

    -- Elo at kickoff of this fixture, from fct_team_fixture. Point-in-time by
    -- construction and the only feature here needing no windowing.
    f.elo,
    f.opponent_elo,
    f.elo_diff,

    -- Rolling rates, as at the deadline. own = this club's defensive record,
    -- opp = the attack it is about to face. The join keys are spelled out
    -- because inverting them would reverse every difficulty feature silently.
    own.elo_current                                 as own_elo_current,
    own.xg_against_per_match_5                      as own_xg_against_5,
    own.xg_against_per_match_10                     as own_xg_against_10,
    own.clean_sheet_rate_10                         as own_clean_sheet_rate_10,
    own.xg_against_home                             as own_xg_against_home,
    own.xg_against_away                             as own_xg_against_away,

    opp.elo_current                                 as opp_elo_current,
    opp.xg_for_per_match_5                          as opp_xg_for_5,
    opp.xg_for_per_match_10                         as opp_xg_for_10,
    opp.xg_for_home                                 as opp_xg_for_home,
    opp.xg_for_away                                 as opp_xg_for_away,

    case when f.is_home
         then opp.strength_attack_away
         else opp.strength_attack_home
    end                                             as opp_attack_strength,

    coalesce(own.is_cold_start, true)               as own_is_cold_start,
    coalesce(opp.is_cold_start, true)               as opp_is_cold_start,

    /*
        Congestion. NULL for any fixture with no kickoff time — 2025/26 GW34-38
        are undated, so this is a real gap rather than a rare one, and
        fct_team_fixture nulls congestion for those rows deliberately.

        Emitted null with a flag rather than coalesced to zero: zero rest days
        and unknown rest days are opposite signals. Imputation is the model's
        decision, not this layer's.
    */
    f.matches_prior_14d,
    least(f.days_since_last_match, 14)              as days_rest,
    f.days_since_last_match is not null             as has_congestion,

    -- ================================================================
    -- LABELS. NULL on fixtures that have not been played.
    -- ================================================================
    f.result is not null                            as is_played,
    f.goals_against,
    f.goals_for,
    f.goals_against = 0                             as is_clean_sheet,
    tm.saves                                        as team_saves,
    opp_tm.shots_on_target                          as shots_on_target_faced,

    current_timestamp                               as built_at

from deadlines d

inner join fixtures f
    on  f.season   = d.season
    and f.gameweek = d.gameweek

left join {{ ref('feat_team_strength') }} own
    on  own.snapshot_id = d.snapshot_id
    and own.team_code   = f.team_code

left join {{ ref('feat_team_strength') }} opp
    on  opp.snapshot_id = d.snapshot_id
    and opp.team_code   = f.opponent_code

left join team_match tm
    on  tm.match_id  = f.match_id
    and tm.team_code = f.team_code

-- The opponent's shots on target in this fixture are the shots this club faced.
left join team_match opp_tm
    on  opp_tm.match_id  = f.match_id
    and opp_tm.team_code = f.opponent_code