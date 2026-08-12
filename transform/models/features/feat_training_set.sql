{{ config(materialized='table', schema='features') }}

/*
    feat_training_set — the assembled matrix. One row per player per fixture
    they could have played in, with every feature and every label.

    GRAIN: (snapshot_id, player_id, match_id). A double gameweek gives a player
    two rows; per-gameweek features are identical across both, which is correct,
    and per-gameweek LABELS (gw_points, gw_bonus, gw_bps) land only on the
    primary fixture.

    TWO PATHS, ONE MATRIX. The live spine covers seasons where deadline captures
    exist; the historical spine reconstructs seasons that predate them. They are
    disjoint by construction and unioned here.

    The capture-sourced columns — price, ownership, status, news,
    chance_of_playing_next, ep_next — exist on the live path only. They are cast
    NULL on the historical side rather than omitted, because the matrix needs one
    shape. is_reconstructed is what tells those nulls apart from a genuinely
    absent value: on a reconstructed row the feature was never available, on a
    live row a null means the player had no news.

    Any model training across both must either drop those columns or carry
    is_reconstructed as a feature. Training on them without it teaches the model
    that "no status" predicts 2025/26, which is not a fact about football.

    LABELS ARE RIGHT OF THE DEADLINE, FEATURES LEFT OF IT. Every feature column
    traces back to a capture that predates the deadline, or to a window bounded
    on deadline_utc. Nothing reads stg_player_gameweek_stats except as a label.
    That property is the only one about this model that matters, and the leakage
    tests are what assert it.

    ONE last_appearance_at, FROM THE SPINE. feat_player_load computes its own
    across all competitions; the spine's is league-only, matching
    prior_appearances. The spine's wins and the load version is excluded below,
    because two columns of the same name differing only in which competitions
    they count is the kind of ambiguity that survives review and then produces a
    number nobody can explain. The all-competition information is not lost — it
    is carried in usable form by excess_days_since_appearance and
    minutes_last_club_fixture.

    Opponent strength is joined on the OPPONENT's team_code — an inversion here
    would silently reverse every fixture-difficulty feature while producing no
    error, which is why both joins are spelled out rather than aliased loosely.
*/

with live as (
    select
        snapshot_id,
        season,
        gameweek,
        deadline_utc,
        player_id,
        player_code,
        team_code,
        position,
        match_id,
        kickoff_utc,
        opponent_code,
        is_home,
        elo_diff,
        fixtures_in_gw,

        -- Capture-sourced. Present only on this path.
        price_tenths,
        selected_by_percent,
        status,
        has_news,
        chance_of_playing_next,
        ep_next,

        -- Labels.
        minutes,
        did_appear,
        played_60,
        goals,
        assists,
        xg,
        xa,
        goals_conceded_on_pitch,
        saves,
        defensive_actions,
        gw_points,
        gw_bonus,
        gw_bps,
        gw_clean_sheets
    from {{ ref('feat_player_fixture_spine') }}
),

historical as (
    select
        snapshot_id,
        season,
        gameweek,
        deadline_utc,
        player_id,
        player_code,
        team_code,
        position,
        match_id,
        kickoff_utc,
        opponent_code,
        is_home,
        elo_diff,
        fixtures_in_gw,

        -- No captures for these seasons. Cast rather than omitted so the union
        -- has one shape; is_reconstructed below is what makes the nulls
        -- interpretable.
        null::int                                   as price_tenths,
        null::numeric                               as selected_by_percent,
        null::text                                  as status,
        null::boolean                               as has_news,
        null::int                                   as chance_of_playing_next,
        null::numeric                               as ep_next,

        minutes,
        did_appear,
        played_60,
        goals,
        assists,
        xg,
        xa,
        goals_conceded_on_pitch,
        saves,
        defensive_actions,
        gw_points,
        gw_bonus,
        gw_bps,
        gw_clean_sheets
    from {{ ref('feat_player_fixture_spine_historical') }}
),

spine as (
    select * from live
    union all
    select * from historical
)

