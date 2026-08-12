/*
    The point-in-time guarantee, made checkable.

    Every row this returns is a leak: a feature window that reached past the
    deadline it is attached to. The models are written so this cannot happen —
    every join predicate bounds on kickoff_utc < deadline_utc — but "written so
    it cannot happen" is a claim, and this is the thing that tests it.

    Bounding on the target FIXTURE's kickoff rather than the deadline is the
    mistake this catches. A Thursday European tie falls after the deadline but
    before Sunday's league match, so a kickoff-bounded window would include a
    result the manager could not have known.
*/

-- Form: the most recent league appearance feeding the rolling windows.
select
    snapshot_id,
    player_id,
    'feat_player_form.last_appearance_at'   as source,
    last_appearance_at                      as observed_at,
    deadline_utc
from {{ ref('feat_player_form') }}
where last_appearance_at >= deadline_utc

union all

-- Load: the player's most recent appearance in any competition.
select
    snapshot_id,
    player_id,
    'feat_player_load.last_appearance_at',
    last_appearance_at,
    deadline_utc
from {{ ref('feat_player_load') }}
where last_appearance_at >= deadline_utc

union all

/*
    Load: the club's most recent fixture. This one matters as much as the
    player's — it is the denominator for every selection-share feature, so a
    leak here would contaminate minutes_share_*, start_rate_* and
    excess_days_since_appearance all at once.
*/
select
    snapshot_id,
    player_id,
    'feat_player_load.club_last_fixture_at',
    club_last_fixture_at,
    deadline_utc
from {{ ref('feat_player_load') }}
where club_last_fixture_at >= deadline_utc

union all

-- The unified spine, which computes prior_appearances from the same windows.
select
    snapshot_id,
    player_id,
    'feat_player_gameweek_spine.last_appearance_at',
    last_appearance_at,
    deadline_utc
from {{ ref('feat_player_gameweek_spine') }}
where last_appearance_at >= deadline_utc