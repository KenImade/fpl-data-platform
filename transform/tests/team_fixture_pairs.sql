-- Every LEAGUE match must produce exactly two rows — one per team. European
-- and cup fixtures produce one, because the non-PL side is dropped.
-- A league match with one row means a null team_code slipped through, and
-- every team-level aggregate over that fixture would be half right. A match
-- with three means the unpivot duplicated.
 
select
    f.match_id,
    count(*) as team_rows
from {{ ref('fct_team_fixture') }} f
where f.is_league
group by f.match_id
having count(*) <> 2