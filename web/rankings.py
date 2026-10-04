"""
National Rankings (John, 3 Oct: "how your team ranks nationally ... points
for, record, and whatever else should be considered").

Every connected team gets a Power Score out of 100 and a national rank:

  50%  points a game in plain PPR, as a percentile of every ranked team
  30%  national all-play: each week, the share of every ranked team in the
       country its PPR score beat, averaged over its weeks (consistency)
  20%  record: actual win share, ties as half

Production runs this as SQL (migration 032: power_rows / power_national, on
top of 031's luck_rows). compute() below is the same arithmetic for the demo
database, and the tests hold them to each other's rules.

Sign-in only: you see your own leagues' teams by name, and everyone else on
the national board as "a 12-team PPR league on Sleeper", never by name.
"""

from __future__ import annotations

import bisect
import math
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

WEIGHTS = {"points": 0.5, "allplay": 0.3, "record": 0.2}
MIN_COVERAGE = 0.75
NATIONAL_SECONDS = 3600
TOP_N = 25
NEAR = 4

#: (share of the country at or below, word): the first that fits.
TIERS = [
    (0.01, "Juggernaut"),
    (0.10, "Contender"),
    (0.35, "Playoff team"),
    (0.65, "Mid"),
    (0.90, "Rebuilding"),
    (0.99, "Bad"),
    (1.01, "Generationally bad"),
]
QUIPS = {
    "Juggernaut": "Everyone in your league is already complaining about you.",
    "Contender": "This is a real team. Don't mess it up at the deadline.",
    "Playoff team": "Comfortably above the line. Now stay there.",
    "Mid": "Right in the middle of America. Like a casserole.",
    "Rebuilding": "There's still time. There's always still time.",
    "Bad": "It's not the schedule. We checked.",
    "Generationally bad": "Historians will study this team.",
}

COLS = ["league_id", "team_id", "rank", "score", "ppg", "w", "l", "t",
        "ap_pct", "win_pct", "ppg_pct", "team_count", "scoring_type",
        "provider", "games", "lw", "lw_pts", "lw_beat", "lw_pool"]

_cache: dict = {}


def clear_cache() -> None:
    _cache.clear()


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


# ---------------------------------------------------------------------------
# The arithmetic (the SQL's twin)
# ---------------------------------------------------------------------------

