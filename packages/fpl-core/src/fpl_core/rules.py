from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from fpl_core.models import Position


class RulesetError(Exception):
    """Ruleset file missing, malformed, or internally inconsistent."""


# --- parsing helpers: fail loudly, never coerce ---------------------------


def _req(d: Any, key: str, ctx: str) -> Any:
    if not isinstance(d, dict) or key not in d:
        raise RulesetError(f"{ctx}: missing required key {key!r}")
    return d[key]


def _int(d: Any, key: str, ctx: str) -> int:
    v = _req(d, key, ctx)
    if not isinstance(v, int) or isinstance(v, bool):
        raise RulesetError(f"{ctx}.{key}: expected int, got {type(v).__name__}")
    return v


def _by_position(d: Any, ctx: str, *, required: bool = True) -> dict[Position, int]:
    """Parse a {GKP: n, DEF: n, ...} block. Absent position = ineligible."""
    if not isinstance(d, dict):
        raise RulesetError(f"{ctx}: expected mapping, got {type(d).__name__}")
    out: dict[Position, int] = {}
    for name, value in d.items():
        try:
            pos = Position[str(name)]
        except KeyError:
            raise RulesetError(f"{ctx}: unknown position {name!r}") from None
        if not isinstance(value, int) or isinstance(value, bool):
            raise RulesetError(f"{ctx}.{name}: expected int, got {type(value).__name__}")
        out[pos] = value
    if required:
        missing = [p.name for p in Position if p not in out]
        if missing:
            raise RulesetError(f"{ctx}: missing positions {missing}")
    return out


def _by_position_bool(d: Any, ctx: str) -> dict[Position, bool]:
    """Parse a {DEF: false, MID: true, ...} block. Absent position = False."""
    if not isinstance(d, dict):
        raise RulesetError(f"{ctx}: expected mapping, got {type(d).__name__}")
    out: dict[Position, bool] = {}
    for name, value in d.items():
        try:
            pos = Position[str(name)]
        except KeyError:
            raise RulesetError(f"{ctx}: unknown position {name!r}") from None
        if not isinstance(value, bool):
            raise RulesetError(f"{ctx}.{name}: expected bool, got {type(value).__name__}")
        out[pos] = value
    return out


# --- structures ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Appearance:
    played: int
    played_60: int


@dataclass(frozen=True, slots=True)
class PerN:
    """Points awarded per N occurrences, e.g. 1 point per 3 saves."""

    per_n: int
    points: int

    def award(self, count: int) -> int:
        return (count // self.per_n) * self.points


@dataclass(frozen=True, slots=True)
class Cards:
    yellow: int
    red: int


@dataclass(frozen=True, slots=True)
class Penalties:
    saved: int
    missed: int


@dataclass(frozen=True, slots=True)
class DefensiveContribution:
    points: int
    thresholds: dict[Position, int]  # absent position = ineligible
    recoveries_count: dict[Position, bool]

    def award(self, position: Position, count: int) -> int:
        threshold = self.thresholds.get(position)
        if threshold is None:
            return 0
        return self.points if count >= threshold else 0

    def actions(
        self,
        position: Position,
        *,
        tackles: int,
        clearances_blocks_interceptions: int,
        recoveries: int,
    ) -> int:
        """Count eligible defensive actions for this position."""
        if position not in self.thresholds:
            return 0
        total = tackles + clearances_blocks_interceptions
        if self.recoveries_count.get(position, False):
            total += recoveries
        return total


@dataclass(frozen=True, slots=True)
class Ruleset:
    ruleset_id: str
    season: str
    appearance: Appearance
    goals: dict[Position, int]
    assists: int
    clean_sheet: dict[Position, int]
    goals_conceded: PerN
    saves: PerN
    penalties: Penalties
    cards: Cards
    own_goal: int
    defensive_contribution: DefensiveContribution

    def goal_points(self, position: Position) -> int:
        return self.goals[position]

    def clean_sheet_points(self, position: Position) -> int:
        return self.clean_sheet[position]


# --- loading -------------------------------------------------------------


def _rulesets_dir() -> Path:
    if override := os.environ.get("FPL_RULESETS_DIR"):
        return Path(override)
    return Path(__file__).resolve().parents[4] / "rulesets"


def _parse(raw: Any, source: str) -> Ruleset:
    if not isinstance(raw, dict):
        raise RulesetError(f"{source}: expected top-level mapping")

    dc_raw = _req(raw, "defensive_contribution", source)

    ruleset = Ruleset(
        ruleset_id=str(_req(raw, "ruleset_id", source)),
        season=str(_req(raw, "season", source)),
        appearance=Appearance(
            played=_int(_req(raw, "appearance", source), "played", f"{source}.appearance"),
            played_60=_int(_req(raw, "appearance", source), "played_60", f"{source}.appearance"),
        ),
        goals=_by_position(_req(raw, "goals", source), f"{source}.goals"),
        assists=_int(raw, "assists", source),
        clean_sheet=_by_position(_req(raw, "clean_sheet", source), f"{source}.clean_sheet"),
        goals_conceded=PerN(
            per_n=_int(_req(raw, "goals_conceded", source), "per_n_conceded", source),
            points=_int(_req(raw, "goals_conceded", source), "points", source),
        ),
        saves=PerN(
            per_n=_int(_req(raw, "saves", source), "per_n_saves", source),
            points=_int(_req(raw, "saves", source), "points", source),
        ),
        penalties=Penalties(
            saved=_int(_req(raw, "penalties", source), "saved", source),
            missed=_int(_req(raw, "penalties", source), "missed", source),
        ),
        cards=Cards(
            yellow=_int(_req(raw, "cards", source), "yellow", source),
            red=_int(_req(raw, "cards", source), "red", source),
        ),
        own_goal=_int(raw, "own_goal", source),
        defensive_contribution=DefensiveContribution(
            points=_int(dc_raw, "points", f"{source}.defensive_contribution"),
            thresholds=_by_position(
                _req(dc_raw, "thresholds", source),
                f"{source}.defensive_contribution.thresholds",
                required=False,  # GKP is legitimately absent
            ),
            recoveries_count=_by_position_bool(
                _req(dc_raw, "recoveries_count", source),
                f"{source}.defensive_contribution.recoveries_count",
            ),
        ),
    )

    dc = ruleset.defensive_contribution
    if orphans := set(dc.recoveries_count) - set(dc.thresholds):
        raise RulesetError(
            f"{source}.defensive_contribution: {[p.name for p in orphans]} "
            "have recoveries_count but no threshold"
        )

    return ruleset


@cache
def load(ruleset_id: str) -> Ruleset:
    path = _rulesets_dir() / f"{ruleset_id}.yml"
    if not path.is_file():
        raise RulesetError(f"no ruleset at {path}")
    raw = yaml.safe_load(path.read_text())
    ruleset = _parse(raw, path.name)
    if ruleset.ruleset_id != ruleset_id:
        raise RulesetError(
            f"{path.name}: declares id {ruleset.ruleset_id!r}, expected {ruleset_id!r}"
        )
    return ruleset
