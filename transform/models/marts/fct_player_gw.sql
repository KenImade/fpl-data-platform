{{ config(materialized='table') }}

/*
    fct_player_gw — one row per player per gameweek per fixture.

    THE GRAIN IS PER FIXTURE, NOT PER GAMEWEEK. This is the constraint
    established in ADR 0005 and confirmed twice: per-match scoring rules
    (the 60-minute appearance threshold, clean sheets, defensive
    contribution) cannot be evaluated on a gameweek aggregate. GW26 2025/26
    shows 180-minute rows in the gameweek-level source, and reconciling
    against it produced 22 divergences that vanish at this grain.

    TWO SOURCES, TWO GRAINS
    -----------------------
    stg_playermatchstats has the right grain but is missing four scoring
    inputs: cards, own goals, penalties saved, and bonus. Those exist only
    in stg_player_gameweek_stats, which aggregates double gameweeks.

    For a single-fixture gameweek the two join cleanly and everything is
    correct. For a double gameweek the gameweek-level fields cannot be
    allocated to individual fixtures — nothing in either source records
    which of the two matches a yellow card came from.

    RESOLUTION: gameweek-level columns are populated on the FIRST fixture of
    a gameweek (by kickoff) and NULL on subsequent ones, with
    `is_gw_primary` marking which. That makes a naive

        select sum(bonus) from fct_player_gw

    correct. Repeating the value across both rows would double-count, which
    is the failure mode most likely to go unnoticed.

    The trade-off: for a double gameweek you cannot say which fixture the
    bonus came from. That information does not exist in the source, so no
    modelling choice here can recover it.
*/

with matches as (
    select * from {{ ref('stg_matches') }}
),

pms as (
    select * from {{ ref('stg_playermatchstats') }}
),

gws as (
    select * from {{ ref('stg_player_gameweek_stats') }}
),

players as (
    select * from {{ ref('stg_players') }}
),

-- Order a player's fixtures within a gameweek so the gameweek-level values
-- attach to exactly one of them.
sequenced as (
    select
        p.*,
        -- Partition over LEAGUE fixtures only. FPL gameweek points arise
        -- solely from Premier League matches, so the gw_* columns must
        -- attach to a league fixture — otherwise a player with a midweek
        -- European tie has them attached to a match that disappears the
        -- moment anyone filters to is_league.
        case when p.is_league then row_number() over (
            partition by p.season, p.player_id, p.gameweek, p.is_league
            order by p.kickoff_utc nulls last, p.match_id
        ) end                                               as fixture_seq,
        count(*) filter (where p.is_league) over (
            partition by p.season, p.player_id, p.gameweek
        )                                                   as league_fixtures_in_gw
    from pms p
),

joined as (
    select
        -- identity
        s.season,
        s.gameweek,
        s.match_id,
        s.player_id,
        pl.player_code,
        pl.team_code,
        pl.web_name,
        pl.position,

        -- fixture context
        s.competition,
        s.is_league,
        s.kickoff_utc,
        s.fixture_seq,
        -- League fixtures only. A player with a midweek European tie is not
        -- in a double gameweek for FPL purposes, and counting those would
        -- make is_double_gw mean "played twice in any competition" — which
        -- is a congestion signal, not a scoring one.
        s.league_fixtures_in_gw                              as fixtures_in_gw,
        s.league_fixtures_in_gw > 1                          as is_double_gw,
        coalesce(s.fixture_seq = 1, false)                   as is_gw_primary,

        -- ============================================================
        -- PER-FIXTURE: true at this grain, safe to aggregate freely
        -- ============================================================
        s.minutes,
        s.start_min,
        s.finish_min,
        s.goals,
        s.assists,
        s.xg,
        s.xa,
        s.xgot,
        s.shots,
        s.shots_on_target,
        s.big_chances_missed,
        s.touches_opp_box,
        s.chances_created,

        s.goals_conceded_on_pitch,
        s.saves,
        s.saves_inside_box,
        s.goals_prevented,
        s.xgot_faced,

        s.tackles,
        s.interceptions,
        s.blocks,
        s.clearances,
        s.headed_clearances,
        s.recoveries,

        s.penalties_scored,
        s.penalties_missed                                  as penalties_missed_match,

        -- Defensive contribution, derived per position rather than taken
        -- from the published aggregate. That aggregate bundles recoveries
        -- for defenders, who are not credited for them — verified across
        -- 3,519 defender-gameweeks, every divergent row matching
        -- tackles + cbi + recoveries exactly. See ADR 0005.
        case pl.position
            when 'DEF' then s.tackles + s.interceptions + s.blocks + s.clearances
            when 'MID' then s.tackles + s.interceptions + s.blocks + s.clearances + s.recoveries
            when 'FWD' then s.tackles + s.interceptions + s.blocks + s.clearances + s.recoveries
        end                                                 as defensive_actions,

        -- ============================================================
        -- PER-GAMEWEEK: attached to the first fixture only. NULL elsewhere,
        -- because these cannot be allocated across a double gameweek and
        -- repeating them would double-count.
        -- ============================================================
        case when s.fixture_seq = 1 then g.points        end as gw_points,
        case when s.fixture_seq = 1 then g.bonus         end as gw_bonus,
        case when s.fixture_seq = 1 then g.bps           end as gw_bps,
        case when s.fixture_seq = 1 then g.yellow_cards  end as gw_yellow_cards,
        case when s.fixture_seq = 1 then g.red_cards     end as gw_red_cards,
        case when s.fixture_seq = 1 then g.own_goals     end as gw_own_goals,
        case when s.fixture_seq = 1 then g.penalties_saved end as gw_penalties_saved,
        case when s.fixture_seq = 1 then g.clean_sheets  end as gw_clean_sheets,
        case when s.fixture_seq = 1 then g.minutes       end as gw_minutes,

        -- market state, gameweek-level by nature
        case when s.fixture_seq = 1 then g.price_tenths       end as gw_price_tenths,
        case when s.fixture_seq = 1 then g.selected_by_percent end as gw_selected_by_percent,

        current_timestamp                                   as built_at

    from sequenced s

    inner join players pl
        on  s.player_id = pl.player_id
        and s.season    = pl.season

    -- Left, not inner: a player can appear in a European or cup fixture and
    -- have no FPL gameweek row at all. Losing those would break exactly the
    -- congestion and fatigue features this grain exists to support.
    left join gws g
        on  s.player_id = g.player_id
        and s.season    = g.season
        and s.gameweek  = g.gameweek
)

select * from joined