def compute(luck_rows: list[dict], team_rows: list[dict], season: int) -> list[dict]:
    """luck.compute() rows for the season, plus team_weeks rows -> ranked
    rows (dicts with COLS), best first."""
    ppr: dict = {}
    weeks: dict = {}
    latest: dict = {}
    for r in team_rows:
        if (int(r["season"]) == season and r.get("result") in ("W", "L", "T")
                and (_f(r.get("points")) or 0) > 0 and r.get("ppr_points") is not None):
            cov = r.get("ppr_coverage")
            if cov is not None and float(cov) < MIN_COVERAGE:
                continue
            key = (str(r["league_id"]), str(r["team_id"]))
            pts = float(r["ppr_points"])
            ppr.setdefault(key, []).append(pts)
            weeks.setdefault(int(r["week"]), []).append((key, pts))
            if key not in latest or int(r["week"]) > latest[key][0]:
                latest[key] = (int(r["week"]), pts)
    # National all-play: each week, the share of every team in the pool whose
    # PPR score was lower (percent_rank, as in the SQL), averaged per team.
    nap: dict = {}
    beat: dict = {}
    for wk, wk_rows in weeks.items():
        n_w = len(wk_rows)
        ordered = sorted(p for _, p in wk_rows)
        for key, pts in wk_rows:
            below = bisect.bisect_left(ordered, pts)
            nap.setdefault(key, []).append(below / (n_w - 1) if n_w > 1 else 0.0)
            beat[(key, wk)] = (below, n_w)
    base = []
    for r in luck_rows:
        key = (str(r["league_id"]), str(r["team_id"]))
        games = int(r.get("games") or 0)
        if key not in ppr or games <= 0:
            continue
        base.append({
            "league_id": key[0], "team_id": key[1],
            "provider": r.get("provider"), "team_count": r.get("team_count"),
            "scoring_type": r.get("scoring_type"), "games": games,
            "w": int(r["w"]), "l": int(r["l"]), "t": int(r["t"]),
            "ppg": sum(ppr[key]) / len(ppr[key]),
            "ap_pct": sum(nap[key]) / len(nap[key]),
            "win_pct": float(r["wins"]) / games,
            "last_week": r.get("last_week"),
            "lw": latest[key][0], "lw_pts": round(latest[key][1], 2),
            "lw_beat": beat[(key, latest[key][0])][0],
            "lw_pool": beat[(key, latest[key][0])][1],
        })
    n = len(base)
    # Postgres averages exactly; floats don't. Equal averages must tie here
    # as they do there, so compare them rounded well below a hundredth.
    for b in base:
        b["_k"] = round(b["ppg"], 6)
    for b in base:
        below = sum(1 for o in base if o["_k"] < b["_k"])
        b["ppg_pct"] = below / (n - 1) if n > 1 else 0.0
        # round() on a numeric in Postgres goes half away from zero.
        raw = 100 * (WEIGHTS["points"] * b["ppg_pct"] + WEIGHTS["allplay"] * b["ap_pct"]
                     + WEIGHTS["record"] * b["win_pct"])
        b["score"] = float(Decimal(repr(raw)).quantize(Decimal("0.1"), ROUND_HALF_UP))
    for b in base:
        b["rank"] = 1 + sum(1 for o in base if (o["score"], o["_k"]) > (b["score"], b["_k"]))
    for b in base:
        del b["_k"]
        b["ppg"] = round(b["ppg"], 2)
        b["ap_pct"] = round(b["ap_pct"], 3)
        b["win_pct"] = round(b["win_pct"], 3)
        b["ppg_pct"] = round(b["ppg_pct"], 4)
    base.sort(key=lambda b: (b["rank"], b["league_id"], b["team_id"]))
    return base


def national_json(rows: list[dict]) -> dict:
    """compute() rows -> the shape power_national() returns."""
    return {"teams": len(rows),
            "through": max((r["last_week"] for r in rows if r.get("last_week")), default=None),
            "rows": [[r[c] for c in COLS] for r in rows]}


# ---------------------------------------------------------------------------
# The pages
# ---------------------------------------------------------------------------

def tier(rank: int, n: int) -> str:
    share = rank / n if n else 1.0
    for floor, word in TIERS:
        if share <= floor:
            return word
    return TIERS[-1][1]


def top_share(rank: int, n: int) -> str:
    """'Top 4%' / 'Bottom 12%' for a rank out of n."""
    if not n:
        return ""
    share = rank / n
    if share <= 0.5:
        return f"Top {max(1, math.ceil(share * 100))}%"
    return f"Bottom {max(1, math.ceil((1 - share + 1 / n) * 100))}%"