select
    -- identity and grain
    sp.snapshot_id,
    sp.season,
    sp.gameweek,
    sp.deadline_utc,
    sp.player_id,
    sp.player_code,
    sp.match_id,
    sp.kickoff_utc,
    sp.team_code,
    sp.opponent_code,
    sp.position,
    sp.is_home,
    sp.fixtures_in_gw,

    -- Which path this row came from. Not decoration: it is the only thing
    -- distinguishing a structurally absent capture feature from a null one.
    gs.is_reconstructed,

    -- Prior involvement, from the unified gameweek spine so both paths share
    -- one definition. On the reconstructed path this also absorbs the roster
    -- inflation — the season squad includes players who had not yet signed,
    -- and prior_appearances is what lets the model discount them.
    gs.prior_appearances,
    gs.had_appeared_before,
    gs.prior_minutes,
    gs.last_appearance_at,
    gs.days_since_last_appearance,

    -- deadline-known player state. NULL throughout on reconstructed rows.
    sp.price_tenths,
    sp.selected_by_percent,
    sp.status,
    sp.has_news,
    sp.chance_of_playing_next,
    sp.ep_next,

    {{ dbt_utils.star(
        from=ref('feat_player_form'),
        relation_alias='fo',
        except=['snapshot_id', 'season', 'gameweek', 'deadline_utc',
                'player_id', 'player_code', 'team_code', 'position',
                'last_appearance_at', 'days_since_last_appearance', 'built_at']
    ) }},

    {{ dbt_utils.star(
        from=ref('feat_player_load'),
        relation_alias='lo',
        except=['snapshot_id', 'season', 'gameweek', 'deadline_utc',
                'player_id', 'player_code', 'team_code', 'position',
                'last_appearance_at', 'days_since_last_appearance',
                'club_last_fixture_at', 'built_at']
    ) }},

    -- own team strength
    ts.elo_current                              as team_elo,
    ts.xg_for_per_match_5                       as team_xg_for_5,
    ts.xg_against_per_match_5                   as team_xg_against_5,
    ts.xg_for_per_match_10                      as team_xg_for_10,
    ts.xg_against_per_match_10                  as team_xg_against_10,
    ts.clean_sheet_rate_10                      as team_clean_sheet_rate_10,

    -- opponent strength. Joined on opponent_code — the inversion here would
    -- not error.
    op.elo_current                              as opponent_elo,
    op.xg_for_per_match_5                       as opponent_xg_for_5,
    op.xg_against_per_match_5                   as opponent_xg_against_5,
    op.xg_for_per_match_10                      as opponent_xg_for_10,
    op.xg_against_per_match_10                  as opponent_xg_against_10,
    op.clean_sheet_rate_10                      as opponent_clean_sheet_rate_10,

    sp.elo_diff,
    case when sp.is_home
         then op.strength_defence_away
         else op.strength_defence_home
    end                                         as opponent_defence_strength,
    case when sp.is_home
         then op.strength_attack_away
         else op.strength_attack_home
    end                                         as opponent_attack_strength,

    -- ================================================================
    -- LABELS. Everything below is an outcome, known only after kickoff.
    -- ================================================================
    sp.did_appear,
    sp.minutes,
    sp.played_60,
    case
        when sp.minutes = 0  then 0
        when sp.minutes < 60 then 1
        else 2
    end                                         as minutes_band,
    sp.goals,
    sp.assists,
    sp.xg,
    sp.xa,
    sp.saves,
    sp.goals_conceded_on_pitch,
    sp.defensive_actions,
    sp.gw_clean_sheets,

    -- Gameweek-level labels. Present only on the primary fixture; NULL on the
    -- second fixture of a double because the source cannot allocate them.
    sp.gw_points,
    sp.gw_bonus,
    sp.gw_bps,

    current_timestamp                           as built_at

from spine sp

inner join {{ ref('feat_player_gameweek_spine') }} gs
    on  gs.snapshot_id = sp.snapshot_id
    and gs.player_id   = sp.player_id

left join {{ ref('feat_player_form') }} fo
    on  fo.snapshot_id = sp.snapshot_id
    and fo.player_id   = sp.player_id

left join {{ ref('feat_player_load') }} lo
    on  lo.snapshot_id = sp.snapshot_id
    and lo.player_id   = sp.player_id

left join {{ ref('feat_team_strength') }} ts
    on  ts.snapshot_id = sp.snapshot_id
    and ts.team_code   = sp.team_code

left join {{ ref('feat_team_strength') }} op
    on  op.snapshot_id = sp.snapshot_id
    and op.team_code   = sp.opponent_code