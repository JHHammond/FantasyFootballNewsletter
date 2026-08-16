"""
Provider-agnostic data model for The Commissioner's Desk.

Every platform adapter (Sleeper, ESPN, Yahoo, ...) normalizes its raw API
response into these types. Nothing downstream -- storylines, lineup_optimizer,
writer, newspaper -- should ever see a platform-specific field again.

Design rules:
  * Player IDs are namespaced ("sleeper:4046") so IDs from different platforms
    can never collide, and so a stored newspaper can always be traced back to
    its source.
  * Points are always floats, never None. A player who did not play scored 0.0.
    Projections may be None (the platform may genuinely not have one).
  * Slot names are canonical (see SLOT_ELIGIBILITY). Platform-specific names
    like Sleeper's "WRRB_FLEX" or ESPN's numeric slot IDs are mapped on the way in.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional


# --------------------------------------------------------------------------
# Slots
# --------------------------------------------------------------------------

#: Which real positions may legally occupy each canonical slot.
SLOT_ELIGIBILITY: dict[str, frozenset[str]] = {
    "QB":          frozenset({"QB"}),
    "RB":          frozenset({"RB"}),
    "WR":          frozenset({"WR"}),
    "TE":          frozenset({"TE"}),
    "K":           frozenset({"K"}),
    "DEF":         frozenset({"DEF"}),
    # Individual defensive players (IDP leagues)
    "DL":          frozenset({"DL", "DE", "DT"}),
    "LB":          frozenset({"LB"}),
    "DB":          frozenset({"DB", "CB", "S"}),
    # Flex variants
    "FLEX":        frozenset({"RB", "WR", "TE"}),
    "REC_FLEX":    frozenset({"WR", "TE"}),
    "WR_RB_FLEX":  frozenset({"WR", "RB"}),
    "SUPER_FLEX":  frozenset({"QB", "RB", "WR", "TE"}),
    "IDP_FLEX":    frozenset({"DL", "DE", "DT", "LB", "DB", "CB", "S"}),
}

#: Slots that do not score. A player here contributed nothing this week.
BENCH_SLOTS: frozenset[str] = frozenset({"BN", "IR", "TAXI"})

#: Ordering used when solving for the optimal lineup. Most-constrained slots
#: are filled first so a flex never steals a player a dedicated slot needed.
SLOT_FILL_PRIORITY: tuple[str, ...] = (
    "QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB",
    "WR_RB_FLEX", "REC_FLEX", "FLEX", "IDP_FLEX", "SUPER_FLEX",
)


def is_starting_slot(slot: str) -> bool:
    """True if a player in this slot's points count toward the team score."""
    return slot not in BENCH_SLOTS


def can_fill_slot(position: str | None, slot: str) -> bool:
    """True if a player at `position` is legally allowed to occupy `slot`."""
    if not position:
        return False
    return position in SLOT_ELIGIBILITY.get(slot, frozenset())


def can_fill_slot_any(positions: Iterable[str] | None, slot: str) -> bool:
    """True if any of a player's eligible positions can occupy `slot`."""
    if not positions:
        return False
    eligible = SLOT_ELIGIBILITY.get(slot, frozenset())
    return any(p in eligible for p in positions)


# --------------------------------------------------------------------------
# Core records
# --------------------------------------------------------------------------

@dataclass
class PlayerLine:
    """One player's contribution to one team in one week."""

    player_id: str                          # namespaced, e.g. "sleeper:4046"
    name: str
    position: str                           # primary fantasy position
    points: float                           # actual scored, never None
    slot: str = "BN"                        # canonical slot occupied
    positions: tuple[str, ...] = ()         # all eligible positions
    nfl_team: Optional[str] = None          # "KC", "SF", ...
    projected: Optional[float] = None
    headshot_url: Optional[str] = None
    injury_status: Optional[str] = None     # "Questionable", "Out", "IR", ...

    @property
    def started(self) -> bool:
        return is_starting_slot(self.slot)

    @property
    def vs_projection(self) -> Optional[float]:
        """Points above (+) or below (-) projection. None if no projection."""
        if self.projected is None:
            return None
        return round(self.points - self.projected, 2)

    @property
    def is_goose_egg(self) -> bool:
        """Scored zero or worse. The chug counter cares about this."""
        return self.points <= 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["positions"] = list(self.positions)
        d["started"] = self.started
        d["vs_projection"] = self.vs_projection
        return d


