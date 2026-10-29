"""
The waiver wire, kept (migration 067, 29 Oct 2026).

The collector has fetched every league's transactions feed since 045 and
kept only the trades. This keeps the rest: every claim that went through,
every claim that lost, what was dropped to make room, and the FAAB bid. It
costs no extra platform call, because the feed is already in hand for the
trades; `collect_league` here fetches it ONCE and stores whichever of the
two the league still needs.

What it is for: the Undefeated Watch ("do unbeaten teams work the wire
harder?"), and the most-added players for the socials.

Same two rules as trades.py:

  * A player is ONE player across Sleeper, ESPN and Yahoo, resolved through
    the PPR matcher, so a pickup joins to the points he went on to score.
  * Team names are the user's content. Stored for joins and for staff; a
    public page counts these rows and never names them.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

#: The kinds kept. Trades live in their own tables; a commissioner's
#: housekeeping is not anybody working the wire.
KINDS = ("waiver", "free_agent")


def move_key(d: dict) -> str:
    """A stable short id for one move. The platforms don't give one we can
    rely on across Sleeper, ESPN and Yahoo, and the same move has to get the
    same key on every collection so a re-run changes nothing."""
    adds = sorted((str(t), str((p or {}).get("player_id") or (p or {}).get("name") or ""))
                  for t, p in (d.get("adds") or []))
    drops = sorted((str(t), str((p or {}).get("player_id") or (p or {}).get("name") or ""))
                   for t, p in (d.get("drops") or []))
    blob = json.dumps([d.get("kind"), d.get("status"), adds, drops, d.get("bid"),
                       d.get("created"), d.get("week")], sort_keys=True, default=str)
    return "w" + hashlib.sha1(blob.encode()).hexdigest()[:12]


def rows(league: dict, moves, season: int, week: int, ctx=None) -> list[dict]:
    """The rows migration 067 stores for the waiver and free-agent moves in
    `moves` (what providers.load_transactions returns, already cut to this
    week's window). Failed claims are kept: somebody wanted him."""
    from .trades import _ms, _player

    out: list[dict] = []
    seen: set = set()
    for m in moves or []:
        kind = getattr(m, "kind", None)
        if kind not in KINDS:
            continue
        status = "complete" if getattr(m, "succeeded", False) else "failed"
        d = m.to_dict()
        key = move_key(d)
        when = _ms(getattr(m, "created", None))
        bid = getattr(m, "bid", None)
        try:
            bid = int(bid) if bid is not None and int(bid) >= 0 else None
        except (TypeError, ValueError):
            bid = None
        for action, pairs in (("add", getattr(m, "adds", None) or []),
                              ("drop", getattr(m, "drops", None) or [])):
            for team, p in pairs:
                row = _player(p, ctx)
                if not row["player_key"]:
                    continue                     # a player with no id at all
                ident = (key, action, row["player_key"])
                if ident in seen:                # the same move twice in a feed
                    continue
                seen.add(ident)
                out.append({
                    "league_id": league["id"], "season": int(season),
                    "week": int(week), "move_key": key, "kind": kind,
                    "status": status, "action": action,
                    "player_key": row["player_key"], "name": row["name"],
                    "position": row["position"], "nfl_team": row["nfl_team"],
                    "team_name": (str(team) if team is not None else None),
                    # A bid belongs to the claim, so it rides on the add.
                    "bid": bid if action == "add" else None,
                    "moved_at": when,
                })
    return out


def collect_league(db, league: dict, season: int, week: int, ctx=None, *,
                   trades: bool = True, waivers: bool = True) -> tuple[int, int]:
    """Fetch one league's moves for one week ONCE, and store the trades
    and/or the waiver moves it still needs. Returns (trades, waiver moves).

    Records each scan either way: a league that made no moves and a league
    we never asked look identical in the data tables, and telling them apart
    is what makes the skip logic and the backfill work."""
    from providers import load_transactions

    moves = load_transactions(league["provider"], league["platform_league_id"],
                              season, week)
    n_trades = n_moves = 0
    if trades:
        from . import trades as trades_mod
        heads, players = trades_mod.rows(league, moves, season, week, ctx)
        db.upsert_trades(heads, players)
        db.upsert_trade_scan({"league_id": league["id"], "season": int(season),
                              "week": int(week), "trades": len(heads)})
        n_trades = len(heads)
    if waivers:
        got = rows(league, moves, season, week, ctx)
        db.upsert_waivers(got)
        n_moves = len({r["move_key"] for r in got})
        db.upsert_waiver_scan({"league_id": league["id"], "season": int(season),
                               "week": int(week), "moves": n_moves})
    return n_trades, n_moves


def summary(volume: Optional[dict], through: int) -> dict[str, Any]:
    """How much of the wire we hold for weeks 1..through, for the staff note
    and for deciding whether the page may say anything about it yet."""
    weeks = [w for w in (volume or {}).get("weeks") or []
             if 1 <= int(w.get("week") or 0) <= int(through or 0)]
    have = sorted(int(w["week"]) for w in weeks if int(w.get("leagues") or 0) > 0)
    missing = [w for w in range(1, int(through or 0) + 1) if w not in have]
    return {"weeks": weeks, "have": have, "missing": missing,
            "adds": sum(int(w.get("adds") or 0) for w in weeks),
            "complete": bool(have) and not missing}
