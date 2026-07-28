-- A club's display name is not stable: "Ipswich" in 2024/25 became
-- "Ipswich Town" in 2026/27. That's harmless, because team_code carries
-- identity.
--
-- The reverse would not be harmless. If one club ever acquired two codes,
-- its history would split in two and nothing would announce it — the
-- per-season uniqueness test would still pass, and every cross-season
-- aggregate would quietly undercount.
--
-- short_name is the most stable human-readable handle, so a short_name
-- mapping to more than one code is the signal.

select
    team_short,
    count(distinct team_code) as codes,
    array_agg(distinct team_code) as code_list
from {{ ref('stg_teams') }}
group by team_short
having count(distinct team_code) > 1