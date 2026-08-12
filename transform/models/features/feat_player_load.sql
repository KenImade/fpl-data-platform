{{ config(materialized='table', schema='features') }}

/*
    feat_player_load — selection share and fatigue, as at each deadline.

    GRAIN: (snapshot_id, player_id). Same as feat_player_form.

    TWO BLOCKS, TWO DIFFERENT QUESTIONS.

    The SELECTION block asks "is this player being picked", and indexes windows
    by the CLUB'S FIXTURES rather than by days. A player who started the last
    three matches their club played is a starter whether that took ten days or
    thirty. Share of available minutes is the quantity, and it is invariant to
    gaps in the calendar by construction.

    The FATIGUE block asks "is this player tired", and indexes by DAYS, because
    fatigue genuinely does decay with elapsed time — three matches in seven days
    is a different physical state from three in twenty-one. Day windows are kept
    for exactly this and nothing else.

    WHY THE SPLIT EXISTS. The previous version used day windows for both, and it
    failed hard at GW32 2025/26: the deadline fell three weeks after GW31 across
    the March international break, so minutes_14d was 0.00 for EVERY player in
    the gameweek and minutes_21d fell to a quarter of normal. The model's top
    features all read "nobody has played in a month", and it predicted that
    nobody would start — p_60 peaked at 0.36 across 841 rows where the true
    band-2 rate was 0.25.

    That inference was correct given what it saw. The features were wrong. A
    zero in a day-indexed window conflates "not selected" with "the league
    paused", and there are three international breaks a season, so the failure
    recurs by design rather than by accident.

    NULL, NOT ZERO, WHERE THE DENOMINATOR IS EMPTY. Every ratio here is null
    when its club played no qualifying fixtures in the window. That is the whole
    point: an unknown share is not a share of zero, and LightGBM splits on
    missingness natively.

    ALL COMPETITIONS in both blocks, and friendlies excluded from both. A
    midweek European tie is the load that causes Sunday rotation, which is why
    fct_player_gw was built at fixture grain across every competition. Friendlies
    would make a pre-season squad look critically fatigued.
*/

{% set club_windows = [3, 5, 10] %}
{% set day_windows = [7, 14, 21, 28] %}

with deadlines as (
    select distinct snapshot_id, season, gameweek, deadline_utc
    from {{ ref('feat_player_gameweek_spine') }}
),

roster as (
    select snapshot_id, season, gameweek, deadline_utc,
           player_id, player_code, team_code, position
    from {{ ref('feat_player_gameweek_spine') }}
),

-- Competitive fixtures only, at team grain. This is the denominator: what the
-- club actually played, whether or not any given player featured in it.
club_fixtures as (
    select
        season,
        team_code,
        match_id,
        kickoff_utc,
        is_league,
        competition
    from {{ ref('fct_team_fixture') }}
    where kickoff_utc is not null
      and competition not in ('friendly')
),

/*
    Rank each club's fixtures backwards from each deadline. recency = 1 is the
    most recent fixture the club played before the deadline, whenever that was.

    This is what makes the selection block break-proof: an international break
    changes WHEN recency 1..3 happened, not WHICH fixtures they were.
*/
club_ranked as (
    select
        d.snapshot_id,
        d.deadline_utc,
        c.team_code,
        c.match_id,
        c.kickoff_utc,
        c.is_league,
        row_number() over (
            partition by d.snapshot_id, c.team_code
            order by c.kickoff_utc desc
        )                                           as recency,
        d.deadline_utc - c.kickoff_utc              as elapsed
    from deadlines d
    inner join club_fixtures c
        on  c.season      = d.season
        and c.kickoff_utc < d.deadline_utc
),

-- Player appearances, joined onto the club's fixture ranking. A left join from
-- the club side would be wrong here — this is the numerator and it is supposed
-- to be sparse.
player_appearances as (
    select
        season,
        player_id,
        match_id,
        minutes
    from {{ ref('fct_player_gw') }}
    where kickoff_utc is not null
      and competition not in ('friendly')
),

