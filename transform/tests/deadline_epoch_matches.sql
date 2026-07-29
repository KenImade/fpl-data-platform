-- deadline_time and deadline_time_epoch are independent encodings of the
-- same instant. If they diverge, one of them has been misparsed — most
-- likely a timezone assumption on the text form, which would shift every
-- point-in-time boundary by hours.

select season, gameweek, deadline_utc, deadline_epoch,
       extract(epoch from deadline_utc)::bigint as derived_epoch
from {{ ref('stg_gameweeks') }}
where extract(epoch from deadline_utc)::bigint <> deadline_epoch