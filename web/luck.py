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


_CARD_KEYS = ("league_id", "team_id", "team_name", "manager", "provider", "team_count",
              "scoring_type", "w", "l", "t", "total", "schedule", "players")


def national(rows: list[dict]) -> dict:
    import math
    from web.league_stats import _pct
    totals = sorted(r["total"] for r in rows)
    hist: dict = {}
    for v in totals:
        lo = max(-4.0, min(3.5, math.floor(v * 2) / 2.0))
        hist[lo] = hist.get(lo, 0) + 1
    card = lambda r: {k: r.get(k) for k in _CARD_KEYS}  # noqa: E731
    tk = lambda r: (str(r["league_id"]), str(r["team_id"]))  # noqa: E731
    return {"teams": len(totals),
            "pct": [_pct(totals, g / 100) for g in range(101)] if totals else None,
            "blessed": sum(1 for v in totals if v >= 1.5),
            "cursed": sum(1 for v in totals if v <= -1.5),
            "hist": [{"lo": k, "n": hist[k]} for k in sorted(hist)],
            "luckiest": [card(r) for r in sorted(rows, key=lambda r: (-r["total"], tk(r)))[:3]],
            "unluckiest": [card(r) for r in sorted(rows, key=lambda r: (r["total"], tk(r)))[:3]]}


def compute_team(team_rows: list[dict], lineup_rows: list[dict], season: int,
                 league_id: str, team_id: str, names: Optional[dict] = None) -> dict:
    """luck_team's twin: one team's weeks and starters."""
    from web.league_stats import _r
    g = [dict(r, points=_f(r["points"]), opponent_points=_f(r.get("opponent_points")))
         for r in team_rows
         if int(r["season"]) == season and r["league_id"] == league_id
         and r["result"] in ("W", "L", "T") and (_f(r["points"]) or 0) > 0]
    tavg: dict = {}
    for r in g:
        tavg.setdefault(r["team_id"], []).append(r["points"])
    tavg = {k: sum(v) / len(v) for k, v in tavg.items()}
    lu = [l for l in lineup_rows if int(l["season"]) == season and l["league_id"] == league_id]
    pv: dict = {}
    for l in lu:
        if _f(l["points"]) and _f(l["points"]) > 0:
            pv.setdefault(l["player_key"], []).append(_f(l["points"]))
    pavg = {k: sum(v) / len(v) for k, v in pv.items()}
    dev: dict = {}
    for l in lu:
        if l["started"] and l["player_key"] in pavg:
            k = (l["team_id"], int(l["week"]))
            dev[k] = dev.get(k, 0.0) + (_f(l["points"]) - pavg[l["player_key"]])
    by_week: dict = {}
    for r in g:
        r["dev"] = dev.get((r["team_id"], int(r["week"])))
        r["adj"] = r["points"] - (r["dev"] or 0.0)
        by_week.setdefault(int(r["week"]), []).append(r)
    weeks = []
    for wk in sorted(by_week):
        rows = by_week[wk]
        mine = next((r for r in rows if r["team_id"] == team_id), None)
        if mine is None:
            continue
        n = len(rows)
        others = [b for b in rows if b["team_id"] != team_id]
        ap_real = ap_adj = None
        if n > 1:
            ap_real = sum(1.0 if b["points"] < mine["points"] else
                          0.5 if b["points"] == mine["points"] else 0.0 for b in others) / (n - 1)
            ap_adj = sum(1.0 if b["points"] < mine["adj"] - 1e-6 else
                         0.5 if abs(b["points"] - mine["adj"]) <= 1e-6 else 0.0
                         for b in others) / (n - 1)
        opps = sorted([o for o in others if o["points"] == mine["opponent_points"]
                       and o["opponent_points"] == mine["points"]], key=lambda o: str(o["team_id"]))
        o = opps[0] if opps else None
        weeks.append({
            "week": wk, "points": mine["points"], "opponent_points": mine["opponent_points"],
            "result": mine["result"],
            "place": 1 + sum(1 for b in rows if b["points"] > mine["points"]), "n": n,
            "opp_id": o["team_id"] if o else None,
            "opp_name": (o.get("team_name") if o else None) or mine.get("opponent_name"),
            "opp_avg": _r(tavg.get(o["team_id"])) if o else None,
            "ap_real": _r(ap_real, 3), "ap_adj": _r(ap_adj, 3),
            "dev": _r(mine["dev"]),
        })
    pl: dict = {}
    for l in lu:
        if l["team_id"] == team_id and l["started"] and l["player_key"] in pavg:
            pl.setdefault(l["player_key"], []).append(_f(l["points"]))
    players = []
    for k, pts in pl.items():
        devs = [p - pavg[k] for p in pts]
        meta = (names or {}).get(k) or {}
        players.append({"player_key": k, "name": meta.get("name") or k,
                        "position": meta.get("position"), "starts": len(pts),
                        "dev_total": _r(sum(devs)), "dev_avg": _r(sum(devs) / len(devs)),
                        "worst": _r(min(pts)), "best": _r(max(pts))})
    players.sort(key=lambda p: (p["dev_total"], p["player_key"]))
    scores = [{"team_id": r["team_id"], "week": int(r["week"]), "points": r["points"]}
              for r in sorted(g, key=lambda r: (int(r["week"]), str(r["team_id"])))]
    mine_lu = []
    for l in sorted((l for l in lu if l["team_id"] == team_id),
                    key=lambda l: (int(l["week"]), str(l["player_key"]))):
        meta = (names or {}).get(l["player_key"]) or {}
        mine_lu.append({"week": int(l["week"]), "player_key": l["player_key"],
                        "name": meta.get("name") or l["player_key"],
                        "position": meta.get("position"), "slot": l.get("slot"),
                        "started": bool(l["started"]), "points": _f(l["points"])})
    return {"weeks": weeks, "players": players, "scores": scores, "lineups": mine_lu}


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


