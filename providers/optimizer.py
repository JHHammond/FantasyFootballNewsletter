"""
Optimal-lineup solver, operating on the normalized model.

Answers "what was the most this roster could have scored, if the manager had
been clairvoyant?" -- which is what the Gardner Minshew award and every
"points left on the bench" line in the paper depend on.

Why this isn't the old greedy-by-slot-order loop: that version walked
roster_positions in whatever order the platform happened to list them and took
the highest scorer eligible for each. That is only correct when eligibility
happens to be nested and the slots happen to be ordered most-constrained-first.
A league that lists SUPER_FLEX before QB, or FLEX before RB, gets a wrong
answer -- the flex eats the best running back and the dedicated RB slots are
left with scraps.

This solves it as a maximum-weight bipartite matching instead. Assignable sets
of players form a transversal matroid, so processing players in descending
point order and augmenting when a slot is contested is provably optimal.
"""

from __future__ import annotations

from .models import BENCH_SLOTS, PlayerLine, Team, WeekData, can_fill_slot_any


def _eligible_slots(player: PlayerLine, slots: list[str]) -> list[int]:
    """Indices of every slot this player could legally occupy."""
    positions = player.positions or ((player.position,) if player.position else ())
    return [i for i, slot in enumerate(slots) if can_fill_slot_any(positions, slot)]


def _try_assign(
    player_idx: int,
    players: list[PlayerLine],
    slots: list[str],
    slot_owner: list[int | None],
    visited: set[int],
) -> bool:
    """Kuhn's augmenting path: seat this player, bumping others if it helps."""
    for slot_idx in _eligible_slots(players[player_idx], slots):
        if slot_idx in visited:
            continue
        visited.add(slot_idx)

        occupant = slot_owner[slot_idx]
        if occupant is None or _try_assign(occupant, players, slots, slot_owner, visited):
            slot_owner[slot_idx] = player_idx
            return True
    return False


def optimal_lineup(team: Team, roster_slots: list[str]) -> tuple[float, list[PlayerLine]]:
    """Return (best possible score, the lineup that achieves it).

    Considers every player on the roster -- starters and bench alike -- because
    the whole point is to find the lineup the manager should have set.
    """
    slots = [s for s in roster_slots if s not in BENCH_SLOTS]
    if not slots:
        return 0.0, []

    # Real players only; empty slots can't be assigned to anything.
    players = [p for p in team.all_players if p.player_id]
    # Descending points: greedy in this order is optimal over a transversal matroid.
    players.sort(key=lambda p: p.points, reverse=True)

    slot_owner: list[int | None] = [None] * len(slots)

    for idx, player in enumerate(players):
        # A negative-scoring player never improves the total; leave the slot empty.
        if player.points <= 0:
            continue
        _try_assign(idx, players, slots, slot_owner, set())

    best_lineup: list[PlayerLine] = []
    total = 0.0
    for slot_idx, player_idx in enumerate(slot_owner):
        if player_idx is None:
            continue
        chosen = players[player_idx]
        total += chosen.points
        # Copy so we don't mutate the real lineup's slot assignment.
        best_lineup.append(
            PlayerLine(
                player_id=chosen.player_id,
                name=chosen.name,
                position=chosen.position,
                points=chosen.points,
                slot=slots[slot_idx],
                positions=chosen.positions,
                nfl_team=chosen.nfl_team,
                projected=chosen.projected,
                headshot_url=chosen.headshot_url,
                injury_status=chosen.injury_status,
            )
        )

    return round(total, 2), best_lineup


def apply_lineup_gaps(week_data: WeekData) -> WeekData:
    """Fill in optimal_points on every team in the week. Mutates and returns."""
    slots = week_data.league.roster_slots
    for team in week_data.teams:
        team.optimal_points, _ = optimal_lineup(team, slots)
    return week_data
