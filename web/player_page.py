"""One player's page in the trade market (John, 23 Oct 2026).

Everything the page shows about a player, gathered in one place so the
route stays a few lines and this can be tested without a server:

  * who he is, with his photo, and what he costs as a place in the market;
  * his price, day by day since the draft (the same line as the market chart);
  * his weekly usage, from Around the Leagues: how many teams started him,
    what he scored, and how often those teams won;
  * who he is traded for most;
  * every trade of him this season, newest first (migration 056);
  * for a paying viewer, the trades where one of HIS players was on the
    other side: "what my guys have fetched for him".

PRIVACY. Trades come from player_trades (056), which returns players, pick
labels and FAAB amounts, and a league only as its shape. The viewer's own
roster is read only for leagues on his account, and only its player keys
leave this module.

Never raises for a missing piece: a page with no usage, or no trades yet,
is still a page.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

#: Trades shown before "show more".
TRADES_FIRST = 25

PROVIDER = {"sleeper": "Sleeper", "espn": "ESPN", "yahoo": "Yahoo"}
SCORING = {"ppr": "PPR", "half_ppr": "half-PPR", "std": "standard"}


def league_shape(t: dict) -> str:
    """'12-team PPR · Sleeper', from what a trade knows about its league."""
    bits = []
    if t.get("team_count"):
        bits.append(f"{int(t['team_count'])}-team")
    if t.get("scoring_type") in SCORING:
        bits.append(SCORING[t["scoring_type"]])
    shape = " ".join(bits) or "A league"
    plat = PROVIDER.get((t.get("provider") or "").lower())
    return f"{shape} · {plat}" if plat else shape


def _side(players: list[dict], picks: list, faab) -> list[dict]:
    """One side of a deal as display items: players first, then picks, then
    FAAB. Each item is {kind, label, key?, position?}."""
    out = [{"kind": "player", "label": p.get("name") or p.get("player_key"),
            "key": p.get("player_key"), "position": p.get("position")}
           for p in players or []]
    out += [{"kind": "pick", "label": str(lbl)} for lbl in picks or [] if lbl]
    try:
        amt = float(faab or 0)
    except (TypeError, ValueError):
        amt = 0.0
    if amt > 0:
        out.append({"kind": "faab", "label": f"${amt:,.0f} FAAB"})
    return out


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def nice_day(day) -> Optional[str]:
    """'2026-10-22' -> 'Oct 22'. Anything else comes back as it was."""
    try:
        y, m, d = (int(x) for x in str(day)[:10].split("-"))
        return f"{MONTHS[m - 1]} {d}"
    except (TypeError, ValueError, IndexError):
        return day or None


def shape_trades(raw: list[dict]) -> list[dict]:
    """player_trades rows -> what the template draws."""
    out = []
    for t in raw or []:
        out.append({
            "key": t.get("key"),
            "day": nice_day(t.get("day")),
            "week": t.get("week"),
            "league": league_shape(t),
            "three_way": int(t.get("teams") or 2) > 2,
            "got": _side(t.get("with"), t.get("got_picks"), t.get("got_faab")),
            "gave": _side(t.get("gave"), t.get("gave_picks"), t.get("gave_faab")),
            "gave_keys": [p.get("player_key") for p in t.get("gave") or []],
        })
    return out


def traded_for(trades: list[dict], limit: int = 10) -> list[dict]:
    """The players he went for most often: everyone on the other side,
    counted once per deal."""
    tally: dict[str, dict] = {}
    for t in trades:
        for item in t["gave"]:
            if item["kind"] != "player" or not item.get("key"):
                continue
            e = tally.setdefault(item["key"], {"key": item["key"], "name": item["label"],
                                               "position": item.get("position"), "n": 0})
            e["n"] += 1
    return sorted(tally.values(), key=lambda e: (-e["n"], e["name"] or ""))[:limit]


def usage(week_json: Callable[[int], dict], key: str, weeks: list[int]) -> list[dict]:
    """His line in Around the Leagues for each week: teams that had him, how
    many started him, his PPR score and those teams' win rate. A week he
    isn't in (a bye, or outside the 400 most rostered) is left out."""
    out = []
    for wk in weeks:
        try:
            players = ((week_json(wk) or {}).get("players") or {}).get("all") or []
        except Exception:  # noqa: BLE001
            continue
        row = next((p for p in players if p.get("player_key") == key), None)
        if not row:
            continue
        out.append({"week": wk, "rostered": row.get("rostered") or 0,
                    "started": row.get("started") or 0, "start_pct": row.get("start_pct"),
                    "ppr": row.get("ppr"), "win_pct": row.get("win_pct"),
                    "did_play": row.get("did_play")})
    return out


def your_trades(trades: list[dict], roster: set[str]) -> list[dict]:
    """Trades of him where one of the viewer's players went the other way,
    each marked with which of them."""
    out = []
    for t in trades:
        hit = [k for k in t["gave_keys"] if k in roster]
        if hit:
            out.append(dict(t, yours=set(hit)))
    return out


def summary(key: str, rating: Optional[dict], names: dict) -> dict:
    """Who he is and what he costs, from the market's cached build."""
    pick = next((p for p in (rating or {}).get("pickable") or [] if p["k"] == key), None)
    meta = names.get(key) or {}
    s = {"key": key, "name": (pick or {}).get("name") or meta.get("name") or key,
         "position": (pick or {}).get("pos") or meta.get("position"),
         "team": (pick or {}).get("team") or meta.get("nfl_team"),
         "photo": (pick or {}).get("photo"), "priced": pick is not None}
    if pick:
        s.update(costs=pick["costs"], rank=pick["rank"], drafted=pick.get("drafted"),
                 trades=pick.get("trades"), firm=pick.get("firm"),
                 moved=(pick["dp"] - pick["rank"]) if pick.get("dp") else None)
    return s


def chart(key: str, rating: Optional[dict]) -> Optional[dict]:
    """His daily line, on the market chart's scale."""
    daily = (rating or {}).get("daily") or {}
    line = (daily.get("lines") or {}).get(key)
    if not line or not daily.get("base"):
        return None
    market = [pt for pt in line if pt[0] > 0] or line
    best = min(market, key=lambda pt: pt[1])
    worst = max(market, key=lambda pt: pt[1])
    return {"base": daily["base"], "last": daily.get("last") or line[-1][0],
            "line": line, "best": best, "worst": worst}
