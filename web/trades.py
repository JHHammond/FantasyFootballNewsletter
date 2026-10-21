"""
Trades, kept (21 Oct 2026).

A trade used to live only as long as it took to render a paper. This turns
one platform's trade into the rows migration 045 stores, so that there is
eventually a market to read: what a player is worth, taken from who gets
traded for whom.

Nothing here values anything. That decision waits on knowing how many
trades a week the site actually sees, which is what the collection is for.

Two rules carried over from the rest of Around the Leagues:

  * A player is ONE player across Sleeper, ESPN and Yahoo. `player_key` is
    resolved through the same PPR matcher `lineup_players` uses, so a
    traded player joins to the scores he went on to put up. Unresolved, he
    keeps his platform id and simply can't be joined — `trade_volume()`
    reports that share, because it caps what any valuation can do.
  * Team names are the user's content. They are stored (staff read them,
    and a league's own paper may print them) but a public page may count
    these rows and never name them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _ms(created: Optional[int]) -> Optional[str]:
    """The platform's epoch milliseconds as a timestamp, or None. A bad
    value loses the timestamp, never the trade."""
    if not created:
        return None
    try:
        return datetime.fromtimestamp(int(created) / 1000, timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _player(p, ctx) -> dict[str, Any]:
    """One traded player, keyed and named the way lineup_players does it:
    Sleeper's id and Sleeper's spelling where the matcher resolved him, the
    platform's own where it didn't."""
    sid = ctx.resolve(p) if ctx else None
    known = ctx.name(sid) if (ctx and sid) else None
    name, pos, nfl = known or (getattr(p, "name", ""), getattr(p, "position", None),
                               getattr(p, "nfl_team", None))
    return {
        "player_key": sid or str(getattr(p, "player_id", "") or ""),
        "name": (name or "")[:80],
        "position": ((pos or "") or "")[:8] or None,
        "nfl_team": nfl or None,
    }


def rows(league: dict, moves, season: int, week: int, ctx=None
         ) -> tuple[list[dict], list[dict]]:
    """(trade headers, player rows) for the completed trades in `moves`.

    `moves` is what providers.load_transactions returns: every kind of
    roster move, already cut to this week's window. Only completed trades
    are kept — a vetoed or withdrawn trade never happened, and a waiver
    claim is not a price.
    """
    from .trade_picks import trade_key

    heads: list[dict] = []
    players: list[dict] = []
    seen: set = set()
    for m in moves or []:
        if not getattr(m, "is_trade", False) or not getattr(m, "succeeded", False):
            continue
        d = m.to_dict()
        key = trade_key(d)
        if key in seen:                      # the same trade twice in a feed
            continue
        seen.add(key)
        heads.append({
            "league_id": league["id"],
            "season": int(season),
            "week": int(week),
            "trade_key": key,
            "provider": league["provider"],
            "team_count": league.get("team_count"),
            "scoring_type": league.get("scoring_type"),
            "teams": list(d.get("teams") or []),
            "picks": [list(x) for x in (d.get("picks") or [])],
            "faab": [list(x) for x in (d.get("faab") or [])],
            "traded_at": _ms(getattr(m, "created", None)),
        })
        # Who gave him up, and who got him. A player can appear on both
        # sides of a three-way; the last word wins, and the primary key
        # keeps one row per player per trade either way.
        moved: dict[str, dict] = {}
        for team, p in (getattr(m, "drops", None) or []):
            row = _player(p, ctx)
            moved.setdefault(row["player_key"], row)["from_team"] = team
        for team, p in (getattr(m, "adds", None) or []):
            row = _player(p, ctx)
            moved.setdefault(row["player_key"], row)["to_team"] = team
        for key_, row in moved.items():
            if not key_:
                continue                     # a player with no id at all
            players.append({
                "league_id": league["id"], "season": int(season),
                "week": int(week), "trade_key": key,
                "player_key": key_, "name": row.get("name") or "",
                "position": row.get("position"), "nfl_team": row.get("nfl_team"),
                "from_team": row.get("from_team"), "to_team": row.get("to_team"),
            })
    return heads, players


def collect_league(db, league: dict, season: int, week: int, ctx=None) -> int:
    """Fetch and store one league's trades for one week. Returns how many.

    Records the scan either way: a league that made no trades and a league
    we never asked look identical in the trades table, and telling them
    apart is what makes both the skip logic and the volume numbers mean
    anything.
    """
    from providers import load_transactions

    moves = load_transactions(league["provider"], league["platform_league_id"],
                              season, week)
    heads, players = rows(league, moves, season, week, ctx)
    db.upsert_trades(heads, players)
    db.upsert_trade_scan({"league_id": league["id"], "season": int(season),
                          "week": int(week), "trades": len(heads)})
    return len(heads)