def national(db, season: int) -> dict:
    """The whole board, parsed and indexed. An hour's cache; an empty board
    rather than an error page if the SQL isn't there or times out."""
    hit = _cache.get(("national", season))
    if hit and time.time() - hit[0] < NATIONAL_SECONDS:
        return hit[1]
    try:
        raw = db.power_national(season) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[rankings] national board unavailable: {exc}", flush=True)
        return {"teams": 0, "through": None, "rows": [], "index": {}, "dist": []}
    rows = []
    for arr in raw.get("rows") or []:
        r = dict(zip(COLS, arr))
        r["league_id"], r["team_id"] = str(r["league_id"]), str(r["team_id"])
        for k in ("score", "ppg", "ap_pct", "win_pct", "ppg_pct"):
            r[k] = _f(r[k])
        r["rank"] = int(r["rank"])
        r["lw_pts"] = _f(r.get("lw_pts"))
        rows.append(r)
    n = len(rows)
    dist = [0] * 20
    for r in rows:
        dist[min(int((r["score"] or 0) // 5), 19)] += 1
    out = {"teams": n, "through": raw.get("through"), "rows": rows,
           "index": {(r["league_id"], r["team_id"]): r for r in rows},
           "dist": dist}
    _cache[("national", season)] = (time.time(), out)
    return out


def _decorate(r: dict, n: int) -> dict:
    r = dict(r)
    r["tier"] = tier(r["rank"], n)
    r["quip"] = QUIPS[r["tier"]]
    r["share"] = top_share(r["rank"], n)
    r["points_share"] = top_share(n - round((r["ppg_pct"] or 0) * (n - 1)), n) if n else ""
    r["record"] = f"{r['w']}-{r['l']}" + (f"-{r['t']}" if r.get("t") else "")
    return r


def league_view(db, season: int, league: dict, team_id: str = "") -> dict:
    """One league on the national board: its teams by name, the national top,
    and the stretch of the board around the chosen team."""
    from web import luck
    from web.around import describe
    nat = national(db, season)
    n = nat["teams"]
    lrows, stats_id = luck._cached(("league", season, league["id"]),
                                   lambda: luck._league_rows(db, season, league))
    names = {str(r["team_id"]): r for r in lrows}
    teams, unranked = [], []
    for tid, lr in names.items():
        nr = nat["index"].get((str(stats_id), tid))
        base = {"team_id": tid, "team_name": lr.get("team_name") or "",
                "manager": lr.get("manager") or ""}
        if nr:
            teams.append({**_decorate(nr, n), **base})
        else:
            unranked.append({**base, "record": f"{lr['w']}-{lr['l']}"
                             + (f"-{lr['t']}" if lr.get("t") else "")})
    teams.sort(key=lambda r: (r["rank"], r["team_id"]))
    for i, t in enumerate(teams, 1):
        t["league_place"] = i
        t["place_text"] = luck._ordinal(i)
    chosen = next((t for t in teams if t["team_id"] == str(team_id)), None)

    def board_row(r: dict) -> dict:
        mine = r["league_id"] == str(stats_id)
        own = names.get(r["team_id"]) if mine else None
        return {**_decorate(r, n), "mine": mine,
                "you": bool(chosen and mine and r["team_id"] == chosen["team_id"]),
                "label": (own.get("team_name") or "") if own else describe(r, cap=True),
                "sub": (own.get("manager") or "") if own else ""}

    top = [board_row(r) for r in nat["rows"][:TOP_N]]
    near = []
    if chosen and chosen["rank"] > TOP_N:
        i = next((k for k, r in enumerate(nat["rows"])
                  if r["league_id"] == str(stats_id) and r["team_id"] == chosen["team_id"]), None)
        if i is not None:
            near = [board_row(r) for r in nat["rows"][max(0, i - NEAR): i + NEAR + 1]]
    peak = max(nat["dist"]) if nat["dist"] else 0
    # Label only you and the league's best and worst: a dozen names on one
    # axis is a pile-up.
    ends = {teams[0]["team_id"], teams[-1]["team_id"]} if teams else set()
    marks = [{"team_id": t["team_id"], "name": t["team_name"], "x": t["score"],
              "you": bool(chosen and t["team_id"] == chosen["team_id"]),
              "ends": t["team_id"] in ends and not (
                  chosen and abs(t["score"] - chosen["score"]) < 12)} for t in teams]
    return {"teams": teams, "unranked": unranked, "chosen": chosen,
            "national": n, "through": nat["through"], "top": top, "near": near,
            "dist": [{"lo": i * 5, "n": c, "h": round(100 * c / peak, 1) if peak else 0}
                     for i, c in enumerate(nat["dist"])],
            "marks": marks, "stats_league_id": stats_id}
