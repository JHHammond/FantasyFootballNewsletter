"""
Bridge from the normalized model back to the legacy dict shape.

storylines.py, writer.py and especially newspaper.py (1,900 lines of it) all
consume the dicts that the old fetch_data.pair_matchups used to emit. Rewriting
those against the new model in one pass would be a big, risky diff for no
immediate user-visible gain.

So: the provider layer produces WeekData, this module flattens it into exactly
the shape the existing pipeline already expects, and everything downstream keeps
working untouched. New code should consume WeekData directly and let this file
shrink over time.

One deliberate behavior change is documented on `bottom_performer` below.
"""

from __future__ import annotations

from typing import Any, Optional

from .models import PlayerLine, Team, WeekData


def _raw_player_id(namespaced: str) -> str:
    """Strip the provider prefix: "sleeper:4046" -> "4046".

    newspaper.get_player_headshot_url() builds a Sleeper CDN URL straight from
    this field, so it has to stay the platform's own ID until that function is
    taught to use the provider-supplied headshot_url instead.
    """
    return namespaced.split(":", 1)[-1] if namespaced else ""


def _performer(player: Optional[PlayerLine]) -> Optional[dict[str, Any]]:
    if player is None or not player.player_id:
        return None
    return {
        "player_id": _raw_player_id(player.player_id),
        "name": player.name,
        "position": player.position,
        "actual": round(player.points, 2),
        "projected": player.projected,
        "beat_projection_by": player.vs_projection,
        # Extras the legacy pipeline ignores but new code can use.
        "slot": player.slot,
        "nfl_team": player.nfl_team,
        "headshot_url": player.headshot_url,
        "injury_status": player.injury_status,
    }


def team_to_legacy(team: Team) -> dict[str, Any]:
    """One normalized Team -> the dict shape the old pipeline expects."""
    starters_ids = [_raw_player_id(p.player_id) for p in team.lineup]
    all_ids = [_raw_player_id(p.player_id) for p in team.all_players if p.player_id]

    players_points = {
        _raw_player_id(p.player_id): p.points
        for p in team.all_players
        if p.player_id
    }

    return {
        "team_name": team.team_name,
        "owner_name": team.manager.display_name,
        "avatar_url": team.manager.avatar_url,
        "points": team.points,
        "record": team.record,
        "wins": team.wins,
        "losses": team.losses,
        "ties": team.ties,
        "empty_slots": team.empty_slots,
        "starters": starters_ids,
        "players": all_ids,
        "players_points": players_points,

        # Filled by the provider layer's optimizer rather than the old
        # greedy slot-order loop -- see providers/optimizer.py.
        "optimal_score": team.optimal_points if team.optimal_points is not None else team.points,
        "lineup_gap": team.lineup_gap,

        "top_performer": _performer(team.top_scorer),

        # BEHAVIOR CHANGE, on purpose: this used to be the starter with the
        # fewest raw points, which is nearly always a kicker or a defense doing
        # something boring. But writer.py's prompt tells Claude to roast whoever
        # "massively underperformed their projection (beat_projection_by < -8)"
        # -- a different player entirely. The columnist was being handed the
        # wrong guy every week. This is now the biggest projection miss.
        # To restore the old behavior, swap in team.low_scorer.
        "bottom_performer": _performer(team.biggest_bust),

        # Both are exposed so nothing is lost.
        "low_scorer": _performer(team.low_scorer),
        "biggest_boom": _performer(team.biggest_boom),
        "goose_eggs": [_performer(p) for p in team.goose_eggs],

        "all_starters": [
            _performer(p) for p in team.lineup if p.player_id
        ],
        "all_bench": [
            _performer(p) for p in team.bench if p.player_id
        ],

        # Escape hatch: new code can reach the real object without a re-fetch.
        "_team": team,
    }


def _record_after(team: Team, result: str) -> str:
    """The record INCLUDING the week being reported on.

    Team.record is deliberately the record *entering* the week, so that "upset"
    and "fraud" logic can compare standings as they stood at kickoff. Correct
    for that purpose, and wrong for every purpose a reader has: the Week 1
    paper printed a standings table reading 0-0 for all ten teams, under
    headlines describing the games that had just decided those records.

    A paper reports the week it covers. This is the number it reports.
    """
    wins, losses, ties = team.wins, team.losses, team.ties
    if result == "won":
        wins += 1
    elif result == "lost":
        losses += 1
    else:
        ties += 1
    base = f"{wins}-{losses}"
    return f"{base}-{ties}" if ties else base


def week_to_legacy_games(week_data: WeekData) -> list[dict[str, Any]]:
    """WeekData -> the `games` list that storylines/writer/newspaper consume."""
    games: list[dict[str, Any]] = []

    for matchup in week_data.matchups:
        winner = matchup.winner
        team_1 = team_to_legacy(matchup.team_1)
        team_2 = team_to_legacy(matchup.team_2)

        if matchup.is_tie or winner is None:
            r1 = r2 = "tied"
        elif winner.team_id == matchup.team_1.team_id:
            r1, r2 = "won", "lost"
        else:
            r1, r2 = "lost", "won"

        team_1["record_after"] = _record_after(matchup.team_1, r1)
        team_2["record_after"] = _record_after(matchup.team_2, r2)

        games.append({
            "team_1": team_1,
            "team_2": team_2,
            "winner": winner.team_name if winner else "Tie",
            "margin": matchup.margin,
            "matchup_id": matchup.matchup_id,
            "_matchup": matchup,
        })

    return games


def legacy_league_info(week_data: WeekData) -> dict[str, Any]:
    """The handful of league fields main.py and app.py pull off the raw league."""
    league = week_data.league
    return {
        "name": league.name,
        "roster_positions": league.roster_slots,
        "season": league.season,
        "total_rosters": league.team_count,
        "scoring_type": league.scoring_type,
        "avatar_url": league.avatar_url,
        "provider": league.provider,
    }
