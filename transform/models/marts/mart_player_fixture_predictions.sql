{{ config(materialized='table') }}

/*
    mart_player_fixture_predictions — model output, shaped for the API.

    GRAIN: (season, gameweek, player_id, match_id). One row per fixture, so a
    double gameweek gives a player two rows. Consumers who want one row per
    player per gameweek should read mart_player_gameweek_predictions instead;
    this exists for anyone who needs to know which fixture the number belongs
    to.

    WHY THIS MART EXISTS AT ALL. The API could read predictions.player_gameweek
    directly, and then the model's storage shape would BE the public contract —
    every column rename in the modelling package would break third-party
    consumers. This layer means the two can move independently, and it is why
    "the API reads marts" stays literally true rather than approximately.

    ONE VERSION, CHOSEN DELIBERATELY. predictions.player_gameweek holds every
    version so a challenger can be compared against the incumbent on identical
    inputs. Serving latest-by-timestamp would discard that: a training run would
    silently change what consumers receive. predictions.active_model names the
    served version, and promotion is a separate act.

    NULLS ARE HONEST HERE. Only the minutes model exists, so every other
    component is null. A null means no model has been built for that component
    yet — not a prediction of zero — and the API's schema documents it that way.
*/

with active as (
    select model_name, model_version
    from {{ source('predictions', 'active_model') }}
    where model_name = 'minutes_ordinal'
),

predictions as (
    select p.*
    from {{ source('predictions', 'player_gameweek') }} p
    inner join active a
        on p.model_version = a.model_version
),

/*
    Fixture context, so a consumer can read the prediction without a second
    request. Opponent and home/away are what make an expected-points figure
    interpretable — 0.4 expected goals means something different at home to a
    promoted side than away at the league leaders.
*/
fixtures as (
    select
        match_id,
        team_code,
        opponent_code,
        opponent_name,
        is_home,
        kickoff_utc,
        elo,
        opponent_elo,
        elo_diff
    from {{ ref('fct_team_fixture') }}
    where is_league
)

select
    p.season,
    p.gameweek,
    p.player_id,
    p.player_code,
    p.match_id,

    -- Display fields, so the API does not need a join for a player list.
    d.web_name,
    d.full_name,
    d.position,
    d.team_code,
    d.team_name,
    d.team_short,
    d.price,

    -- Fixture context.
    f.opponent_code,
    f.opponent_name,
    f.is_home,
    f.kickoff_utc,
    f.elo_diff,

    -- ---------------------------------------------------------------
    -- MINUTES. The only component currently modelled.
    -- ---------------------------------------------------------------
    p.p_minutes_0,
    p.p_minutes_1_59,
    p.p_minutes_60,
    p.e_minutes,

    -- ---------------------------------------------------------------
    -- AWAITING A MODEL. Null means no model exists for this component, not a
    -- prediction of zero. Carried so the API's response shape is stable as
    -- components arrive.
    -- ---------------------------------------------------------------
    p.e_goals,
    p.e_assists,
    p.p_clean_sheet,
    p.e_saves,
    p.e_goals_conceded,
    p.p_defcon,
    p.e_bonus,
    p.e_cards,
    p.e_points,

    /*
        How much history the prediction rests on. A cold-start row is a
        positional prior wearing an estimate's clothing, and a consumer
        building a transfer tool deserves to know which they are looking at
        rather than discovering it from behaviour.
    */
    p.prior_appearances,
    p.is_cold_start,

    -- Provenance. snapshot_id names the exact feature state; model_version
    -- names the code and data that produced the number. Between them a
    -- prediction is reproducible, which is the claim the whole point-in-time
    -- layer exists to support.
    p.snapshot_id,
    p.model_version,
    p.predicted_at,

    current_timestamp                           as built_at

from predictions p

inner join {{ ref('dim_player') }} d
    on  d.season    = p.season
    and d.player_id = p.player_id

left join fixtures f
    on  f.match_id  = p.match_id
    and f.team_code = d.team_code