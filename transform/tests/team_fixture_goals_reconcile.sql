-- The two rows of a fixture are mirror images: one team's goals_for is the
-- other's goals_against. If the unpivot ever mismatches those columns, every
-- defensive statistic in the warehouse inverts — and nothing else would
-- notice, because both values are plausible integers.
 
with pairs as (
    select
        match_id,
        max(goals_for)  filter (where is_home)      as home_gf,
        max(goals_against) filter (where is_home)   as home_ga,
        max(goals_for)  filter (where not is_home)  as away_gf,
        max(goals_against) filter (where not is_home) as away_ga
    from {{ ref('fct_team_fixture') }}
    where is_league and result is not null
    group by match_id
)
 
select *
from pairs
where home_gf is distinct from away_ga
   or home_ga is distinct from away_gf