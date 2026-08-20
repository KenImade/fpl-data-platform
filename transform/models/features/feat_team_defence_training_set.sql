{{ config(materialized='view', schema='features') }}

/*
    feat_team_defence_training_set — the labelled subset of
    feat_team_fixture_spine, with the feature/label boundary made explicit.

    A view. Every feature is DEFINED once, in the spine; this model only
    SELECTS. Predictions score against the spine and training reads this, so
    the two cannot drift in definition — but the columns are named rather than
    starred, because a training matrix whose shape changes when someone adds a
    column upstream is not a matrix anyone can reproduce a model against.

    TWO FILTERS, BOTH LOAD-BEARING.

    is_played is the labelled/unlabelled split. It reads from result rather
    than from goals_against, matching feat_team_strength — both are proxies for
    a status field the source does not provide, but result's nullability is one
    documented decision in fct_team_fixture rather than an incidental property
    of a label column.

    The cold-start exclusion removes rows where every rolling rate is null. A
    club at its first deadline of a season has no xG history, so those rows
    would train the model on the imputation strategy rather than on football.
    They are still SCORED — Elo is defined for them, which is the whole reason
    Elo is the backbone — they are just not fitted on.

    WHAT IS NOT FILTERED, DELIBERATELY. Rows with has_congestion false, where
    the fixture has no kickoff time and matches_prior_14d and days_rest are
    null. 2025/26 GW34-38 are undated, which is roughly 100 of about 760 rows
    in the only season with labels. Dropping an eighth of the training set to
    avoid imputing two weak features is the wrong trade; the flag is carried so
    the model can price them.

    SAMPLE SIZE IS THE CONSTRAINT ON EVERYTHING DOWNSTREAM. Roughly 700 rows
    per completed season after both filters — two orders of magnitude below the
    minutes matrix, against a dozen features. That is why the defence model is
    a regularised GLM and not a GBM, and why the feature list here is
    deliberately shorter than what the spine makes available.
*/

select
    -- ================================================================
    -- IDENTITY AND GRAIN. (snapshot_id, team_code, match_id).
    -- deadline_utc is the walk-forward split key: a fold boundary must
    -- fall on a deadline, not on a kickoff, or a fold can contain
    -- fixtures whose features were built from the fold it is tested on.
    -- ================================================================
    snapshot_id,
    season,
    gameweek,
    deadline_utc,
    match_id,
    team_code,
    opponent_code,
    kickoff_utc,

    -- ================================================================
    -- FEATURES. Everything below is known at deadline_utc.
    -- ================================================================

    -- Structural.
    is_home,

    -- Elo. Point-in-time by construction and defined for every club
    -- including the newly promoted. Positive elo_diff is the stronger
    -- side, so it must correlate NEGATIVELY with goals_against.
    elo_diff,
    own_elo_current,
    opp_elo_current,

    -- This club's defensive record. xG rather than goals: same signal,
    -- a fraction of the variance, which decides the model at n=700.
    own_xg_against_5,
    own_xg_against_10,
    own_xg_against_home,
    own_xg_against_away,

    -- The attack about to be faced. Joined on opponent_code upstream.
    opp_xg_for_5,
    opp_xg_for_10,
    opp_xg_for_home,
    opp_xg_for_away,

    -- Congestion. NULL where the fixture is undated; has_congestion is
    -- what makes those nulls interpretable, and imputation is the
    -- model's decision rather than this layer's.
    matches_prior_14d,
    days_rest,
    has_congestion,

    /*
        DELIBERATELY EXCLUDED, though present on the spine:

        own_clean_sheet_rate_10 — the outcome this model predicts,
        measured over 10 matches. Standard error near 0.14 on a base rate
        of 0.28, so it is mostly noise, and it is collinear with
        own_xg_against_10 on the signal it does carry.

        opp_attack_strength — FPL's own rating. Updated infrequently and
        crude next to Elo, which is already here. Carried on the spine for
        comparison against ep_next, not for fitting.

        own_is_cold_start, opp_is_cold_start — constant false after the
        filter below. A constant column is not a feature.
    */

    -- ================================================================
    -- LABELS. Outcomes, known only after kickoff. Never NULL here.
    -- ================================================================

    -- The primary target. A rate is fitted to this and every defensive
    -- component falls out of the resulting distribution: P(0) is the
    -- clean sheet, E[floor(k/2)] the concession penalty.
    goals_against,
    is_clean_sheet,

    -- The saves model's target and its volume driver. Separate fit,
    -- same features plus these.
    team_saves,
    shots_on_target_faced,

    -- Not a target. Carried because a defence that concedes few goals
    -- while shipping many shots is a different thing from one that
    -- concedes few shots, and residual analysis needs to tell them apart.
    goals_for,

    current_timestamp                               as built_at

from {{ ref('feat_team_fixture_spine') }}

where is_played
  and not own_is_cold_start
  and not opp_is_cold_start