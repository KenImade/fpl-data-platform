{{ config(materialized='table', schema='features') }}

/*
    feat_player_form — a player's recent league output, as at each deadline.

    GRAIN: (snapshot_id, player_id). One row per player per gameweek, NOT per
    fixture. A double gameweek fans this out across both fixtures when joined
    to the spine, which is correct: a manager knows the same thing about a
    player before both matches. Computing it per fixture would let the second
    fixture see the first, which is post-deadline information.

    THE BOUNDARY IS THE DEADLINE, NOT THE KICKOFF. Every window here bounds on
    kickoff_utc < deadline_utc of the target gameweek. Bounding on the target
    fixture's kickoff instead would look correct and leak: a Thursday European
    tie falls after the deadline but before Sunday's league match, so a
    kickoff-bounded window would include a result the manager could not have
    known. That is the single most likely leak in this layer and the reason
    this model does not use a `rows between N preceding` frame.

    LEAGUE ONLY. Champions League xG against a weaker opponent is not
    comparable to Premier League xG, so mixing them distorts per-90 rates.
    All-competition minutes are fatigue, not form, and live in
    feat_player_load.

    RATES ARE PER 90 AND SHRUNK. A player with 45 minutes and one goal has a
    2.0 goals-per-90 that means nothing. Rates are shrunk toward the positional
    mean with a prior weight in minutes, so small samples pull to the prior
    and large ones barely move. See PRIOR_MINUTES below.

    NULL, NOT ZERO, for a player with no prior league appearances. A promoted
    club's signing at GW1 has no history and that is different from having a
    history of zeroes. The model handles the null; SQL should not invent a
    value for it.
*/

{% set windows = [3, 5, 10] %}
{% set prior_minutes = 270 %}

with deadlines as (
    select distinct snapshot_id, season, gameweek, deadline_utc
    from {{ ref('feat_player_gameweek_spine') }}
),

-- Every league appearance, at fixture grain. Appearances only: a player who
-- was not in the squad has no row in fct_player_gw, and a non-appearance is
-- not a data point about form. It is a data point about availability, which
-- feat_player_load and the spine's did_appear label cover.
appearances as (
    select
        season,
        player_id,
        player_code,
        match_id,
        kickoff_utc,
        minutes,
        goals,
        assists,
        xg,
        xa,
        coalesce(xg, 0) + coalesce(xa, 0)               as xgi,
        shots,
        shots_on_target,
        touches_opp_box,
        chances_created,
        big_chances_missed,
        saves,
        goals_prevented,
        goals_conceded_on_pitch,
        defensive_actions,
        position
    from {{ ref('fct_player_gw') }}
    where is_league
      and minutes > 0
      and kickoff_utc is not null
),

/*
    Cross deadlines with prior appearances, then rank backwards from the
    deadline. The join predicate IS the point-in-time guard: a fixture is only
    a candidate if it kicked off strictly before the deadline.

    This is a range join and will be the expensive model in the DAG. It stays
    manageable because a season is 38 deadlines and a player has at most 38
    league appearances, so the cross product is bounded per player-season.
*/
ranked as (
    select
        d.snapshot_id,
        d.season,
        d.gameweek,
        d.deadline_utc,
        a.player_id,
        a.player_code,
        a.position,
        a.kickoff_utc,
        a.minutes,
        a.goals,
        a.assists,
        a.xg,
        a.xa,
        a.xgi,
        a.shots,
        a.shots_on_target,
        a.touches_opp_box,
        a.chances_created,
        a.big_chances_missed,
        a.saves,
        a.goals_prevented,
        a.goals_conceded_on_pitch,
        a.defensive_actions,
        row_number() over (
            partition by d.snapshot_id, a.player_id
            order by a.kickoff_utc desc
        )                                               as recency
    from deadlines d
    inner join appearances a
        on  a.season      = d.season
        and a.kickoff_utc < d.deadline_utc
),