/*
    SELECTION BLOCK. Indexed by club fixture, so the denominator is always the
    number of matches the club actually played.
*/
selection as (
    select
        r.snapshot_id,
        r.player_id,

        {% for n in club_windows %}
        -- Denominator: club fixtures in the window. Usually {{ n }}, but fewer
        -- early in a season, which is why it is carried rather than assumed.
        count(*) filter (where cr.recency <= {{ n }})
                                                    as club_fixtures_{{ n }},

        sum(coalesce(pa.minutes, 0)) filter (where cr.recency <= {{ n }})
                                                    as minutes_in_{{ n }},

        -- Share of available minutes. NULL where the club played nothing,
        -- which is the case day-indexed windows got wrong.
        sum(coalesce(pa.minutes, 0)) filter (where cr.recency <= {{ n }})::numeric
            / nullif(count(*) filter (where cr.recency <= {{ n }}) * 90.0, 0)
                                                    as minutes_share_{{ n }},

        -- Appearances and starts as a share, same denominator.
        count(*) filter (where cr.recency <= {{ n }} and pa.minutes > 0)
                                                    as appearances_in_{{ n }},
        count(*) filter (where cr.recency <= {{ n }} and pa.minutes >= 60)
                                                    as starts_in_{{ n }},
        count(*) filter (where cr.recency <= {{ n }} and pa.minutes >= 60)::numeric
            / nullif(count(*) filter (where cr.recency <= {{ n }}), 0)
                                                    as start_rate_{{ n }},
        {% endfor %}

        -- The most recent club fixture, and what the player did in it. The
        -- single strongest selection signal: did they play last time out.
        max(cr.kickoff_utc) filter (where cr.recency = 1)
                                                    as club_last_fixture_at,
        max(coalesce(pa.minutes, 0)) filter (where cr.recency = 1)
                                                    as minutes_last_club_fixture,
        bool_or(cr.recency = 1 and coalesce(pa.minutes, 0) >= 60)
                                                    as started_last_club_fixture,
        bool_or(cr.recency = 1 and not cr.is_league)
                                                    as last_club_fixture_non_league
    from roster r
    inner join club_ranked cr
        on  cr.snapshot_id = r.snapshot_id
        and cr.team_code   = r.team_code
    left join player_appearances pa
        on  pa.match_id  = cr.match_id
        and pa.player_id = r.player_id
        and pa.season    = r.season
    group by 1, 2
),

/*
    FATIGUE BLOCK. Indexed by days, because this is the one thing that genuinely
    decays with elapsed time.

    club_fixtures_{n}d is carried alongside every minutes figure so a zero is
    interpretable. Without it, "played 0 minutes in 14 days" is ambiguous
    between rested and unavailable — the ambiguity that broke GW32.
*/
fatigue as (
    select
        r.snapshot_id,
        r.player_id,

        {% for n in day_windows %}
        -- Denominator first. A player cannot have accumulated load in a window
        -- where their club played nothing.
        count(*) filter (where cr.elapsed < interval '{{ n }} days')
                                                    as club_fixtures_{{ n }}d,

        sum(coalesce(pa.minutes, 0)) filter (
            where cr.elapsed < interval '{{ n }} days'
        )                                           as minutes_{{ n }}d,

        count(*) filter (
            where cr.elapsed < interval '{{ n }} days'
              and coalesce(pa.minutes, 0) > 0
        )                                           as fixtures_played_{{ n }}d,

        sum(coalesce(pa.minutes, 0)) filter (
            where cr.elapsed < interval '{{ n }} days' and not cr.is_league
        )                                           as non_league_minutes_{{ n }}d,

        count(*) filter (
            where cr.elapsed < interval '{{ n }} days'
              and not cr.is_league
              and coalesce(pa.minutes, 0) > 0
        )                                           as non_league_fixtures_{{ n }}d,

        -- Minutes per available club fixture. NULL where the club played none,
        -- so an international break reads as unknown rather than as zero load.
        sum(coalesce(pa.minutes, 0)) filter (
            where cr.elapsed < interval '{{ n }} days'
        )::numeric
            / nullif(
                count(*) filter (where cr.elapsed < interval '{{ n }} days') * 90.0,
                0
            )                                       as minutes_share_{{ n }}d,
        {% endfor %}

        max(cr.kickoff_utc) filter (where coalesce(pa.minutes, 0) > 0)
                                                    as last_appearance_at
    from roster r
    inner join club_ranked cr
        on  cr.snapshot_id = r.snapshot_id
        and cr.team_code   = r.team_code
       and cr.elapsed     < interval '28 days'
    left join player_appearances pa
        on  pa.match_id  = cr.match_id
        and pa.player_id = r.player_id
        and pa.season    = r.season
    group by 1, 2
)

