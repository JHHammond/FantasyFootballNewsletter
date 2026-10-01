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
    return {"teams": rows, "national": nat.get("teams") or 0, "nat": nat,
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


def report(team: dict, detail: dict, league_rows: list[dict]) -> dict:
    """{"cards": [...], "highlights": [...]} for one team. `team` is its row
    from league_page (with verdict, luckier_than, deserved_w/l)."""
    weeks = [w for w in (detail.get("weeks") or []) if w.get("ap_real") is not None]
    players = detail.get("players") or []
    cards: list[dict] = []
    highlights: list[str] = []
    rec = f"{team['w']}-{team['l']}" + (f"-{team['t']}" if team.get("t") else "")
    dw, dl = team["deserved_w"], team["deserved_l"]

    # 2. the record, and the record you earned
    if abs(team["total"]) < 0.25:
        cards.append({"kind": "record", "kicker": "The record", "big": rec,
                      "line": "Right about what you earned. The football gods are even with you.",
                      "sub": f"You played like a {dw:.1f}-{dl:.1f} team."})
    else:
        cards.append({"kind": "record", "kicker": "The record", "big": rec,
                      "line": f"You played like a {dw:.1f}-{dl:.1f} team.",
                      "sub": ("Somebody owes you." if team["total"] < 0
                              else "Somebody up there likes you.")})

    # 3. the unluckiest loss and the luckiest win
    losses = [w for w in weeks if w["result"] == "L"]
    wins = [w for w in weeks if w["result"] == "W"]
    if losses:
        w = max(losses, key=lambda x: (x["ap_real"], -x["week"]))
        if w["ap_real"] >= 0.5:
            line = (f"You scored {_pts(w['points'])}, the {_ordinal(w['place'])}-highest "
                    f"score of {w['n']} in your league.")
            cards.append({"kind": "week", "kicker": "Your unluckiest week",
                          "eyebrow": f"Week {w['week']}", "big": f"{_ordinal(w['place'])} of {w['n']}",
                          "line": line, "sub": "And you lost.", "tone": "down"})
            highlights.append(f"Week {w['week']}: the {_ordinal(w['place'])}-highest score "
                              f"in the league. Lost.")
    if wins:
        w = min(wins, key=lambda x: (x["ap_real"], x["week"]))
        if w["ap_real"] <= 0.5:
            cards.append({"kind": "week", "kicker": "Your luckiest week",
                          "eyebrow": f"Week {w['week']}", "big": f"{_ordinal(w['place'])} of {w['n']}",
                          "line": f"You scored {_pts(w['points'])}. That would have lost to "
                                  f"{w['n'] - w['place']} teams in your league.",
                          "sub": "You drew the one it beat.", "tone": "up"})
            if not highlights:
                highlights.append(f"Week {w['week']}: {_ordinal(w['place'])} of {w['n']} "
                                  f"in the league. Won anyway.")

    # 4. the opponent who found another gear (or forgot to show up)
    faced = [w for w in weeks if w.get("opp_avg") is not None and w.get("opponent_points") is not None]
    if faced:
        hot = max(faced, key=lambda x: (x["opponent_points"] - x["opp_avg"], -x["week"]))
        delta = hot["opponent_points"] - hot["opp_avg"]
        if delta >= 8:
            cards.append({"kind": "opponent", "kicker": "The opponent who found another gear",
                          "eyebrow": f"Week {hot['week']} \u00b7 {hot['opp_name']}",
                          "big": f"+{delta:.1f}",
                          "line": f"{hot['opp_name']} averages {_pts(hot['opp_avg'])}. "
                                  f"They scored {_pts(hot['opponent_points'])}.",
                          "sub": "Against you, naturally.", "tone": "down",
                          "opp": hot["opp_name"], "chart": opponent_chart(detail, hot)})
            highlights.append(f"{hot['opp_name']} scored {delta:.0f} above their average. "
                              f"Against you.")
        cold = min(faced, key=lambda x: (x["opponent_points"] - x["opp_avg"], x["week"]))
        cdelta = cold["opponent_points"] - cold["opp_avg"]
        if cdelta <= -8 and cold["result"] == "W":
            cards.append({"kind": "opponent", "kicker": "The opponent who didn't show up",
                          "eyebrow": f"Week {cold['week']} \u00b7 {cold['opp_name']}",
                          "big": f"\u2212{abs(cdelta):.1f}",
                          "line": f"{cold['opp_name']} averages {_pts(cold['opp_avg'])}. "
                                  f"Against you: {_pts(cold['opponent_points'])}.",
                          "sub": "You'll take it.", "tone": "up",
                          "opp": cold["opp_name"], "chart": opponent_chart(detail, cold)})

    # 5. your players: the bust and the boom
    if players:
        bust = players[0]
        if bust["dev_total"] <= -6:
            cards.append({"kind": "player", "kicker": "Your biggest no-show",
                          "eyebrow": bust.get("name") or bust["player_key"],
                          "big": f"\u2212{abs(bust['dev_total']):.1f}",
                          "line": f"Points below his own average, across {bust['starts']} "
                                  f"start{'s' if bust['starts'] != 1 else ''} for you.",
                          "sub": f"Low point: {_pts(bust['worst'])}.", "tone": "down",
                          "position": bust.get("position"),
                          "photo": photo_url(bust["player_key"], bust.get("position"))})
        boom = players[-1]
        if boom["dev_total"] >= 6 and boom is not bust:
            cards.append({"kind": "player", "kicker": "The one who carried you",
                          "eyebrow": boom.get("name") or boom["player_key"],
                          "big": f"+{boom['dev_total']:.1f}",
                          "line": f"Points above his own average, across {boom['starts']} "
                                  f"start{'s' if boom['starts'] != 1 else ''} for you.",
                          "sub": f"Best game: {_pts(boom['best'])}.", "tone": "up",
                          "position": boom.get("position"),
                          "photo": photo_url(boom["player_key"], boom.get("position"))})

    # 6. close games
    cw, cl = team.get("close_w") or 0, team.get("close_l") or 0
    if cw + cl:
        cards.append({"kind": "close", "kicker": "Coin flips", "big": f"{cw}-{cl}",
                      "line": "In games decided by a few points.",
                      "sub": ("The coin hates you." if cl > cw else
                              "The coin loves you." if cw > cl else "Dead even."),
                      "tone": "down" if cl > cw else "up" if cw > cl else None})

    # 7. not luck
    own = team.get("own_losses") or 0
    bench = team.get("bench_avg")
    swap = winning_swap(detail) if own else None
    if swap:
        o, i = swap["out"], swap["in"]
        others = own - 1
        cards.append({"kind": "swap", "kicker": "Not luck",
                      "eyebrow": f"Week {swap['week']} \u00b7 lost by {_pts(swap['margin'])}",
                      "swap": {"out": _swap_side(o, "Started"), "in": _swap_side(i, "On your bench")},
                      "line": f"Start {i['name']} over {o['name']} and you win by "
                              f"{_pts(swap['gain'] - swap['margin'])}.",
                      "sub": ("That one's on you." if not others else
                              "And your bench would have won another one." if others == 1 else
                              f"And your bench would have won {others} more."),
                      "tone": "down"})
        highlights.append(f"Week {swap['week']}: {i['name']} scored {_pts(i['points'])} on your "
                          f"bench. You lost by {_pts(swap['margin'])}.")
    elif own:
        cards.append({"kind": "skill", "kicker": "Not luck", "big": str(own),
                      "line": f"Loss{'es' if own != 1 else ''} your own bench would have won.",
                      "sub": "That one's on you." if own == 1 else "Those are on you.",
                      "tone": "down"})
        highlights.append(f"Your bench would have won {own} of your losses.")
    elif bench is not None:
        cards.append({"kind": "skill", "kicker": "Not luck", "big": _pts(bench),
                      "line": "Points a week left on your bench.",
                      "sub": "None of your losses were the lineup's fault." if team["l"]
                             else "Never cost you a game. Yet."})

    # 8. league place
    if league_rows:
        place = 1 + sum(1 for r in league_rows if r["total"] > team["total"])
        cards.append({"kind": "league", "kicker": "In your league",
                      "big": f"{_ordinal(place)} of {len(league_rows)}",
                      "line": ("The luckiest team in the league." if place == 1 else
                               "The unluckiest team in the league." if place == len(league_rows)
                               else "On the luck table."),
                      "sub": "One more card: the verdict."})
    return {"cards": cards, "highlights": highlights[:3]}
