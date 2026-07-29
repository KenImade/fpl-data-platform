/*
    The leakage guarantee, asserted rather than assumed.

    Every row in fct_player_snapshot must derive from a capture strictly
    before its deadline. If this ever returns rows, every model trained on
    these features has seen the future, and every backtest result is void.

    Cheap to run, catastrophic to skip.
*/

select
    snapshot_id,
    player_id,
    snapshot_at,
    deadline_utc,
    snapshot_at - deadline_utc as leakage
from {{ ref('fct_player_snapshot') }}
where snapshot_at >= deadline_utc