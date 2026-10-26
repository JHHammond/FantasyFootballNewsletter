"""The live trade feed's page logic (John, 24 Oct 2026): every trade as it
comes in, newest first, filterable. See migration 060 for the data and
web/live_trades.py for how it arrives every half hour.

Free: the ten newest, filterable by position only. Paid: the full feed and
every filter, including "trades with my players in them"."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from . import player_page as pp

FREE_ROWS = 10
PAID_ROWS = 120
#: A trade is "lopsided" when the cheaper side is worth less than this
#: share of the dearer one by today's prices (the market ignores under 25%).
LOPSIDED_SHARE = 0.75

FILTERS = ("pos", "player", "shape", "size", "scoring", "platform", "fmt", "lopsided", "mine")


def _ago(at: Optional[str], now: datetime) -> str:
    try:
        t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    s = max(0, int((now - t).total_seconds()))
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} hr ago"
    d = s // 86400
    return "yesterday" if d == 1 else f"{d} days ago"


def shape(raw: list[dict], prices: dict, spot: float, photo=None,
          now: Optional[datetime] = None, dynasty: Optional[tuple] = None) -> list[dict]:
    """Feed rows the template draws: each side's players with photo and
    today's price, plus a verdict when the deal can be weighed.

    `prices`/`spot` are the redraft market; `dynasty` is (prices, spot) for
    the dynasty one (062). A dynasty league's trade is priced and judged in
    its own market, never the redraft one."""
    now = now or datetime.now(timezone.utc)
    out = []
    redraft = (prices, spot)
    for t in raw or []:
        prices, spot = dynasty if (dynasty and t.get("format") == "dynasty") else redraft
        sides = []
        for s in t.get("sides") or []:
            players = []
            for p in s.get("players") or []:
                pr = prices.get(p.get("player_key")) or {}
                players.append({"kind": "player", "key": p.get("player_key"), "label": p.get("name"),
                                "position": p.get("position") or pr.get("pos"),
                                "costs": pr.get("costs"),
                                "photo": pr.get("photo") or (photo(p.get("player_key"), p.get("position")) if photo else None)})
            extras = [{"kind": "pick", "label": str(x)} for x in s.get("picks") or [] if x]
            faab = float(s.get("faab") or 0)
            if faab > 0:
                extras.append({"kind": "faab", "label": f"${faab:,.0f} FAAB"})
            sides.append({"items": players + extras, "players": players, "extras": bool(extras)})
        sides = [s for s in sides if s["items"]]
        if len(sides) < 2:
            continue
        keys = [p["key"] for s in sides for p in s["players"]]
        poss = {p.get("position") for s in sides for p in s["players"]}
        n_players = [len(s["players"]) for s in sides]
        picks = any(s["extras"] for s in sides)
        verdict = None
        if len(sides) == 2 and not picks and all(k in prices for k in keys):
            vals = [sum(prices[p["key"]]["v"] for p in s["players"]) - spot * (len(s["players"]) - 1)
                    for s in sides]
            if min(vals) > 0:
                hi = max(vals)
                share = min(vals) / hi
                verdict = {"winner": vals.index(hi), "share": round(100 * share),
                           "edge": round(100 * (hi / min(vals) - 1)),
                           "fair": share >= 0.9, "lopsided": share < LOPSIDED_SHARE}
        out.append({
            "key": t.get("key"), "at": t.get("at"), "ago": _ago(t.get("at"), now),
            "league": pp.league_shape(t), "provider": (t.get("provider") or "").lower(),
            "team_count": t.get("team_count"), "scoring": t.get("scoring_type"),
            "fmt": "dynasty" if (t.get("format") == "dynasty") else "redraft",
            "sides": sides, "keys": keys, "positions": poss, "picks": picks,
            "straight": len(sides) == 2 and n_players == [1, 1] and not picks,
            "three_way": len(sides) > 2, "verdict": verdict,
        })
    return out


def from_player_trades(key: str, trades: list[dict], prices: dict, names: dict) -> list[dict]:
    """One player's whole season (player_trades, migration 056) in the feed's
    raw shape, for the player filter: filtering the newest few hundred
    trades of everybody missed most of his (John, 25 Oct). His side is
    first; the other side is everything that went the other way."""
    me = prices.get(key) or {}
    meta = names.get(key) or {}
    him = {"player_key": key, "name": me.get("name") or meta.get("name") or key,
           "position": me.get("pos") or meta.get("position")}
    out = []
    for t in trades or []:
        out.append({
            "key": t.get("key"), "week": t.get("week"),
            "at": f"{t['day']}T12:00:00+00:00" if t.get("day") else None,
            "provider": t.get("provider"), "team_count": t.get("team_count"),
            "scoring_type": t.get("scoring_type"), "format": None,
            "sides": [{"players": [him] + list(t.get("with") or []),
                       "picks": t.get("got_picks") or [], "faab": t.get("got_faab") or 0},
                      {"players": list(t.get("gave") or []),
                       "picks": t.get("gave_picks") or [], "faab": t.get("gave_faab") or 0}],
        })
    return out


def clean_filters(q: dict, paid: bool) -> dict:
    f = {k: (q.get(k) or "").strip()[:40] for k in FILTERS}
    if f["pos"] not in ("QB", "RB", "WR", "TE"):
        f["pos"] = ""
    if f["shape"] not in ("straight", "package", "picks"):
        f["shape"] = ""
    if f["size"] not in ("8", "10", "12", "14"):
        f["size"] = ""
    if f["scoring"] not in ("ppr", "half_ppr", "std"):
        f["scoring"] = ""
    if f["platform"] not in ("sleeper", "espn", "yahoo"):
        f["platform"] = ""
    if f["fmt"] not in ("redraft", "dynasty"):
        f["fmt"] = ""
    f["lopsided"] = "1" if f["lopsided"] == "1" else ""
    f["mine"] = "1" if f["mine"] == "1" else ""
    if not paid:                       # free: position only
        f = {k: (f[k] if k == "pos" else "") for k in FILTERS}
    return f


def apply(rows: list[dict], f: dict, mine: Optional[set] = None) -> list[dict]:
    def ok(r):
        if f["pos"] and f["pos"] not in r["positions"]:
            return False
        if f["player"] and f["player"] not in r["keys"]:
            return False
        if f["shape"] == "straight" and not r["straight"]:
            return False
        if f["shape"] == "package" and (r["straight"] or r["picks"]):
            return False
        if f["shape"] == "picks" and not r["picks"]:
            return False
        if f["size"]:
            n = int(r["team_count"] or 0)
            want = int(f["size"])
            if (want == 14 and n < 14) or (want != 14 and n != want):
                return False
        if f["scoring"] and r["scoring"] != f["scoring"]:
            return False
        if f["platform"] and r["provider"] != f["platform"]:
            return False
        if f["fmt"] and r["fmt"] != f["fmt"]:
            return False
        if f["lopsided"] and not (r["verdict"] and r["verdict"]["lopsided"]):
            return False
        if f["mine"] and not (mine and mine.intersection(r["keys"])):
            return False
        return True
    return [r for r in rows if ok(r)]