@dataclass
class Manager:
    """The human behind a team."""

    manager_id: str
    display_name: str
    avatar_url: Optional[str] = None
    is_commissioner: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Team:
    """A fantasy team's state for one specific week."""

    team_id: str
    team_name: str
    manager: Manager
    points: float = 0.0
    lineup: list[PlayerLine] = field(default_factory=list)   # starters
    bench: list[PlayerLine] = field(default_factory=list)
    # Record ENTERING this week -- excludes the week being reported on, so
    # "upset" and "fraud" logic can compare standings as they stood at kickoff.
    wins: int = 0
    losses: int = 0
    ties: int = 0
    # Filled in by the optimizer, not the provider.
    optimal_points: Optional[float] = None

    @property
    def record(self) -> str:
        base = f"{self.wins}-{self.losses}"
        return f"{base}-{self.ties}" if self.ties else base

    @property
    def games_played(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def empty_slots(self) -> int:
        """Starting slots with nobody in them. Pure managerial neglect."""
        return sum(1 for p in self.lineup if not p.player_id)

    @property
    def all_players(self) -> list[PlayerLine]:
        return list(self.lineup) + list(self.bench)

    @property
    def lineup_gap(self) -> float:
        """Points left on the bench. 0.0 until the optimizer has run."""
        if self.optimal_points is None:
            return 0.0
        return max(round(self.optimal_points - self.points, 2), 0.0)

    @property
    def top_scorer(self) -> Optional[PlayerLine]:
        real = [p for p in self.lineup if p.player_id]
        return max(real, key=lambda p: p.points) if real else None

    @property
    def low_scorer(self) -> Optional[PlayerLine]:
        """Fewest raw points. Usually a kicker -- see `biggest_bust` instead."""
        real = [p for p in self.lineup if p.player_id]
        return min(real, key=lambda p: p.points) if real else None

    @property
    def biggest_bust(self) -> Optional[PlayerLine]:
        """Started, had a projection, missed it by the most. The roast target.

        This is deliberately NOT `low_scorer`: the lowest raw score is almost
        always a kicker or defense doing something unremarkable, whereas the
        biggest bust is the stud who was supposed to win you the week.
        """
        candidates = [
            p for p in self.lineup
            if p.player_id and p.vs_projection is not None
        ]
        return min(candidates, key=lambda p: p.vs_projection) if candidates else None

    @property
    def biggest_boom(self) -> Optional[PlayerLine]:
        """Started, had a projection, beat it by the most."""
        candidates = [
            p for p in self.lineup
            if p.player_id and p.vs_projection is not None
        ]
        return max(candidates, key=lambda p: p.vs_projection) if candidates else None

    @property
    def goose_eggs(self) -> list[PlayerLine]:
        """Started and scored nothing. One chug each."""
        return [p for p in self.lineup if p.player_id and p.is_goose_egg]

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "team_name": self.team_name,
            "manager": self.manager.to_dict(),
            "points": self.points,
            "record": self.record,
            "wins": self.wins,
            "losses": self.losses,
            "ties": self.ties,
            "lineup": [p.to_dict() for p in self.lineup],
            "bench": [p.to_dict() for p in self.bench],
            "optimal_points": self.optimal_points,
            "lineup_gap": self.lineup_gap,
            "empty_slots": self.empty_slots,
        }


@dataclass
class Matchup:
    """Two teams, one week, one result."""

    matchup_id: str
    teams: tuple[Team, Team]

    @property
    def team_1(self) -> Team:
        return self.teams[0]

    @property
    def team_2(self) -> Team:
        return self.teams[1]

    @property
    def is_tie(self) -> bool:
        return self.teams[0].points == self.teams[1].points

    @property
    def winner(self) -> Optional[Team]:
        if self.is_tie:
            return None
        return max(self.teams, key=lambda t: t.points)

    @property
    def loser(self) -> Optional[Team]:
        if self.is_tie:
            return None
        return min(self.teams, key=lambda t: t.points)

    @property
    def margin(self) -> float:
        return round(abs(self.teams[0].points - self.teams[1].points), 2)

    @property
    def combined_points(self) -> float:
        return round(self.teams[0].points + self.teams[1].points, 2)

    def to_dict(self) -> dict[str, Any]:
        w = self.winner
        return {
            "matchup_id": self.matchup_id,
            "team_1": self.teams[0].to_dict(),
            "team_2": self.teams[1].to_dict(),
            "winner": w.team_name if w else "Tie",
            "margin": self.margin,
            "is_tie": self.is_tie,
        }


@dataclass
class League:
    """League configuration. Stable across a season."""

    provider: str                            # "sleeper", "espn", "yahoo"
    league_id: str
    name: str
    season: int
    roster_slots: list[str] = field(default_factory=list)   # incl. BN/IR/TAXI
    team_count: int = 0
    scoring_type: str = "ppr"                # "std" | "half_ppr" | "ppr"
    avatar_url: Optional[str] = None
    previous_league_id: Optional[str] = None
    commissioner_ids: list[str] = field(default_factory=list)

    @property
    def starting_slots(self) -> list[str]:
        return [s for s in self.roster_slots if is_starting_slot(s)]

    @property
    def is_superflex(self) -> bool:
        return "SUPER_FLEX" in self.roster_slots

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["starting_slots"] = self.starting_slots
        return d


@dataclass
class WeekData:
    """Everything that happened in one league in one week."""

    league: League
    week: int
    matchups: list[Matchup] = field(default_factory=list)
    byes: list[Team] = field(default_factory=list)   # odd team counts / playoffs

    @property
    def teams(self) -> list[Team]:
        out: list[Team] = []
        for m in self.matchups:
            out.extend(m.teams)
        out.extend(self.byes)
        return out

    @property
    def season(self) -> int:
        return self.league.season

    def team_by_id(self, team_id: str) -> Optional[Team]:
        return next((t for t in self.teams if t.team_id == team_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "league": self.league.to_dict(),
            "week": self.week,
            "season": self.season,
            "matchups": [m.to_dict() for m in self.matchups],
            "byes": [t.to_dict() for t in self.byes],
        }