def _league_rows(db, season: int, league: dict) -> tuple[list[dict], str]:
    """(rows, the league id the stats are stored under)."""
    rows = db.luck_league(season, league["id"]) or []
    if rows:
        return rows, league["id"]
    # The same real league connected twice: its stats live under one row.
    for other in db.sibling_league_ids(league.get("provider"),
                                       league.get("platform_league_id"), season):
        if other != league["id"]:
            rows = db.luck_league(season, other) or []
            if rows:
                return rows, other
    return [], league["id"]


def team_report(db, season: int, stats_league_id: str, team: dict,
                league_rows: list[dict]) -> dict:
    detail = _cached(("team", season, stats_league_id, str(team["team_id"])),
                     lambda: db.luck_team(season, stats_league_id, str(team["team_id"])))
    return report(team, detail or {}, league_rows)


def league_strip(rows: list[dict], lanes: int = 3, gap: float = 7.0) -> dict:
    """Every team in the league on one line of luck, for the verdict card:
    zero in the middle, unlucky to the left. Dots that would overlap move up
    a lane. x is a percentage of the width."""
    if not rows:
        return {"dots": [], "spread": 1.0}
    spread = max([abs(float(r["total"])) for r in rows] + [1.0])
    dots = sorted(({"team_id": str(r["team_id"]), "name": r.get("team_name") or "",
                    "total": float(r["total"]),
                    "x": round(50 + 46 * float(r["total"]) / spread, 1)} for r in rows),
                  key=lambda d: (d["x"], d["team_id"]))
    last = [-100.0] * lanes
    for d in dots:
        lane = next((i for i in range(lanes) if d["x"] - last[i] >= gap),
                    min(range(lanes), key=lambda i: last[i]))
        d["lane"] = lane
        last[lane] = d["x"]
    return {"dots": dots, "spread": spread,
            "luckiest": dots[-1], "unluckiest": dots[0]}


