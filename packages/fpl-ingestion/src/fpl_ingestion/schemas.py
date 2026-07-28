"""Declared schemas for FPL payloads.

Two jobs:

1. Validation. A missing or retyped required field fails the partition
   loudly rather than propagating a silently wrong value. Unknown fields are
   kept and reported, not rejected — FPL adds fields mid-season and losing
   the data would be worse than not knowing about it.

2. A stable polars schema. Without an explicit one, polars infers column
   types per partition: `chance_of_playing_next_round` is all-null in
   pre-season (typing as Null) and integer once the season starts (Int64),
   so a union across those partitions fails.
"""

from __future__ import annotations

import types
import typing
from typing import Any

import polars as pl
from pydantic import BaseModel, ConfigDict


class Element(BaseModel):
    """A player in bootstrap-static.elements.

    Raw fidelity is deliberate. `selected_by_percent` and `ep_next` arrive as
    strings despite being numeric; they stay strings here. Type conversion
    belongs in dbt staging, so bronze stays a faithful record of what the API
    actually sent.
    """

    model_config = ConfigDict(extra="allow")

    id: int
    code: int
    web_name: str
    first_name: str
    second_name: str
    element_type: int
    team: int
    team_code: int
    now_cost: int
    status: str
    news: str
    chance_of_playing_next_round: int | None = None
    chance_of_playing_this_round: int | None = None
    total_points: int
    event_points: int
    minutes: int
    selected_by_percent: str
    ep_next: str | None = None
    ep_this: str | None = None


_POLARS_TYPES: dict[Any, pl.DataType | type[pl.DataType]] = {
    int: pl.Int64,
    float: pl.Float64,
    str: pl.String,
    bool: pl.Boolean,
}

KNOWN_UNMAPPED = frozenset(
    {
        "assists",
        "birth_date",
        "bonus",
        "bps",
        "can_select",
        "can_transact",
        "clean_sheets",
        "clean_sheets_per_90",
        "clearances_blocks_interceptions",
        "corners_and_indirect_freekicks_order",
        "corners_and_indirect_freekicks_text",
        "cost_change_event",
        "cost_change_event_fall",
        "cost_change_start",
        "cost_change_start_fall",
        "creativity",
        "creativity_rank",
        "creativity_rank_type",
        "defensive_contribution",
        "defensive_contribution_per_90",
        "direct_freekicks_order",
        "direct_freekicks_text",
        "dreamteam_count",
        "expected_assists",
        "expected_assists_per_90",
        "expected_goal_involvements",
        "expected_goal_involvements_per_90",
        "expected_goals",
        "expected_goals_conceded",
        "expected_goals_conceded_per_90",
        "expected_goals_per_90",
        "form",
        "form_rank",
        "form_rank_type",
        "goals_conceded",
        "goals_conceded_per_90",
        "goals_scored",
        "has_temporary_code",
        "ict_index",
        "ict_index_rank",
        "ict_index_rank_type",
        "in_dreamteam",
        "influence",
        "influence_rank",
        "influence_rank_type",
        "known_name",
        "news_added",
        "now_cost_rank",
        "now_cost_rank_type",
        "opta_code",
        "own_goals",
        "penalties_missed",
        "penalties_order",
        "penalties_saved",
        "penalties_text",
        "photo",
        "points_per_game",
        "points_per_game_rank",
        "points_per_game_rank_type",
        "price_change_percent",
        "recoveries",
        "red_cards",
        "region",
        "removed",
        "saves",
        "saves_per_90",
        "scout_news_link",
        "scout_risks",
        "selected_rank",
        "selected_rank_type",
        "special",
        "squad_number",
        "starts",
        "starts_per_90",
        "tackles",
        "team_join_date",
        "threat",
        "threat_rank",
        "threat_rank_type",
        "transfers_in",
        "transfers_in_event",
        "transfers_out",
        "transfers_out_event",
        "value_form",
        "value_season",
        "yellow_cards",
    }
)


def _unwrap_optional(annotation: object) -> object:
    """`int | None` -> `int`. Nullability is free in polars; only the
    underlying type needs mapping."""
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) != 1:
            raise TypeError(f"cannot map union with multiple types: {annotation}")
        return args[0]
    return annotation


def polars_schema(model: type[BaseModel]) -> dict[str, pl.DataType]:
    """Explicit polars schema derived from a Pydantic model.

    Deriving rather than hand-writing means the two cannot drift: adding a
    field to the model automatically adds it to the schema, and adding one
    with an unmapped type fails immediately and by name.
    """
    schema: dict[str, pl.DataType] = {}
    for name, field in model.model_fields.items():
        annotation = _unwrap_optional(field.annotation)
        try:
            schema[name] = _POLARS_TYPES[annotation]  # type: ignore[index]
        except KeyError:
            raise TypeError(
                f"{model.__name__}.{name}: no polars type mapped for {annotation}. "
                f"Add it to _POLARS_TYPES."
            ) from None
    return schema


def unknown_fields(model: BaseModel) -> set[str]:
    """Fields present in the payload but absent from the model.

    The additive half of schema drift. These are dropped when writing with an
    explicit schema, so surfacing them is how a new FPL field gets noticed and
    added deliberately rather than discovered months later.
    """
    return set(model.model_extra or {})


def novel_fields(model: BaseModel) -> set[str]:
    """Fields we've never seen before.

    Undeclared-but-known fields are deliberately excluded — the signal is a
    NEW field appearing, not the standing set we've chosen not to model.
    """
    return set(model.model_extra or {}) - set(type(model).model_fields) - KNOWN_UNMAPPED
