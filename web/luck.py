"""
The Luck Index (John, 30 Sep: "if people could go in and measure how lucky
their team was").

Luck is measured in WINS: the wins a team has, minus the wins it earned.

  earned    each week, the share of its league it would have beaten playing
            everybody (all-play), with its starters' scores set to their own
            season averages in that league; summed over the weeks.
  schedule  actual wins - all-play wins on the real scores. Who you drew and
            when: their big week, your good week wasted.
  players   all-play wins on the real scores - all-play wins with starters at
            their averages. Your guys going off, or going missing.
  total     schedule + players = actual wins - earned.

The rest explains it and is never added in: points against compared with
each opponent's own average, close games, opponent benches that handed you a
win, and your own lineup losses (skill, not luck).

Production runs this as SQL (migration 031: luck_rows / luck_league /
luck_national); compute() below is the same arithmetic, for the demo
database, and the tests hold the two to each other.
"""

from __future__ import annotations

import time
from typing import Any, Optional

#: Total luck, in wins -> the word for it.
VERDICTS = [(1.5, "Blessed"), (0.5, "Lucky"), (-0.5, "Fair"), (-1.5, "Unlucky")]
CACHE_SECONDS = 600
NATIONAL_SECONDS = 3600
_cache: dict = {}


def verdict(total: Optional[float]) -> str:
    if total is None:
        return "–"
    for floor, word in VERDICTS:
        if total >= floor:
            return word
    return "Cursed"


def _f(v):
    return float(v) if v is not None else None


# ---------------------------------------------------------------------------
# The arithmetic (the SQL's twin)
# ---------------------------------------------------------------------------

def compute(team_rows: list[dict], lineup_rows: list[dict], season: int,
            league_id: Optional[str] = None) -> list[dict]:
    from web.league_stats import _r
    g = [dict(r, points=_f(r["points"]), opponent_points=_f(r.get("opponent_points")),
              optimal_points=_f(r.get("optimal_points")), bench_left=_f(r.get("bench_left")))
         for r in team_rows
         if int(r["season"]) == season and r["result"] in ("W", "L", "T")
         and (_f(r["points"]) or 0) > 0
         and (league_id is None or r["league_id"] == league_id)]
    lu = [l for l in lineup_rows if int(l["season"]) == season
          and (league_id is None or l["league_id"] == league_id)]

    lavg: dict = {}
    for r in g:
        lavg.setdefault(r["league_id"], []).append(r["points"])
    lavg = {k: sum(v) / len(v) for k, v in lavg.items()}

    pv: dict = {}
    for l in lu:
        if _f(l["points"]) and _f(l["points"]) > 0:
            pv.setdefault((l["league_id"], l["player_key"]), []).append(_f(l["points"]))
    pavg = {k: sum(v) / len(v) for k, v in pv.items()}
    dev: dict = {}
    for l in lu:
        if not l["started"]:
            continue
        k = (l["league_id"], l["player_key"])
        if k not in pavg:
            continue
        tk = (l["league_id"], l["team_id"], int(l["week"]))
        dev[tk] = dev.get(tk, 0.0) + (_f(l["points"]) - pavg[k])

    lw: dict = {}
    for r in g:
        r["dev"] = dev.get((r["league_id"], r["team_id"], int(r["week"])))
        r["adj"] = r["points"] - (r["dev"] or 0.0)
        lw.setdefault((r["league_id"], int(r["week"])), []).append(r)

    tavg: dict = {}
    for r in g:
        tavg.setdefault((r["league_id"], r["team_id"]), []).append(r["points"])
    tavg = {k: sum(v) / len(v) for k, v in tavg.items()}

    for (lid, wk), rows in lw.items():
        n = len(rows)
        for a in rows:
            if n > 1:
                others = [b for b in rows if b["team_id"] != a["team_id"]]
                a["ap_real"] = sum(1.0 if b["points"] < a["points"] else
                                   0.5 if b["points"] == a["points"] else 0.0
                                   for b in others) / (n - 1)
                # A millionth absorbs float noise; the SQL is exact (numeric).
                a["ap_adj"] = sum(1.0 if b["points"] < a["adj"] - 1e-6 else
                                  0.5 if abs(b["points"] - a["adj"]) <= 1e-6 else 0.0
                                  for b in others) / (n - 1)
            else:
                a["ap_real"] = a["ap_adj"] = None
            opps = sorted([o for o in rows if o["team_id"] != a["team_id"]
                           and o["points"] == a["opponent_points"]
                           and o["opponent_points"] == a["points"]],
                          key=lambda o: str(o["team_id"]))
            o = opps[0] if opps else None
            a["opp_avg"] = tavg.get((lid, o["team_id"])) if o else None
            a["opp_optimal"] = o["optimal_points"] if o else None
            a["is_close"] = (a["opponent_points"] is not None
                             and abs(a["points"] - a["opponent_points"]) < 0.05 * lavg[lid])

    teams: dict = {}
    latest: dict = {}
    for r in g:
        k = (r["league_id"], r["team_id"])
        if k not in latest or int(r["week"]) > int(latest[k]["week"]):
            latest[k] = r
        if r.get("ap_real") is None:
            continue
        teams.setdefault(k, []).append(r)

    out = []
    for (lid, tid), rows in teams.items():
        last = latest[(lid, tid)]
        wins = sum(1 if r["result"] == "W" else 0.5 if r["result"] == "T" else 0 for r in rows)
        apr = sum(r["ap_real"] for r in rows)
        apa = sum(r["ap_adj"] for r in rows)
        avg = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
        opp_l = [r["opp_avg"] - r["opponent_points"] for r in rows
                 if r["opp_avg"] is not None and r["opponent_points"] is not None]
        devs = [r["dev"] for r in rows if r["dev"] is not None]
        benches = [r["bench_left"] for r in rows if r["bench_left"] is not None]
        out.append({
            "league_id": lid, "team_id": tid, "team_name": last.get("team_name"),
            "manager": last.get("manager"), "provider": last.get("provider"),
            "team_count": last.get("team_count"), "scoring_type": last.get("scoring_type"),
            "games": len(rows),
            "w": sum(1 for r in rows if r["result"] == "W"),
            "l": sum(1 for r in rows if r["result"] == "L"),
            "t": sum(1 for r in rows if r["result"] == "T"),
            "wins": wins, "ap_real": _r(apr, 3), "ap_adj": _r(apa, 3),
            "schedule": _r(wins - apr, 3), "players": _r(apr - apa, 3),
            "total": _r(wins - apa, 3),
            "pf": _r(avg([r["points"] for r in rows])),
            "pa": _r(avg([r["opponent_points"] for r in rows
                          if r["opponent_points"] is not None])),
            "opp_luck": _r(avg(opp_l)), "player_dev": _r(avg(devs)),
            "close_w": sum(1 for r in rows if r["is_close"] and r["result"] == "W"),
            "close_l": sum(1 for r in rows if r["is_close"] and r["result"] == "L"),
            "gifts": sum(1 for r in rows if r["result"] == "W" and r["opp_optimal"] is not None
                         and r["opp_optimal"] > r["points"]),
            "own_losses": sum(1 for r in rows if r["result"] == "L"
                              and r["optimal_points"] is not None
                              and r["opponent_points"] is not None
                              and r["optimal_points"] > r["opponent_points"]),
            "bench_avg": _r(avg(benches)),
            "has_players": bool(devs),
            "last_week": max(int(r["week"]) for r in rows),
        })
    out.sort(key=lambda r: (-r["total"], str(r["team_id"])))
    return out