def league_page(db, season: int, league: dict) -> dict:
    rows, stats_id = _cached(("league", season, league["id"]),
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
    for r in rows:
        from web.luck_card import rank_line
        r["rank_line"] = rank_line(r.get("luckier_than"), nat.get("teams") or 0)
        r["league_place"] = 1 + sum(1 for o in rows if o["total"] > r["total"])
    return {"teams": rows, "national": nat.get("teams") or 0, "nat": nat,
            "strip": league_strip(rows),
            "stats_league_id": stats_id,
            "has_players": any(r.get("has_players") for r in rows),
            "through": max((r["last_week"] for r in rows), default=None)}


# ---------------------------------------------------------------------------
# The Luck Report: one team's season as story cards (John, 1 Oct: "like a
# Spotify Wrapped thing"). Each card is one sentence with one big number.
# Cards that have nothing to say are left out rather than padded.
# ---------------------------------------------------------------------------

def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _pts(v) -> str:
    return f"{float(v):.1f}"


SLEEPER_HEADSHOT = "https://sleepercdn.com/content/nfl/players"
SLEEPER_TEAM_LOGO = "https://sleepercdn.com/images/team_logos/nfl"
ESPN_HEADSHOT = "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full"


def photo_url(player_key: Optional[str], position: Optional[str] = None) -> Optional[str]:
    """A picture for a lineup player_key. Most keys are Sleeper ids (the PPR
    matcher resolves ESPN and Yahoo players to them); defenses are team
    abbreviations; an unmatched ESPN player keeps "espn:<id>". Anything else
    has no picture, and the card goes without."""
    k = str(player_key or "").strip()
    if not k:
        return None
    if k.isdigit():
        return f"{SLEEPER_HEADSHOT}/{k}.jpg"
    if position == "DEF" or (k.isalpha() and k.isupper() and len(k) <= 3):
        return f"{SLEEPER_TEAM_LOGO}/{k.lower()}.png"
    provider, _, raw = k.partition(":")
    if provider == "espn" and raw.isdigit() and position != "DEF":
        return f"{ESPN_HEADSHOT}/{raw}.png&w=350&h=254"
    return None


def opponent_chart(detail: dict, wk: dict) -> Optional[dict]:
    """The opponent's season as bars, their average as a dashed rule, and the
    week they played you picked out: the bump (or the drop) you can see.
    Geometry is in a 320 x 180 box. None with fewer than two weeks to show."""
    opp = str(wk.get("opp_id") or "")
    rows = [r for r in (detail.get("scores") or []) if str(r.get("team_id")) == opp]
    if not opp or len(rows) < 2 or wk.get("opp_avg") is None:
        return None
    rows.sort(key=lambda r: int(r["week"]))
    avg = float(wk["opp_avg"])
    you = float(wk["points"]) if wk.get("points") is not None else None
    top = max([float(r["points"]) for r in rows] + [avg] + ([you] if you else [])) * 1.12
    base, height, left, width = 146.0, 112.0, 14.0, 292.0
    slot = width / len(rows)
    bw = min(34.0, slot * 0.62)

    def y(v: float) -> float:
        return round(base - height * v / top, 1)

    bars = []
    for i, r in enumerate(rows):
        pts = float(r["points"])
        x = left + slot * i + (slot - bw) / 2
        bars.append({"x": round(x, 1), "w": round(bw, 1), "y": y(pts),
                     "h": round(base - y(pts), 1), "cx": round(x + bw / 2, 1),
                     "week": int(r["week"]), "pts": _pts(pts),
                     "vs": int(r["week"]) == int(wk["week"])})
    hit = next((b for b in bars if b["vs"]), None)
    return {"bars": bars, "avg_y": y(avg), "avg": _pts(avg), "base": base,
            "you_y": y(you) if (you is not None and hit) else None,
            "you": _pts(you) if you is not None else None,
            "hit": hit}


UNSWAPPABLE = frozenset({"BN", "IR", "TAXI", "RES", "NA"})


def winning_swap(detail: dict) -> Optional[dict]:
    """The one bench-for-starter swap that would have turned a loss into a
    win (John, 1 Oct: "started Drake London and he got 5, Terry McLaurin on
    the bench got 15"). The bench player has to be able to play the starter's
    slot. Of all the losses one swap would have won, the closest loss; within
    it, the swap that gains the most. None when no single swap does it."""
    from providers.models import can_fill_slot
    roster: dict = {}
    for l in detail.get("lineups") or []:
        roster.setdefault(int(l["week"]), []).append(l)
    best = None
    for w in detail.get("weeks") or []:
        if w.get("result") != "L" or w.get("opponent_points") is None:
            continue
        rows = roster.get(int(w["week"])) or []
        starters = [r for r in rows if r.get("started") and r.get("slot") not in UNSWAPPABLE]
        bench = [r for r in rows if not r.get("started") and (r.get("slot") or "BN") == "BN"
                 and r.get("position")]
        margin = float(w["opponent_points"]) - float(w["points"])
        for b in bench:
            for st in starters:
                gain = float(b["points"] or 0) - float(st["points"] or 0)
                if gain <= margin or not can_fill_slot(b["position"], st["slot"]):
                    continue
                key = (margin, -gain, int(w["week"]), str(b["player_key"]), str(st["player_key"]))
                if best is None or key < best[0]:
                    best = (key, {"week": int(w["week"]), "margin": margin, "gain": gain,
                                  "opp_name": w.get("opp_name"),
                                  "out": st, "in": b, "slot": st["slot"]})
    return best[1] if best else None


def _swap_side(r: dict, role: str) -> dict:
    return {"role": role, "name": r.get("name") or r["player_key"],
            "position": r.get("position"), "pts": _pts(r.get("points") or 0),
            "photo": photo_url(r["player_key"], r.get("position"))}


def n_wins(n: float) -> str:
    """1.0 -> "1 win", 0.3 -> "0.3 wins", 2.0 -> "2 wins"."""
    n = round(float(n), 1)
    text = f"{n:.0f}" if n == int(n) else f"{n:.1f}"
    return f"{text} win" + ("" if n == 1 else "s")



# ---------------------------------------------------------------------------
# The Luck Report, as a show (27 Oct, John: "bland and static ... graphs and
# visuals when possible, like the Instagram videos"). Each card gets a colour
# world and, where the numbers allow, a chart. Geometry is worked out here so
# it can be tested; the template only draws it.
# ---------------------------------------------------------------------------

#: The verdict's colour world: gold for the blessed, ink for the cursed.
VERDICT_WORLD = {"Blessed": "gold", "Lucky": "green", "Fair": "paper",
                 "Unlucky": "ink", "Cursed": "ink"}    # no red cards (John, 27 Oct)


def cover(team: dict, league_name: str, through: Optional[int]) -> dict:
    """The front page. It asks the question and keeps the answer sealed for
    the verdict at the end, so there is a reason to keep swiping."""
    w, l = int(team.get("w") or 0), int(team.get("l") or 0)
    name = team.get("team_name") or "This team"
    if w > l:
        head, deck = (f"Is {name} good, or just lucky?",
                      "A winning record. We checked whether they earned it.")
    elif l > w:
        head, deck = (f"Was {name} robbed?",
                      "A losing record. We checked whether it was their fault.")
    else:
        head, deck = (f"What is {name}, really?",
                      "Dead even. We checked which way the luck ran.")
    weeks = f"{through} week{'s' if through != 1 else ''}" if through else "the season"
    return {"head": head, "deck": deck,
            "byline": f"A special investigation into {weeks} of {league_name}: every "
                      f"score, every opponent, every bench decision."}


def record_meter(team: dict) -> dict:
    """Wins you have and wins you earned, on one line from 0 to games played."""
    games = max(1.0, float(team.get("games") or (team["w"] + team["l"] + (team.get("t") or 0))))
    have = float(team["w"]) + 0.5 * float(team.get("t") or 0)
    earned = max(0.0, min(games, float(team["deserved_w"])))
    return {"games": int(round(games)), "have": round(have, 1), "earned": round(earned, 1),
            "have_x": round(100 * have / games, 1), "earned_x": round(100 * earned / games, 1),
            "gap": round(have - earned, 1),
            "ticks": [{"n": i, "x": round(100 * i / games, 2)} for i in range(int(round(games)) + 1)]}


def season_chart(weeks: list[dict]) -> Optional[dict]:
    """Every week as a column: how much of the league your score would have
    beaten. A loss standing above the halfway line was a robbery; a win
    below it was a gift. 320 x 200 box."""
    rows = sorted((w for w in weeks if w.get("ap_real") is not None), key=lambda w: int(w["week"]))
    if len(rows) < 2:
        return None
    base, top, left, width = 168.0, 22.0, 10.0, 300.0
    slot = width / len(rows)
    bw = min(30.0, slot * 0.6)
    cols, robbed, gifts = [], 0, 0
    for i, w in enumerate(rows):
        ap = max(0.0, min(1.0, float(w["ap_real"])))
        h = max(3.0, (base - top) * ap)
        tag = None
        if w["result"] == "L" and ap >= 0.5:
            tag, robbed = "robbed", robbed + 1
        elif w["result"] == "W" and ap < 0.5:
            tag, gifts = "gift", gifts + 1
        x = left + slot * i + (slot - bw) / 2
        place = int(w["place"]) if w.get("place") is not None else None
        cols.append({"x": round(x, 1), "w": round(bw, 1), "y": round(base - h, 1),
                     "h": round(h, 1), "cx": round(x + bw / 2, 1), "week": int(w["week"]),
                     "pct": round(100 * ap), "result": w["result"], "tag": tag,
                     "place": _ordinal(place) if place else f"{round(100 * ap)}%"})
    return {"cols": cols, "base": base, "top": top, "mid_y": round(base - (base - top) / 2, 1),
            "robbed": robbed, "gifts": gifts}


def week_board(detail: dict, wk: dict, team_id: str, names: dict, cap: int = 14) -> Optional[dict]:
    """The whole league's scores the week of an unluckiest loss or luckiest
    win, best to worst, with you and the team you played picked out."""
    week = int(wk["week"])
    rows = sorted(((float(r["points"]), str(r["team_id"])) for r in (detail.get("scores") or [])
                   if int(r["week"]) == week), key=lambda r: (-r[0], r[1]))
    if len(rows) < 3:
        return None
    top = rows[0][0] or 1.0
    opp = str(wk.get("opp_id") or "")
    out = []
    for i, (pts, tid) in enumerate(rows):
        out.append({"place": i + 1, "pts": _pts(pts), "w": round(100 * pts / top, 1),
                    "name": names.get(tid) or ("You" if tid == str(team_id) else "—"),
                    "me": tid == str(team_id), "opp": bool(opp) and tid == opp})
    if len(out) > cap:   # a very big league: the top of the table, plus you and them
        keep = out[:cap - 2] + [r for r in out[cap - 2:] if r["me"] or r["opp"]]
        out = keep[:cap]
    return {"rows": out, "n": len(rows)}


def player_chart(detail: dict, p: dict, mark: str) -> Optional[dict]:
    """One starter's weeks for you as bars, his own average as a dashed rule,
    the worst (bust) or best (boom) week picked out. 320 x 170 box."""
    key = str(p.get("player_key"))
    rows = sorted(((int(l["week"]), float(l.get("points") or 0)) for l in (detail.get("lineups") or [])
                   if str(l.get("player_key")) == key and l.get("started")), key=lambda r: r[0])
    starts = int(p.get("starts") or 0)
    if len(rows) < 2 or not starts:
        return None
    avg = (sum(v for _, v in rows) - float(p["dev_total"])) / starts if len(rows) == starts else \
        sum(v for _, v in rows) / len(rows) - float(p.get("dev_avg") or 0)
    top = max([v for _, v in rows] + [avg, 1.0]) * 1.15
    base, height, left, width = 140.0, 112.0, 12.0, 296.0
    slot = width / len(rows)
    bw = min(32.0, slot * 0.62)
    pick = (min(rows, key=lambda r: (r[1], r[0])) if mark == "low"
            else max(rows, key=lambda r: (r[1], -r[0])))

    def y(v):
        return round(base - height * max(0.0, v) / top, 1)

    bars = []
    for i, (wk, v) in enumerate(rows):
        x = left + slot * i + (slot - bw) / 2
        bars.append({"x": round(x, 1), "w": round(bw, 1), "y": y(v), "h": round(base - y(v), 1),
                     "cx": round(x + bw / 2, 1), "week": wk, "pts": _pts(v),
                     "hit": (wk, v) == pick, "above": v >= avg})
    return {"bars": bars, "avg_y": y(avg), "avg": _pts(avg), "base": base}


def coins(detail: dict, weeks: list[dict]) -> list[dict]:
    """The close games, one coin each, in week order. Close means decided by
    under 5% of the league's average score, as in luck_rows."""
    pts = [float(r["points"]) for r in (detail.get("scores") or []) if r.get("points")]
    if not pts:
        return []
    line = 0.05 * (sum(pts) / len(pts))
    out = []
    for w in sorted(weeks, key=lambda w: int(w["week"])):
        if w.get("opponent_points") is None or w["result"] not in ("W", "L"):
            continue
        margin = abs(float(w["points"]) - float(w["opponent_points"]))
        if margin < line:
            out.append({"week": int(w["week"]), "result": w["result"], "margin": _pts(margin),
                        "opp": w.get("opp_name") or ""})
    return out


def national_marker(nat: dict, total: float) -> Optional[dict]:
    """The country's histogram, small, with your bin lit. 16 half-win bins
    from -4 to +4."""
    bins = nat.get("hist") or []
    if not bins:
        return None
    top = max(int(b["n"]) for b in bins) or 1
    mine = max(-4.0, min(3.5, (total * 2 // 1) / 2.0))
    cols = []
    for b in bins:
        lo = float(b["lo"])
        i = int(round((lo + 4.0) * 2))
        cols.append({"x": round(4 + i * 19.5, 1), "h": round(96 * int(b["n"]) / top, 1),
                     "up": lo >= 0, "me": abs(lo - mine) < 1e-6})
    you_x = round(4 + (max(-4.0, min(4.0, total)) + 4.0) * 2 * 19.5, 1)
    return {"cols": cols, "you_x": you_x}


def _plural(n, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


#: The report's chapters, in story order (John, 27 Oct: "it needs to tell a
#: story. rn it is disjointed cards"). Each chapter keeps one colour, paper or
#: ink (John: no red cards); red and green are only ever accents.
CHAPTERS = [("record", "The record", "paper"), ("schedule", "The schedule", "ink"),
            ("players", "The players", "paper"), ("fault", "Not luck", "ink")]
CHAPTER_OF = {"record": "record", "against": "schedule", "season": "schedule", "week": "schedule",
              "opponent": "schedule", "close": "schedule", "lineup": "players", "player": "players",
              "swap": "fault", "skill": "fault"}
#: Story order inside the schedule chapter (John, 27 Oct: "maybe the order of
#: cards needs to be changed"): who you drew, the one who went off, what it
#: did to the record week by week, the worst and best of those weeks, then
#: the coin flips.
KIND_ORDER = {"record": 0, "against": 1, "opponent": 2, "season": 3, "week": 4, "close": 5,
              "lineup": 6, "player": 7, "swap": 8, "skill": 8}


def storyline(cards: list[dict], games: int) -> list[dict]:
    """Put the cards in story order, chapter by chapter, and give each one
    the line that leads into it from the card before, its chapter's colour,
    and "Part 2 · The schedule". The last card of the schedule and players
    chapters closes that chapter: the case file at the foot of the report
    fills in its number there."""
    order = {c: i for i, (c, _, _) in enumerate(CHAPTERS)}
    cards = sorted(cards, key=lambda c: (order[CHAPTER_OF[c["kind"]]], KIND_ORDER[c["kind"]],
                                         0 if c.get("tone") == "down" else 1))
    present = [c for c, _, _ in CHAPTERS if any(CHAPTER_OF[x["kind"]] == c for x in cards)]
    names = {c: (n, w) for c, n, w in CHAPTERS}
    prev = None
    for i, c in enumerate(cards):
        ch = CHAPTER_OF[c["kind"]]
        c["chapter"] = ch
        c["part"] = f"Part {present.index(ch) + 1} · {names[ch][0]}"
        c["world"] = names[ch][1]
        k, pk = c["kind"], (prev or {}).get("kind")
        hot_week = next((x.get("week") for x in cards if x["kind"] == "opponent" and x.get("tone") == "down"), None)
        if k == "record":
            lead = f"Through {_plural(games, 'week')}:"
        elif k == "against":
            lead = "First, the schedule."
        elif k == "opponent" and c.get("tone") == "down":
            lead = "Some weeks, the other team went off."
        elif k == "opponent":
            lead = ("And some weeks, they didn't show up." if pk == "opponent"
                    else "Some weeks, the other team didn't show up.")
        elif k == "season":
            lead = "How did your score rank each week?"
        elif k == "week" and c.get("tone") == "down":
            lead = (f"Week {c['week']} hurt the most." if c.get("week") == hot_week
                    else f"Week {c['week']} is where it hurt.")
        elif k == "week":
            lead = "But it cut both ways." if pk == "week" else f"Week {c['week']} went your way."
        elif k == "close":
            lead = "Then the coin flips."
        elif k == "lineup":
            lead = "Now, the players."
        elif k == "player" and c.get("tone") == "up" and pk == "player":
            lead = "Not everybody let you down."
        elif k == "player":
            lead = "The biggest no-show:" if pk == "lineup" else "Now, the players."
        else:
            lead = "One thing we can't blame on luck."
        c["lead"] = lead
        nxt = cards[i + 1] if i + 1 < len(cards) else None
        c["closes"] = ch if ch in ("schedule", "players") and (nxt is None or CHAPTER_OF[nxt["kind"]] != ch) else None
        prev = c
    return cards


def points_against(team: dict, league_rows: list[dict]) -> Optional[dict]:
    """Every team's average points against, most first, you picked out."""
    rows = [r for r in league_rows or [] if r.get("pa") is not None]
    if len(rows) < 4 or team.get("pa") is None:
        return None
    rows = sorted(rows, key=lambda r: (-float(r["pa"]), str(r.get("team_name") or "")))
    top = float(rows[0]["pa"]) or 1.0
    me = str(team["team_id"])
    board = [{"place": i + 1, "name": r.get("team_name") or "", "pts": _pts(r["pa"]),
              "w": round(100 * float(r["pa"]) / top, 1), "me": str(r["team_id"]) == me}
             for i, r in enumerate(rows)]
    n = len(rows)
    rank = next(b["place"] for b in board if b["me"])
    if rank == 1:
        where = "The most against anybody in your league."
    elif rank == n:
        where = "The fewest against anybody in your league."
    elif rank <= n / 2:
        where = f"The {_ordinal(rank)}-most of {n} teams."
    else:
        where = f"The {_ordinal(n - rank + 1)}-fewest of {n} teams."
    ol = team.get("opp_luck")
    if ol is None or abs(float(ol)) < 1:
        how = "About what those teams score against everybody else."
    elif float(ol) < 0:
        how = f"Your opponents scored {abs(float(ol)):.1f} a week more against you than they usually do."
    else:
        how = f"Your opponents scored {float(ol):.1f} a week less against you than they usually do."
    tone = "down" if rank <= max(1, n // 3) else "up" if rank > n - max(1, n // 3) else None
    return {"kind": "against", "kicker": "Points against", "big": _pts(team["pa"]),
            "board": {"rows": board, "n": n}, "rank": rank, "tone": tone,
            "line": f"Your opponents averaged {_pts(team['pa'])} points against you.",
            "sub": how,
            "caps": [f"Your opponents averaged {_pts(team['pa'])} points against you.", where, how]}


def lineup_card(players: list[dict], team: dict, cap: int = 8) -> Optional[dict]:
    """Every starter you used, against his own average, photos down the side:
    player luck you can see (John, 27 Oct: "we can do more for player luck
    and players/their photos"). The biggest swings either way, best at the
    top, at most `cap` rows."""
    ps = [p for p in players if int(p.get("starts") or 0) >= 1 and p.get("dev_total") is not None]
    if len(ps) < 3:
        return None
    ps = sorted(ps, key=lambda p: -abs(float(p["dev_total"])))[:cap]
    ps.sort(key=lambda p: -float(p["dev_total"]))
    spread = max([abs(float(p["dev_total"])) for p in ps] + [1.0])
    rows = [{"name": p.get("name") or p["player_key"], "position": p.get("position") or "",
             "photo": photo_url(p["player_key"], p.get("position")),
             "dev": float(p["dev_total"]), "starts": int(p["starts"]),
             "w": round(50 * abs(float(p["dev_total"])) / spread, 1)} for p in ps]
    pl = team.get("players")
    if pl is None or abs(float(pl)) < 0.05:
        total = "Add it up, and your players were worth nothing either way."
    elif float(pl) > 0:
        total = f"Add it up, and your players were worth {n_wins(float(pl))} of luck."
    else:
        total = f"Add it up, and your players cost you {n_wins(-float(pl))}."
    return {"kind": "lineup", "kicker": "Player luck", "rows": rows,
            "tone": "up" if (pl or 0) > 0.05 else "down" if (pl or 0) < -0.05 else None,
            "line": "Every starter you used, against his own average.", "sub": total,
            "caps": ["Every starter you used, against his own average.",
                     "Above the line, the ones who outplayed themselves for you. Below it, the ones who didn't.",
                     total]}


def report(team: dict, detail: dict, league_rows: list[dict]) -> dict:
    """{"cards": [...], "highlights": [...]} for one team. `team` is its row
    from league_page (with verdict, luckier_than, deserved_w/l).

    Each card carries "caps": what it says at each beat as it plays (27 Oct,
    John: "multiple animations, i.e. the week you played them, then something
    else could populate that is their average"). The template shows one
    caption per beat and builds the chart up beat by beat."""
    weeks = [w for w in (detail.get("weeks") or []) if w.get("ap_real") is not None]
    players = detail.get("players") or []
    cards: list[dict] = []
    highlights: list[str] = []
    rec = f"{team['w']}-{team['l']}" + (f"-{team['t']}" if team.get("t") else "")
    dw = team["deserved_w"]
    names = {str(r["team_id"]): r.get("team_name") or "" for r in league_rows or []}

    # the record, and the wins you earned. One number, not a W-L pair:
    # "a 0.3-2.7 team" read as a range (John, 1 Oct).
    owed = dw - (team["w"] + 0.5 * (team.get("t") or 0))
    if abs(owed) < 0.25:
        sub = "Right about what you earned. The football gods are even with you."
    elif owed > 0:
        sub = f"Somebody owes you {n_wins(owed)}."
    else:
        sub = f"You're {n_wins(-owed)} ahead of what you earned. Somebody up there likes you."
    cards.append({"kind": "record", "kicker": "The record", "big": rec,
                  "line": f"Your play earned {n_wins(dw)}.", "sub": sub,
                  "meter": record_meter(team), "tone": "up" if owed < -0.25 else "down" if owed > 0.25 else None,
                  "caps": ["That's the record.",
                           f"Your play earned {n_wins(dw)}. Every week, against every team in your league.",
                           sub]})

    # points against, the whole league (John, 27 Oct: "a leaguewide comparison
    # of points against you could be interesting"): your number first, then
    # every team's around it, then how your opponents played against you
    # compared with their usual
    ag = points_against(team, league_rows)
    if ag:
        cards.append(ag)

    # the season, week by week: robberies and gifts
    sc = season_chart(weeks)
    if sc:
        bits = []
        if sc["robbed"]:
            bits.append(f"{_plural(sc['robbed'], 'week')} in the top half of your league, and a loss.")
        if sc["gifts"]:
            bits.append(f"{_plural(sc['gifts'], 'week')} in the bottom half, and a win.")
        summary = " ".join(bits) or "Every result about what your score deserved."
        cards.append({"kind": "season", "kicker": "Week by week",
                      "line": "Where your score ranked in your league, every week.",
                      "sub": summary, "season": sc,
                      "caps": ["Where your score ranked in your league, every week.", summary]})

    # the unluckiest loss and the luckiest win: your score alone, then the
    # league around it, then the team you happened to play
    def week_card(w, unlucky):
        board = week_board(detail, w, team["team_id"], names)
        place, n = int(w["place"]), int(w["n"])
        opp_place = next((r["place"] for r in (board or {}).get("rows", []) if r["opp"]), None)
        if unlucky:
            above = place - 1
            c2 = ("The highest score in your league." if place == 1 else
                  f"The {_ordinal(place)}-highest of {n}. Better than {_plural(n - place, 'team')}.")
            c3 = ("You played the one team that scored more." if above == 1 else
                  f"Only {above} teams scored more. You played one of them.")
            return {"kind": "week", "kicker": "Your unluckiest week", "week": int(w["week"]),
                    "eyebrow": f"Week {w['week']}", "big": f"{_ordinal(place)} of {n}",
                    "pts": _pts(w["points"]), "result": "L", "opp_place": opp_place,
                    "line": f"You scored {_pts(w['points'])}, the {_ordinal(place)}-highest "
                            f"score of {n} in your league.",
                    "sub": "And you lost.", "tone": "down", "board": board,
                    "caps": [f"Week {w['week']}. You scored {_pts(w['points'])}.", c2, c3 + " And you lost."]}
        below = n - place
        c2 = f"{_ordinal(place)} of {n}. Only {_plural(below, 'team')} scored less."
        c3 = ("You played the one team that scored less." if below == 1 else
              f"You played one of the {below}.")
        return {"kind": "week", "kicker": "Your luckiest week", "week": int(w["week"]),
                "eyebrow": f"Week {w['week']}", "big": f"{_ordinal(place)} of {n}",
                "pts": _pts(w["points"]), "result": "W", "opp_place": opp_place,
                "line": f"You scored {_pts(w['points'])}. That would have lost to "
                        f"{n - place} teams in your league.",
                "sub": "You drew the one it beat.", "tone": "up", "board": board,
                "caps": [f"Week {w['week']}. You scored {_pts(w['points'])}.", c2, c3 + " And you won."]}

    losses = [w for w in weeks if w["result"] == "L"]
    wins = [w for w in weeks if w["result"] == "W"]
    if losses:
        w = max(losses, key=lambda x: (x["ap_real"], -x["week"]))
        if w["ap_real"] >= 0.5:
            cards.append(week_card(w, True))
            highlights.append(f"Week {w['week']}: the {_ordinal(w['place'])}-highest score "
                              f"in the league. Lost.")
    if wins:
        w = min(wins, key=lambda x: (x["ap_real"], x["week"]))
        if w["ap_real"] <= 0.5 and int(w["n"]) > int(w["place"]):
            cards.append(week_card(w, False))
            if not highlights:
                highlights.append(f"Week {w['week']}: {_ordinal(w['place'])} of {w['n']} "
                                  f"in the league. Won anyway.")

    # the opponent who found another gear (or forgot to show up): that week,
    # then their season and their average, then the gap and what you'd have
    # done against their usual
    def opp_card(w, hot):
        chart = opponent_chart(detail, w)
        delta = w["opponent_points"] - w["opp_avg"]
        their, avg, you = float(w["opponent_points"]), float(w["opp_avg"]), float(w["points"])
        theirs = sorted((float(b["pts"]) for b in (chart or {}).get("bars", [])), reverse=hot)
        rank = theirs.index(their) + 1 if their in theirs else None
        if rank == 1:
            how = "Their best week of the season." if hot else "Their worst week of the season."
        elif rank:
            how = f"Their {_ordinal(rank)}-{'best' if hot else 'worst'} week of the season."
        else:
            how = ""
        if hot and you > avg:
            vs = f"Against their usual {_pts(avg)}, your {_pts(you)} wins by {you - avg:.1f}."
        elif not hot and you < avg:
            vs = f"Against their usual {_pts(avg)}, your {_pts(you)} loses by {avg - you:.1f}."
        else:
            vs = "Against you, naturally." if hot else "You'll take it."
        big = f"+{delta:.1f}" if hot else f"−{abs(delta):.1f}"
        return {"kind": "opponent",
                "kicker": "The opponent who found another gear" if hot else "The opponent who didn't show up",
                "eyebrow": f"Week {w['week']} · {w['opp_name']}", "week": int(w["week"]),
                "big": big, "their": _pts(their), "avg": _pts(avg), "you": _pts(you),
                "line": (f"{w['opp_name']} averages {_pts(avg)}. They scored {_pts(their)}." if hot else
                         f"{w['opp_name']} averages {_pts(avg)}. Against you: {_pts(their)}."),
                "sub": "Against you, naturally." if hot else "You'll take it.",
                "tone": "down" if hot else "up", "opp": w["opp_name"], "chart": chart,
                "caps": [f"{w['opp_name']} averages {_pts(avg)} points a week.",
                         (f"But in Week {w['week']}, they put up {_pts(their)} against you." if hot else
                          f"But in Week {w['week']}, against you, they put up just {_pts(their)}."),
                         " ".join(x for x in (how, vs) if x)]}

    faced = [w for w in weeks if w.get("opp_avg") is not None and w.get("opponent_points") is not None]
    if faced:
        hot = max(faced, key=lambda x: (x["opponent_points"] - x["opp_avg"], -x["week"]))
        delta = hot["opponent_points"] - hot["opp_avg"]
        if delta >= 8:
            cards.append(opp_card(hot, True))
            highlights.append(f"{hot['opp_name']} scored {delta:.0f} above their average. "
                              f"Against you.")
        cold = min(faced, key=lambda x: (x["opponent_points"] - x["opp_avg"], x["week"]))
        cdelta = cold["opponent_points"] - cold["opp_avg"]
        if cdelta <= -8 and cold["result"] == "W":
            cards.append(opp_card(cold, False))

    # close games
    cw, cl = team.get("close_w") or 0, team.get("close_l") or 0
    if cw + cl:
        verdict_line = ("The coin hates you." if cl > cw else
                        "The coin loves you." if cw > cl else "Dead even.")
        cards.append({"kind": "close", "kicker": "Coin flips", "big": f"{cw}-{cl}",
                      "line": "In games decided by a few points.", "sub": verdict_line,
                      "tone": "down" if cl > cw else "up" if cw > cl else None,
                      "coins": coins(detail, weeks),
                      "caps": [f"{_plural(cw + cl, 'game')} decided by a few points. {verdict_line}"]})

    # your players: the bust and the boom. His average first, then his weeks
    # for you, then what it added up to.
    def player_card(p, bust):
        chart = player_chart(detail, p, "low" if bust else "high")
        name = p.get("name") or p["player_key"]
        starts = _plural(p["starts"], "start")
        avg = chart["avg"] if chart else None
        # The plain version (John, 28 Oct: "the player pages are a bit
        # difficult to understand"): his usual against what he did for you,
        # each start listed under it.
        key = str(p["player_key"])
        games = sorted(((int(l["week"]), float(l.get("points") or 0)) for l in (detail.get("lineups") or [])
                        if str(l.get("player_key")) == key and l.get("started")), key=lambda r: r[0])
        usual = float(avg) if avg else None
        mine = (sum(v for _, v in games) / len(games)) if games else None
        top = max([v for v in (usual, mine) if v is not None] + [1.0])
        compare = None
        if usual is not None and mine is not None:
            compare = {"usual": _pts(usual), "mine": _pts(mine),
                       "usual_w": round(100 * max(usual, 0) / top, 1),
                       "mine_w": round(100 * max(mine, 0) / top, 1),
                       "weeks": [{"week": wk, "pts": _pts(v), "above": usual is not None and v >= usual}
                                 for wk, v in games]}
        return {"kind": "player", "kicker": "Your biggest no-show" if bust else "The one who carried you",
                "eyebrow": name,
                "big": (f"−{abs(p['dev_total']):.1f}" if bust else f"+{p['dev_total']:.1f}"),
                "line": (f"Points {'below' if bust else 'above'} his own average, across {starts} for you."),
                "sub": (f"Low point: {_pts(p['worst'])}." if bust else f"Best game: {_pts(p['best'])}."),
                "tone": "down" if bust else "up", "position": p.get("position"), "chart": chart,
                "photo": photo_url(p["player_key"], p.get("position")),
                "compare": compare, "starts": int(p["starts"]),
                "caps": [(f"{name} averages {avg} points a week in your league." if avg else f"{name}."),
                         (f"For you, he averaged {compare['mine']}, across {starts}." if compare
                          else f"For you, across {starts}:"),
                         (f"That's {abs(p['dev_total']):.1f} points short of his average, all told. "
                          f"Low point: {_pts(p['worst'])}." if bust else
                          f"That's {p['dev_total']:.1f} points over his average, all told. "
                          f"Best game: {_pts(p['best'])}.")]}

    lu = lineup_card(players, team)
    if lu:
        cards.append(lu)
    if players:
        bust = players[0]
        if bust["dev_total"] <= -6:
            cards.append(player_card(bust, True))
        boom = players[-1]
        if boom["dev_total"] >= 6 and boom is not bust:
            cards.append(player_card(boom, False))

    # not luck
    own = team.get("own_losses") or 0
    bench = team.get("bench_avg")
    swap = winning_swap(detail) if own else None
    if swap:
        o, i = swap["out"], swap["in"]
        others = own - 1
        sub = ("That one's on you." if not others else
               "And your bench would have won another one." if others == 1 else
               f"And your bench would have won {others} more.")
        cards.append({"kind": "swap", "kicker": "Not luck",
                      "eyebrow": f"Week {swap['week']} · lost by {_pts(swap['margin'])}",
                      "swap": {"out": _swap_side(o, "Started"), "in": _swap_side(i, "On your bench")},
                      "line": f"Start {i['name']} over {o['name']} and you win by "
                              f"{_pts(swap['gain'] - swap['margin'])}.",
                      "sub": sub, "tone": "down",
                      "caps": [f"Start {i['name']} over {o['name']} and you win by "
                               f"{_pts(swap['gain'] - swap['margin'])}. {sub}"]})
        highlights.append(f"Week {swap['week']}: {i['name']} scored {_pts(i['points'])} on your "
                          f"bench. You lost by {_pts(swap['margin'])}.")
    # Every loss, with the margin, for the "not luck" card (John, 28 Oct: "none
    # of your losses were the lineup's fault" needed to be clearer).
    lost = [{"week": int(w["week"]), "margin": _pts(float(w["opponent_points"]) - float(w["points"]))}
            for w in sorted(weeks, key=lambda w: int(w["week"]))
            if w["result"] == "L" and w.get("opponent_points") is not None]
    n_lost = int(team.get("l") or 0)
    if own:
        line = f"Loss{'es' if own != 1 else ''} your own bench would have won."
        sub = "That one's on you." if own == 1 else "Those are on you."
        cards.append({"kind": "skill", "kicker": "Not luck", "big": str(own), "line": line,
                      "sub": sub, "tone": "down", "losses": lost, "unit": "on you",
                      "caps": [f"You lost {_plural(n_lost, 'game')}.",
                               (f"Your best possible lineup would have won {own} of them." if n_lost > 1
                                else "Your best possible lineup would have won it."),
                               "Those were the lineup, not luck." if own > 1 else "That one was the lineup, not luck."]})
        highlights.append(f"Your bench would have won {own} of your losses.")
    elif bench is not None:
        line = "Points a week left on your bench."
        sub = ("None of your losses were the lineup's fault." if team["l"]
               else "Never cost you a game. Yet.")
        if n_lost:
            all_ = "both" if n_lost == 2 else f"all {n_lost}" if n_lost > 2 else "it"
            caps = [f"You left {_pts(bench)} points a week on your bench.",
                    (f"But you lost {_plural(n_lost, 'game')}, and your best possible lineup "
                     f"would have lost {all_} too."),
                    ("So those losses weren't the lineup. They were the matchups." if n_lost > 1
                     else "So that loss wasn't the lineup. It was the matchup.")]
        else:
            caps = [f"You left {_pts(bench)} points a week on your bench.",
                    "And you haven't lost a game, so it never cost you. Yet."]
        cards.append({"kind": "skill", "kicker": "Not luck", "big": _pts(bench), "line": line,
                      "sub": sub, "losses": lost, "unit": "pts a week on the bench", "caps": caps})

    games = int(team.get("games") or len(weeks) or 0)
    return {"cards": storyline(cards, games), "highlights": highlights[:3],
            "verdict_world": VERDICT_WORLD.get(team.get("verdict") or "", "paper")}


def standings_slope(teams: list[dict]) -> Optional[dict]:
    """The league page's headline chart: the standings on the left, the
    standings each team earned on the right, a line between each team's two
    places. Lucky teams sit higher on the left than they earned (green),
    unlucky teams lower (red). Ties go to points for, then name."""
    if len(teams) < 3:
        return None

    def key_actual(t):
        return (-(float(t["w"]) + 0.5 * float(t.get("t") or 0)), -float(t.get("pf") or 0),
                str(t.get("team_name") or ""))

    def key_earned(t):
        return (-float(t["deserved_w"]), -float(t.get("pf") or 0), str(t.get("team_name") or ""))

    actual = sorted(teams, key=key_actual)
    earned = sorted(teams, key=key_earned)
    a_rank = {str(t["team_id"]): i for i, t in enumerate(actual)}
    e_rank = {str(t["team_id"]): i for i, t in enumerate(earned)}

    def rec(t):
        return f"{t['w']}-{t['l']}" + (f"-{t['t']}" if t.get("t") else "")

    lines = []
    for t in teams:
        tid = str(t["team_id"])
        d = e_rank[tid] - a_rank[tid]          # >0: placed higher than earned
        lines.append({"team_id": tid, "a": a_rank[tid], "e": e_rank[tid],
                      "dir": "up" if d > 0 else "down" if d < 0 else "even", "d": abs(d)})
    biggest = max(lines, key=lambda l: (l["d"], l["dir"] == "down"))
    return {"n": len(teams),
            "left": [{"team_id": str(t["team_id"]), "name": t.get("team_name") or "",
                      "rec": rec(t), "place": i + 1} for i, t in enumerate(actual)],
            "right": [{"team_id": str(t["team_id"]), "name": t.get("team_name") or "",
                       "earned": f"{float(t['deserved_w']):.1f}", "place": i + 1}
                      for i, t in enumerate(earned)],
            "lines": sorted(lines, key=lambda l: l["d"]),     # steepest drawn last, on top
            "biggest": biggest if biggest["d"] else None}
