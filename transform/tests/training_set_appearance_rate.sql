
-- Shape check rather than correctness. Outside 30-60% means the spine is
-- wrong in one direction or the other — see feat_training_set's description.
select season, avg(did_appear::int) as rate
from {{ ref('feat_training_set') }} t
join {{ ref('dim_gameweek') }} g using (season, gameweek)
where g.is_finished
group by 1
having avg(did_appear::int) not between 0.30 and 0.60