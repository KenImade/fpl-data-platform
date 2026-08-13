{{ config(materialized='table') }}

/*
    mart_player_gameweek_predictions — one row per player per gameweek.

    GRAIN: (season, gameweek, player_id). The API's default prediction surface.

    WHY THIS EXISTS ALONGSIDE THE FIXTURE MART. A double gameweek gives a player
    two fixtures, and a consumer asking "how many points will Salah score in
    GW26" wants one number. Serving fixture grain by default would make every
    consumer implement the aggregation themselves, and most would forget —
    producing tools that silently halve a player's projection in exactly the
    gameweeks where the projection matters most.

    HOW COMPONENTS AGGREGATE. Expectations SUM across fixtures: two matches is
    two chances to score, and E[goals over both] = E[first] + E[second] by
    linearity, which holds whether or not the fixtures are independent.

    PROBABILITIES DO NOT SUM. P(60+ minutes) across two fixtures is not
    p1 + p2 — that can exceed 1 and means nothing anyway. What a consumer
    actually wants from a double is the probability of reaching the threshold in
    AT LEAST ONE fixture, which under independence is 1 - (1-p1)(1-p2).
    Independence is not quite right — a player injured in the first fixture
    misses the second — so this slightly overstates. That is documented rather
    than corrected, because correcting it needs a joint model the data does not
    support.

    The per-fixture probabilities remain available at fixture grain for anyone
    who needs them unaggregated.
*/

with fixture_predictions as (
    select * from {{ ref('mart_player_fixture_predictions') }}
),

aggregated as (
    select
        season,
        gameweek,
        player_id,

        count(*)                                    as fixtures_in_gw,
        count(*) > 1                                as is_double_gw,

        -- Expectations sum. Linearity holds regardless of independence.
        sum(e_minutes)                              as e_minutes,
        sum(e_goals)                                as e_goals,
        sum(e_assists)                              as e_assists,
        sum(e_saves)                                as e_saves,
        sum(e_goals_conceded)                       as e_goals_conceded,
        sum(e_bonus)                                as e_bonus,
        sum(e_cards)                                as e_cards,
        sum(e_points)                               as e_points,

        /*
            Probability of the event in AT LEAST ONE fixture, under
            independence: 1 - prod(1 - p).

            Postgres has no product aggregate, so this is exp(sum(ln(...))).
            The greatest() guard keeps ln() away from zero — a p of exactly 1
            would otherwise produce ln(0) and a null for the whole row.
        */
        1 - exp(sum(ln(greatest(1 - p_minutes_60, 1e-9))))
                                                    as p_minutes_60,
        1 - exp(sum(ln(greatest(1 - (1 - p_minutes_0), 1e-9))))
                                                    as p_did_not_play_any,
        1 - exp(sum(ln(greatest(1 - coalesce(p_clean_sheet, 0), 1e-9))))
                                                    as p_clean_sheet_any,
        1 - exp(sum(ln(greatest(1 - coalesce(p_defcon, 0), 1e-9))))
                                                    as p_defcon_any,

        -- Provenance. One snapshot and one version per gameweek by
        -- construction, so max() is picking the only value there is.
        max(snapshot_id)                            as snapshot_id,
        max(model_version)                          as model_version,
        max(predicted_at)                           as predicted_at,

        max(prior_appearances)                      as prior_appearances,
        bool_or(is_cold_start)                      as is_cold_start,
        min(kickoff_utc)                            as first_kickoff,
        max(kickoff_utc)                            as last_kickoff
    from fixture_predictions
    group by 1, 2, 3
),

-- Opponents as a list, since a double gameweek has two and a consumer
-- displaying a fixture ticker wants both.
opponents as (
    select
        season,
        gameweek,
        player_id,
        string_agg(
            opponent_name || case when is_home then ' (H)' else ' (A)' end,
            ', ' order by kickoff_utc
        )                                           as opponents,
        avg(elo_diff)                               as avg_elo_diff
    from fixture_predictions
    group by 1, 2, 3
)

select
    a.season,
    a.gameweek,
    a.player_id,

    d.player_code,
    d.web_name,
    d.full_name,
    d.position,
    d.team_code,
    d.team_name,
    d.team_short,
    d.price,

    a.fixtures_in_gw,
    a.is_double_gw,
    o.opponents,
    o.avg_elo_diff,
    a.first_kickoff,
    a.last_kickoff,

    -- ---------------------------------------------------------------
    -- MINUTES. p_minutes_60 is the probability of reaching 60 in at least one
    -- fixture; e_minutes is the sum across both.
    -- ---------------------------------------------------------------
    a.p_minutes_60,
    a.e_minutes,

    -- ---------------------------------------------------------------
    -- AWAITING A MODEL. Null throughout until the component exists.
    -- ---------------------------------------------------------------
    a.e_goals,
    a.e_assists,
    a.p_clean_sheet_any                         as p_clean_sheet,
    a.e_saves,
    a.e_goals_conceded,
    a.p_defcon_any                              as p_defcon,
    a.e_bonus,
    a.e_cards,
    a.e_points,

    a.prior_appearances,
    a.is_cold_start,

    a.snapshot_id,
    a.model_version,
    a.predicted_at,

    current_timestamp                           as built_at

from aggregated a

inner join {{ ref('dim_player') }} d
    on  d.season    = a.season
    and d.player_id = a.player_id

left join opponents o
    on  o.season    = a.season
    and o.gameweek  = a.gameweek
    and o.player_id = a.player_id