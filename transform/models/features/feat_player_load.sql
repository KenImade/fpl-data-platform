{{ config(materialized='table', schema='features') }}

/*
    feat_player_load — fatigue and rotation risk, as at each deadline.

    GRAIN: (snapshot_id, player_id). Same as feat_player_form.

    ALL COMPETITIONS, deliberately — this is the mirror of feat_player_form.
    A Thursday Europa tie is exactly the load that causes Sunday rotation, and
    fct_player_gw carries it because the fact was built at fixture grain across
    every competition for this purpose.

    PER-PLAYER, NOT PER-TEAM. "The club played Thursday" is a weak proxy;
    "this player played 90 minutes on Thursday" is close to the actual causal
    variable. stg_playermatchstats gives individual European minutes, which
    most public FPL models do not have because they ingest only the FPL API.
    This is the strongest edge available in the minutes model.

    FRIENDLIES ARE EXCLUDED. stg_matches warns that 2026/27 files 97 friendlies
    under numbered gameweeks; counting them would show a pre-season squad as
    critically fatigued.

    TIME-WINDOWED, NOT FIXTURE-WINDOWED. Load decays with elapsed days, not
    with fixtures played, so windows here are intervals rather than counts —
    the opposite choice to feat_player_form.
*/

{% set day_windows = [7, 14, 21, 28] %}

with deadlines as (
    select distinct snapshot_id, season, gameweek, deadline_utc
    from {{ ref('feat_player_gameweek_spine') }}
),

-- Every appearance in any competitive fixture. Zero-minute rows are kept
-- here, unlike in feat_player_form: being an unused sub is a fact about
-- squad involvement even though it carries no load.
appearances as (
    select
        season,
        player_id,
        match_id,
        kickoff_utc,
        competition,
        is_league,
        minutes
    from {{ ref('fct_player_gw') }}
    where kickoff_utc is not null
      and competition not in ('friendly')
),

ranked as (
    select
        d.snapshot_id,
        d.deadline_utc,
        a.player_id,
        a.kickoff_utc,
        a.minutes,
        a.is_league,
        d.deadline_utc - a.kickoff_utc              as elapsed,
        row_number() over (
            partition by d.snapshot_id, a.player_id
            order by a.kickoff_utc desc
        )                                           as recency
    from deadlines d
    inner join appearances a
        on  a.season      = d.season
        and a.kickoff_utc < d.deadline_utc
        and a.kickoff_utc > d.deadline_utc - interval '28 days'
),

aggregated as (
    select
        snapshot_id,
        player_id,

        {% for n in day_windows %}
        sum(minutes) filter (where elapsed < interval '{{ n }} days')
                                                    as minutes_{{ n }}d,
        count(*) filter (where elapsed < interval '{{ n }} days')
                                                    as fixtures_{{ n }}d,
        count(*) filter (
            where elapsed < interval '{{ n }} days' and not is_league
        )                                           as non_league_fixtures_{{ n }}d,
        sum(minutes) filter (
            where elapsed < interval '{{ n }} days' and not is_league
        )                                           as non_league_minutes_{{ n }}d,
        count(*) filter (
            where elapsed < interval '{{ n }} days' and minutes >= 60
        )                                           as starts_{{ n }}d,
        {% endfor %}

        -- The most recent fixture, whatever competition. The midweek-tie
        -- signal in its rawest form.
        max(kickoff_utc)                            as last_fixture_at,
        max(minutes) filter (where recency = 1)     as last_fixture_minutes,
        bool_or(recency = 1 and not is_league)      as last_fixture_was_non_league
    from ranked
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

    {% for n in day_windows %}
    coalesce(a.minutes_{{ n }}d, 0)                 as minutes_{{ n }}d,
    coalesce(a.fixtures_{{ n }}d, 0)                as fixtures_{{ n }}d,
    coalesce(a.non_league_fixtures_{{ n }}d, 0)     as non_league_fixtures_{{ n }}d,
    coalesce(a.non_league_minutes_{{ n }}d, 0)      as non_league_minutes_{{ n }}d,
    coalesce(a.starts_{{ n }}d, 0)                  as starts_{{ n }}d,
    {% endfor %}

    -- Zero rather than NULL throughout: a player with no fixtures in the
    -- window genuinely has no load. That is the opposite of feat_player_form,
    -- where absence means unknown rather than none.

    a.last_fixture_at,
    a.last_fixture_minutes,
    coalesce(a.last_fixture_was_non_league, false)  as last_fixture_was_non_league,
    extract(epoch from (d.deadline_utc - a.last_fixture_at)) / 86400
                                                    as days_since_last_fixture,

    -- Acute:chronic ratio, the standard sports-science load measure. Recent
    -- load against the established baseline: above 1 means a spike.
    case when coalesce(a.minutes_28d, 0) > 0 then
        (a.minutes_7d * 4.0) / a.minutes_28d
    end                                             as acute_chronic_ratio,

    -- Team-level congestion from fct_team_fixture, which counts fixtures the
    -- club played whether or not this player featured. A squad player at a
    -- congested club is a rotation BENEFICIARY, so this is signed opposite to
    -- the player's own load and both are needed.
    t.matches_prior_14d                             as team_matches_prior_14d,
    t.days_since_last_match                         as team_days_since_last_match,

    current_timestamp                               as built_at

from deadlines d

inner join {{ ref('feat_player_gameweek_spine') }} s
    on s.snapshot_id = d.snapshot_id

left join aggregated a
    on  a.snapshot_id = d.snapshot_id
    and a.player_id   = s.player_id

-- Team congestion as at the club's most recent fixture before the deadline.
left join lateral (
    select matches_prior_14d, days_since_last_match
    from {{ ref('fct_team_fixture') }} tf
    where tf.team_code   = s.team_code
      and tf.season      = d.season
      and tf.kickoff_utc < d.deadline_utc
      and tf.competition not in ('friendly')
    order by tf.kickoff_utc desc
    limit 1
) t on true