/*
    One aggregate per window. Rates are computed on summed totals rather than
    averaged per-fixture rates, so a 20-minute cameo does not carry the same
    weight as a full match.
*/
windowed as (
    select
        snapshot_id,
        player_id,

        {% for n in windows %}
        count(*)      filter (where recency <= {{ n }})  as appearances_{{ n }},
        sum(minutes)  filter (where recency <= {{ n }})  as minutes_{{ n }},
        sum(goals)    filter (where recency <= {{ n }})  as goals_{{ n }},
        sum(assists)  filter (where recency <= {{ n }})  as assists_{{ n }},
        sum(xg)       filter (where recency <= {{ n }})  as xg_{{ n }},
        sum(xa)       filter (where recency <= {{ n }})  as xa_{{ n }},
        sum(xgi)      filter (where recency <= {{ n }})  as xgi_{{ n }},
        sum(shots)    filter (where recency <= {{ n }})  as shots_{{ n }},
        sum(shots_on_target)
                      filter (where recency <= {{ n }})  as sot_{{ n }},
        sum(touches_opp_box)
                      filter (where recency <= {{ n }})  as box_touches_{{ n }},
        sum(chances_created)
                      filter (where recency <= {{ n }})  as chances_created_{{ n }},
        sum(big_chances_missed)
                      filter (where recency <= {{ n }})  as big_chances_missed_{{ n }},
        sum(saves)    filter (where recency <= {{ n }})  as saves_{{ n }},
        sum(goals_prevented)
                      filter (where recency <= {{ n }})  as goals_prevented_{{ n }},
        sum(goals_conceded_on_pitch)
                      filter (where recency <= {{ n }})  as conceded_{{ n }},
        sum(defensive_actions)
                      filter (where recency <= {{ n }})  as defensive_actions_{{ n }},
        count(*) filter (where recency <= {{ n }} and minutes >= 60)
                                                         as starts_{{ n }},
        {% endfor %}

        -- Career-to-date within the season, for the shrinkage denominator and
        -- as a stable long-run rate.
        count(*)                                        as appearances_season,
        sum(minutes)                                    as minutes_season,
        sum(xgi)                                        as xgi_season,

        -- Recency of the last appearance. A ten-fixture window can reach back
        -- two months for a returning player; this is what lets the model
        -- discount a stale rate rather than SQL deciding for it.
        max(kickoff_utc)                                as last_appearance_at
    from ranked
    group by 1, 2
),

/*
    Positional priors, computed at the same deadline so they carry no
    hindsight either. The prior is the league-wide per-90 rate for the
    position over all fixtures preceding this deadline.
*/
priors as (
    select
        r.snapshot_id,
        r.position,
        sum(r.xg)  / nullif(sum(r.minutes), 0) * 90      as prior_xg_per_90,
        sum(r.xa)  / nullif(sum(r.minutes), 0) * 90      as prior_xa_per_90,
        sum(r.xgi) / nullif(sum(r.minutes), 0) * 90      as prior_xgi_per_90,
        sum(r.defensive_actions)
                   / nullif(sum(r.minutes), 0) * 90      as prior_defcon_per_90
    from ranked r
    where r.position is not null
    group by 1, 2
)

