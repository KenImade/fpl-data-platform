from __future__ import annotations

from dataclasses import dataclass

from fpl_core.models import Position
from fpl_core.rules import Ruleset


@dataclass(frozen=True, slots=True)
class MatchStats:
    """One player, one fixture. The minimum needed to score a performance"""

    position: Position
    minutes: int
    goals: int = 0
    assists: int = 0
    goals_conceded_on_pitch: int = 0
    saves: int = 0
    penalties_saved: int = 0
    penalties_missed: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    own_goals: int = 0
    defensive_contributions: int = 0
    bonus: int = 0


@dataclass(frozen=True, slots=True)
class PointsBreakdown:
    appearance: int = 0
    goals: int = 0
    assists: int = 0
    clean_sheet: int = 0
    goals_conceded: int = 0
    saves: int = 0
    penalties_saved: int = 0
    penalties_missed: int = 0
    cards: int = 0
    own_goals: int = 0
    defensive_contribution: int = 0
    bonus: int = 0

    @property
    def total(self) -> int:
        return (
            self.appearance
            + self.goals
            + self.assists
            + self.clean_sheet
            + self.goals_conceded
            + self.saves
            + self.penalties_saved
            + self.penalties_missed
            + self.cards
            + self.own_goals
            + self.defensive_contribution
            + self.bonus
        )


def score(stats: MatchStats, rules: Ruleset) -> PointsBreakdown:
    disciplinary = PointsBreakdown(
        cards=(stats.yellow_cards * rules.cards.yellow + stats.red_cards * rules.cards.red),
        own_goals=stats.own_goals * rules.own_goal,
    )
    if stats.minutes == 0:
        return disciplinary

    played_60 = stats.minutes >= 60

    appearance = rules.appearance.played_60 if played_60 else rules.appearance.played

    # Clean sheet: no goals conceded while on the pitch, AND played 60+.
    # A player subbed off before a goal keeps the clean sheet.
    clean_sheet = 0
    if played_60 and stats.goals_conceded_on_pitch == 0:
        clean_sheet = rules.clean_sheet_points(stats.position)

    # Concession penalty applies regardless of the 60-minute threshold.
    goals_conceded = 0
    if stats.position in (Position.GKP, Position.DEF):
        goals_conceded = rules.goals_conceded.award(stats.goals_conceded_on_pitch)

    saves = rules.saves.award(stats.saves) if stats.position is Position.GKP else 0

    return PointsBreakdown(
        appearance=appearance,
        goals=stats.goals * rules.goal_points(stats.position),
        assists=stats.assists * rules.assists,
        clean_sheet=clean_sheet,
        goals_conceded=goals_conceded,
        saves=saves,
        penalties_saved=stats.penalties_saved * rules.penalties.saved,
        penalties_missed=stats.penalties_missed * rules.penalties.missed,
        cards=(stats.yellow_cards * rules.cards.yellow + stats.red_cards * rules.cards.red),
        own_goals=stats.own_goals * rules.own_goal,
        defensive_contribution=rules.defensive_contribution.award(
            stats.position, stats.defensive_contributions
        ),
        bonus=stats.bonus,
    )
