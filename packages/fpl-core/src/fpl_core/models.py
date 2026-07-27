from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

from fpl_core.ids import FixtureId, PlayerCode, PlayerId, SeasonId, TeamId
from fpl_core.money import Price


class Position(IntEnum):
    GKP = 1
    DEF = 2
    MID = 3
    FWD = 4

    @property
    def short(self) -> str:
        return self.name


_POSITION_NAMES = {
    "Goalkeeper": Position.GKP,
    "Defender": Position.DEF,
    "Midfielder": Position.MID,
    "Forward": Position.FWD,
}


def position_from_name(name: str) -> Position:
    """Parse Core Insights' full-word position, e.g. 'Midfielder'."""
    try:
        return _POSITION_NAMES[name]
    except KeyError:
        raise ValueError(f"Unknown position name: {name!r}") from None


def position_from_element_type(value: int) -> Position:
    """Parse FPL API's element_type (1-4)."""
    try:
        return Position(value)
    except ValueError:
        raise ValueError(f"unknown element_type: {value!r}") from None


@dataclass(frozen=True, slots=True)
class Team:
    id: TeamId
    name: str
    short_name: str


@dataclass(frozen=True, slots=True)
class Player:
    id: PlayerId
    code: PlayerCode
    web_name: str
    position: Position
    team_id: TeamId
    price: Price


@dataclass(frozen=True, slots=True)
class Gameweek:
    number: int
    season: SeasonId
    deadline: datetime


@dataclass(frozen=True, slots=True)
class Fixture:
    id: FixtureId
    gameweek: int | None
    home: TeamId
    away: TeamId
    kickoff: datetime | None