select
    r.snapshot_id,
    r.season,
    r.gameweek,
    r.deadline_utc,

    r.player_id,
    r.player_code,
    r.team_code,
    r.position,

    -- ================================================================
    -- SELECTION. Club-fixture indexed, invariant to calendar gaps.
    -- ================================================================
    {% for n in club_windows %}
    s.club_fixtures_{{ n }},
    s.minutes_in_{{ n }},
    s.minutes_share_{{ n }},
    s.appearances_in_{{ n }},
    s.starts_in_{{ n }},
    s.start_rate_{{ n }},
    {% endfor %}

    s.club_last_fixture_at,
    s.minutes_last_club_fixture,
    coalesce(s.started_last_club_fixture, false)    as started_last_club_fixture,
    coalesce(s.last_club_fixture_non_league, false) as last_club_fixture_non_league,

    -- ================================================================
    -- FATIGUE. Day indexed, with denominators so zeroes are readable.
    -- ================================================================
    {% for n in day_windows %}
    coalesce(f.club_fixtures_{{ n }}d, 0)           as club_fixtures_{{ n }}d,
    coalesce(f.minutes_{{ n }}d, 0)                 as minutes_{{ n }}d,
    coalesce(f.fixtures_played_{{ n }}d, 0)         as fixtures_played_{{ n }}d,
    coalesce(f.non_league_minutes_{{ n }}d, 0)      as non_league_minutes_{{ n }}d,
    coalesce(f.non_league_fixtures_{{ n }}d, 0)     as non_league_fixtures_{{ n }}d,
    -- NOT coalesced. A null share means the club played nothing in the window;
    -- zero would say the player was available and unused.
    f.minutes_share_{{ n }}d,
    {% endfor %}

    -- Acute:chronic ratio, the standard sports-science load measure. Null
    -- rather than zero where there is no 28-day baseline to compare against.
    case when coalesce(f.minutes_28d, 0) > 0 then
        (f.minutes_7d * 4.0) / f.minutes_28d
    end                                             as acute_chronic_ratio,

    -- ================================================================
    -- CALENDAR CONTEXT. What makes a break distinguishable from a benching.
    -- ================================================================
    f.last_appearance_at,
    extract(epoch from (r.deadline_utc - f.last_appearance_at)) / 86400
                                                    as days_since_last_appearance,
    extract(epoch from (r.deadline_utc - s.club_last_fixture_at)) / 86400
                                                    as days_since_club_fixture,

    /*
        The player's gap MINUS their club's gap. Near zero means they featured
        in the last available fixture; large means the club has played since
        they last did.

        This is the feature that separates the two cases the old model
        conflated. During an international break every player's raw gap is
        large, but this stays near zero for everyone who played the last
        fixture before it — which is what selection actually depends on.
    */
    extract(epoch from (r.deadline_utc - f.last_appearance_at)) / 86400
        - extract(epoch from (r.deadline_utc - s.club_last_fixture_at)) / 86400
                                                    as excess_days_since_appearance,

    -- Explicit break flag. Three occur a season, so the model has few examples
    -- and benefits from being told rather than having to infer it from a
    -- continuous variable.
    coalesce(
        extract(epoch from (r.deadline_utc - s.club_last_fixture_at)) / 86400 > 14,
        false
    )                                               as follows_break,

    current_timestamp                               as built_at

from roster r

left join selection s
    on  s.snapshot_id = r.snapshot_id
    and s.player_id   = r.player_id

left join fatigue f
    on  f.snapshot_id = r.snapshot_id
    and f.player_id   = r.player_id