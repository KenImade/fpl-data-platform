/*
    predictions.player_gameweek — model output, one row per player per fixture
    per model version.

    WRITTEN BY fpl_modelling, READ BY dbt. The API never reads this directly;
    it reads a mart built on top, so the model's storage shape and the public
    contract can change independently. That indirection is the reason the API's
    "reads marts only" rule stays literally true.

    MODEL VERSION IS IN THE PRIMARY KEY, not a column to overwrite. Two things
    follow. A new model can be scored against the same snapshot as the one
    currently serving, without a rerun, so "is it actually better on live data"
    is answerable. And months later "what did we predict at the time" has an
    answer, which it does not if each run clobbers the last. Twenty thousand
    rows a gameweek makes the storage cost irrelevant next to that.

    COMPONENTS ARE STORED, NOT JUST THE TOTAL. When a prediction is wrong the
    useful question is which part was wrong — the minutes model or the goals
    model — and that is unrecoverable from a sum. Every component is nullable
    because they arrive one model at a time; a null means "no model for this
    yet", not "predicted zero".

    SNAPSHOT_ID IS THE REPRODUCIBILITY ANCHOR. It names exactly the feature
    state the prediction was made from. Rebuild that snapshot, rebuild the
    features, rerun the version, assert the row is identical.
*/

create schema if not exists predictions;

create table if not exists predictions.player_gameweek (
    -- identity and grain
    snapshot_id         text        not null,
    season              text        not null,
    gameweek            int         not null,
    player_id           int         not null,
    player_code         int,
    match_id            text        not null,

    -- Which model produced this. Part of the key so versions coexist.
    model_version       text        not null,
    predicted_at        timestamptz not null default now(),

    -- ---------------------------------------------------------------
    -- MINUTES. The only component currently modelled.
    -- ---------------------------------------------------------------
    p_minutes_0         numeric,    -- did not play
    p_minutes_1_59      numeric,    -- appeared, under 60
    p_minutes_60        numeric,    -- 60+, the appearance-points threshold
    e_minutes           numeric,    -- expected minutes, for scaling per-90 rates

    -- ---------------------------------------------------------------
    -- AWAITING A MODEL. Nullable because they arrive one at a time; a null
    -- here means no model exists yet, not a prediction of zero.
    -- ---------------------------------------------------------------
    e_goals             numeric,
    e_assists           numeric,
    p_clean_sheet       numeric,
    e_saves             numeric,
    e_goals_conceded    numeric,
    p_defcon            numeric,    -- defensive contribution threshold met
    e_bonus             numeric,
    e_cards             numeric,

    -- Recombined through the scoring rules. Null until enough components
    -- exist to make it meaningful.
    e_points            numeric,

    /*
        How much the model had to work with. A prediction from a cold start —
        a promoted club's signing in GW1 — is a different object from one made
        with ten fixtures of history, and the API should be able to say so
        rather than presenting both as equally solid.
    */
    prior_appearances   int,
    is_cold_start       boolean,

    primary key (snapshot_id, player_id, match_id, model_version)
);

-- The serving path reads one gameweek of one version at a time.
create index if not exists player_gameweek_serving_idx
    on predictions.player_gameweek (season, gameweek, model_version);

-- Backtesting reads one player across every gameweek.
create index if not exists player_gameweek_player_idx
    on predictions.player_gameweek (player_id, season);

comment on table predictions.player_gameweek is
    'Model output. Written by fpl_modelling, read by dbt. Never read directly by the API.';

comment on column predictions.player_gameweek.model_version is
    'Identifies code and data together. Part of the key so versions coexist rather than overwrite.';

comment on column predictions.player_gameweek.snapshot_id is
    'The feature state this prediction was made from. Rebuild it to reproduce the row.';