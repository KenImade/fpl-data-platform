-- matches_prior_14d counts fixtures strictly BEFORE the current one. If the
-- window frame ever loses its exclusive upper bound, a match counts itself
-- and every congestion feature shifts by one — a bias too small to look
-- wrong and large enough to matter.
-- A team cannot plausibly play more than 7 matches in a 14-day window.
 
select
    match_id,
    team_code,
    kickoff_utc,
    matches_prior_14d
from {{ ref('fct_team_fixture') }}
where matches_prior_14d > 7