select
    d.snapshot_id,
    d.season,
    d.gameweek,
    d.deadline_utc,

    s.player_id,
    s.player_code,
    s.team_code,
    s.position,

    -- Raw window aggregates. NULL where the player has no appearances in the
    -- window, which is distinct from a window of zeroes.
    {% for n in windows %}
    w.appearances_{{ n }},
    w.minutes_{{ n }},
    w.starts_{{ n }},
    w.goals_{{ n }},
    w.assists_{{ n }},
    w.xg_{{ n }},
    w.xa_{{ n }},
    w.xgi_{{ n }},
    w.shots_{{ n }},
    w.sot_{{ n }},
    w.box_touches_{{ n }},
    w.chances_created_{{ n }},
    w.big_chances_missed_{{ n }},
    w.saves_{{ n }},
    w.goals_prevented_{{ n }},
    w.conceded_{{ n }},
    w.defensive_actions_{{ n }},

    -- Unshrunk per-90 rates. Kept alongside the shrunk versions because for
    -- a player with a full window they are the more faithful figure, and the
    -- difference between the two is itself informative.
    w.xg_{{ n }}  / nullif(w.minutes_{{ n }}, 0) * 90    as xg_per_90_{{ n }},
    w.xa_{{ n }}  / nullif(w.minutes_{{ n }}, 0) * 90    as xa_per_90_{{ n }},
    w.xgi_{{ n }} / nullif(w.minutes_{{ n }}, 0) * 90    as xgi_per_90_{{ n }},
    w.shots_{{ n }}
                  / nullif(w.minutes_{{ n }}, 0) * 90    as shots_per_90_{{ n }},
    w.box_touches_{{ n }}
                  / nullif(w.minutes_{{ n }}, 0) * 90    as box_touches_per_90_{{ n }},
    w.defensive_actions_{{ n }}
                  / nullif(w.minutes_{{ n }}, 0) * 90    as defcon_per_90_{{ n }},

    /*
        Shrunk rates. The estimate is a minutes-weighted blend of the player's
        own rate and the positional prior:

            (own_total + prior_rate * PRIOR_MINUTES / 90)
            / (own_minutes + PRIOR_MINUTES) * 90

        PRIOR_MINUTES = {{ prior_minutes }}, i.e. three full matches. A player
        with 270 minutes sits halfway between their own rate and the prior; at
        900 minutes the prior contributes under a quarter. This is what stops
        a 20-minute hat-trick reading as a 13.5 goals-per-90 striker.
    */
    case when w.minutes_{{ n }} > 0 then
        (w.xg_{{ n }} + p.prior_xg_per_90 * {{ prior_minutes }} / 90.0)
        / (w.minutes_{{ n }} + {{ prior_minutes }}) * 90
    end                                                  as xg_per_90_shrunk_{{ n }},

    case when w.minutes_{{ n }} > 0 then
        (w.xa_{{ n }} + p.prior_xa_per_90 * {{ prior_minutes }} / 90.0)
        / (w.minutes_{{ n }} + {{ prior_minutes }}) * 90
    end                                                  as xa_per_90_shrunk_{{ n }},

    case when w.minutes_{{ n }} > 0 then
        (w.xgi_{{ n }} + p.prior_xgi_per_90 * {{ prior_minutes }} / 90.0)
        / (w.minutes_{{ n }} + {{ prior_minutes }}) * 90
    end                                                  as xgi_per_90_shrunk_{{ n }},

    case when w.minutes_{{ n }} > 0 then
        (w.defensive_actions_{{ n }} + p.prior_defcon_per_90 * {{ prior_minutes }} / 90.0)
        / (w.minutes_{{ n }} + {{ prior_minutes }}) * 90
    end                                                  as defcon_per_90_shrunk_{{ n }},

    -- Overperformance. Positive means finishing above the underlying numbers,
    -- which regresses — so it is a signal about the PAST rate being
    -- unsustainable, not about future scoring.
    w.goals_{{ n }} - w.xg_{{ n }}                       as goals_minus_xg_{{ n }},
    {% endfor %}

    -- Season-to-date, league only.
    w.appearances_season,
    w.minutes_season,
    w.xgi_season / nullif(w.minutes_season, 0) * 90      as xgi_per_90_season,

    -- Staleness. NULL for a player with no prior league appearance this
    -- season — a new signing, a promoted club's squad player, or GW1.
    w.last_appearance_at,
    extract(epoch from (d.deadline_utc - w.last_appearance_at)) / 86400
                                                         as days_since_last_appearance,

    -- No league history at all as at this deadline. An explicit flag rather
    -- than leaving the model to infer it from a wall of nulls.
    w.player_id is null                                  as is_cold_start,

    -- The positional priors themselves, so a cold-start row still has a
    -- usable rate to fall back on.
    p.prior_xg_per_90,
    p.prior_xa_per_90,
    p.prior_xgi_per_90,
    p.prior_defcon_per_90,

    current_timestamp                                    as built_at

from deadlines d

-- The snapshot is the roster: who existed, at which club, in which position,
-- as at this deadline. Driving from here rather than from appearances means
-- a player with no history still gets a row.
inner join {{ ref('feat_player_gameweek_spine') }} s
    on s.snapshot_id = d.snapshot_id

left join windowed w
    on  w.snapshot_id = d.snapshot_id
    and w.player_id   = s.player_id

left join priors p
    on  p.snapshot_id = d.snapshot_id
    and p.position    = s.position