def national(rows: list[dict]) -> dict:
    from web.league_stats import _pct
    totals = sorted(r["total"] for r in rows)
    return {"teams": len(totals),
            "pct": [_pct(totals, g / 100) for g in range(101)] if totals else None}


# ---------------------------------------------------------------------------
# For the pages
# ---------------------------------------------------------------------------

def clear_cache() -> None:
    _cache.clear()


def _cached(key, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def share_below(pct: Optional[list], value: float) -> Optional[float]:
    """The share of the country with less luck than `value`."""
    if not pct:
        return None
    if value <= pct[0]:
        return 0.0
    if value >= pct[-1]:
        return 1.0
    for i in range(1, len(pct)):
        if value <= pct[i]:
            span = pct[i] - pct[i - 1]
            f = (value - pct[i - 1]) / span if span > 0 else 0.0
            return (i - 1 + f) / (len(pct) - 1)
    return 1.0


def _league_rows(db, season: int, league: dict) -> list[dict]:
    rows = db.luck_league(season, league["id"]) or []
    if rows:
        return rows
    # The same real league connected twice: its stats live under one row.
    for other in db.sibling_league_ids(league.get("provider"),
                                       league.get("platform_league_id"), season):
        if other != league["id"]:
            rows = db.luck_league(season, other) or []
            if rows:
                return rows
    return []


def league_page(db, season: int, league: dict) -> dict:
    rows = _cached(("league", season, league["id"]),
                   lambda: _league_rows(db, season, league))
    rows = [dict(r) for r in rows]
    # The whole country takes a few seconds and only changes on Tuesdays:
    # an hour's cache, and a page without it rather than no page.
    hit = _cache.get(("national", season))
    if hit and time.time() - hit[0] < NATIONAL_SECONDS:
        nat = hit[1]
    else:
        try:
            nat = db.luck_national(season) or {}
            _cache[("national", season)] = (time.time(), nat)
        except Exception as exc:  # noqa: BLE001
            print(f"[luck] national comparison unavailable: {exc}", flush=True)
            nat = {}
    pct = nat.get("pct")
    spread = max([abs(r["total"]) for r in rows] + [1.0])
    for r in rows:
        r["verdict"] = verdict(r["total"])
        below = share_below(pct, r["total"])
        r["luckier_than"] = round(below * 100) if below is not None else None
        r["bar"] = round(50 * abs(r["total"]) / spread, 1)       # % of half the track
        r["deserved_w"] = round(r["w"] + 0.5 * r["t"] - r["total"], 1)
        r["deserved_l"] = round(r["games"] - r["deserved_w"], 1)
    return {"teams": rows, "national": nat.get("teams") or 0,
            "has_players": any(r.get("has_players") for r in rows),
            "through": max((r["last_week"] for r in rows), default=None)}
