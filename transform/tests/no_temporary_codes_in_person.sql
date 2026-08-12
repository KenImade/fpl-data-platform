select distinct player_code
from {{ ref('stg_fpl_players') }}
where has_temporary_code