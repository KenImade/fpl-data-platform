{{ config(materialized='table', schema='features') }}

/*
    feat_team_strength — attacking and defensive strength, as at each deadline.

    GRAIN: (snapshot_id, team_code).

    Elo is the backbone. fct_team_fixture carries the rating AT KICKOFF, so it
    is point-in-time by construction and needs no windowing — it is the only
    feature here that is free of the leakage question entirely. It also solves
    the promoted-club problem: a newly promoted side has no Premier League
    history but does have an Elo, so it starts with a defensible prior rather
    than a null.

    Rolling goal and xG rates are LEAGUE ONLY, for the same reason
    feat_player_form is: a Champions League goal against a weaker opponent is
    not evidence of Premier League attacking strength.

    xG for and against are summed from player rows, since fct_team_fixture
    carries goals but not xG. A team's xG is the sum of its players', which
    holds because fct_player_gw is at fixture grain.
*/

{% set windows = [5, 10] %}

with deadlines as (
    select distinct snapshot_id, season, gameweek, deadline_utc
    from {{ ref('feat_player_gameweek_spine') }}
),

-- Team xG per match, summed from players. Attributed to the club the player
-- is recorded under, which inherits fct_player_gw's end-of-season club
-- assignment — a known limitation for mid-season transfers, immaterial at
-- team aggregate level since the player's xG lands at one of the two clubs
-- involved in that fixture in the overwhelming majority of cases.
team_xg as (
    select
        season,
        match_id,
        team_code,
        sum(xg)                                     as xg_for
    from {{ ref('fct_player_gw') }}
    where is_league
    group by 1, 2, 3
),

fixtures as (
    select
        tf.season,
        tf.match_id,
        tf.team_code,
        tf.kickoff_utc,
        tf.is_home,
        tf.goals_for,
        tf.goals_against,
        tf.elo,
        tf.opponent_elo,
        tf.elo_diff,
        tf.result,
        x.xg_for,
        xa.xg_for                                   as xg_against
    from {{ ref('fct_team_fixture') }} tf
    left join team_xg x
        on  x.match_id  = tf.match_id
        and x.team_code = tf.team_code
    -- The opponent's xG in the same fixture is this team's xG conceded.
    left join team_xg xa
        on  xa.match_id  = tf.match_id
        and xa.team_code = tf.opponent_code
    where tf.is_league
      and tf.result is not null
      and tf.kickoff_utc is not null
),

ranked as (
    select
        d.snapshot_id,
        d.deadline_utc,
        f.*,
        row_number() over (
            partition by d.snapshot_id, f.team_code
            order by f.kickoff_utc desc
        )                                           as recency
    from deadlines d
    inner join fixtures f
        on  f.season      = d.season
        and f.kickoff_utc < d.deadline_utc
),

windowed as (
    select
        snapshot_id,
        team_code,

        {% for n in windows %}
        count(*)          filter (where recency <= {{ n }}) as matches_{{ n }},
        sum(goals_for)    filter (where recency <= {{ n }}) as goals_for_{{ n }},
        sum(goals_against) filter (where recency <= {{ n }}) as goals_against_{{ n }},
        sum(xg_for)       filter (where recency <= {{ n }}) as xg_for_{{ n }},
        sum(xg_against)   filter (where recency <= {{ n }}) as xg_against_{{ n }},
        count(*) filter (where recency <= {{ n }} and goals_against = 0)
                                                            as clean_sheets_{{ n }},
        {% endfor %}

        -- Home and away splits, season to date. Home advantage is large and
        -- team-specific enough to be worth carrying separately.
        avg(xg_for) filter (where is_home)          as xg_for_home,
        avg(xg_for) filter (where not is_home)      as xg_for_away,
        avg(xg_against) filter (where is_home)      as xg_against_home,
        avg(xg_against) filter (where not is_home)  as xg_against_away,

        count(*)                                    as matches_season,
        -- Elo at the most recent fixture before the deadline: current
        -- strength, with no hindsight.
        max(elo) filter (where recency = 1)         as elo_current
    from ranked
    group by 1, 2
)

select
    d.snapshot_id,
    d.season,
    d.gameweek,
    d.deadline_utc,

    t.team_code,
    t.team_name,

    -- Elo. Available for every club including the newly promoted, which is
    -- what makes it the right prior at GW1.
    w.elo_current,

    {% for n in windows %}
    w.matches_{{ n }},
    w.goals_for_{{ n }},
    w.goals_against_{{ n }},
    w.xg_for_{{ n }},
    w.xg_against_{{ n }},
    w.clean_sheets_{{ n }},
    w.xg_for_{{ n }}     / nullif(w.matches_{{ n }}, 0) as xg_for_per_match_{{ n }},
    w.xg_against_{{ n }} / nullif(w.matches_{{ n }}, 0) as xg_against_per_match_{{ n }},
    w.clean_sheets_{{ n }}::numeric
                         / nullif(w.matches_{{ n }}, 0) as clean_sheet_rate_{{ n }},
    {% endfor %}

    w.xg_for_home,
    w.xg_for_away,
    w.xg_against_home,
    w.xg_against_away,
    w.matches_season,

    -- FPL's own strength ratings. Crude next to Elo, but they are what FPL's
    -- published fixture difficulty is built from, so worth carrying when
    -- comparing model output against ep_next.
    t.strength,
    t.strength_attack_home,
    t.strength_attack_away,
    t.strength_defence_home,
    t.strength_defence_away,

    w.matches_season is null                        as is_cold_start,

    current_timestamp                               as built_at

from deadlines d

-- dim_team is one row per club per season, so joining on season alone yields
-- every club at every deadline in that season. Inner join rather than a cross
-- join with a trailing filter: same result, but the condition sits where a
-- reader expects to find it.
inner join {{ ref('dim_team') }} t
    on t.season = d.season

left join windowed w
    on  w.snapshot_id = d.snapshot_id
    and w.team_code   = t.team_code