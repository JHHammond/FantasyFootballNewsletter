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
VIEW_SECONDS = 600
TOP_N = 5          # the preview on the page; the full board opens on demand (7 Oct)
PAGE = 50
#: A league size gets its own filter chip once it has this share of the board.
SIZE_MIN_SHARE = 0.01
NEAR = 2

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
        "provider", "games", "lw", "lw_pts", "lw_beat", "lw_pool", "own"]

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
    # Each week in PPR when it was re-scored with enough starters matched;
    # otherwise the league's own points, if its scoring looks standard (the
    # Around the Leagues test). Leagues that fail it are left off.
    wk0 = []
    for r in team_rows:
        if (int(r["season"]) == season and r.get("result") in ("W", "L", "T")
                and (_f(r.get("points")) or 0) > 0):
            cov = r.get("ppr_coverage")
            ok = r.get("ppr_points") is not None and (cov is None or float(cov) >= MIN_COVERAGE)
            wk0.append((str(r["league_id"]), str(r["team_id"]), int(r["week"]),
                        float(r["points"]), float(r["ppr_points"]) if ok else None))
    lg: dict = {}
    for lid, _, _, raw, ppr_w in wk0:
        e = lg.setdefault(lid, ([], []))
        e[0].append(raw)
        if ppr_w is not None:
            e[1].append(ppr_w)

    def standard(lid: str) -> bool:
        raw, ppr_l = lg[lid]
        raw_avg = sum(raw) / len(raw)
        if ppr_l and sum(ppr_l) / len(ppr_l) > 0:
            return 0.7 <= raw_avg / (sum(ppr_l) / len(ppr_l)) <= 1.3
        return 40 <= raw_avg <= 200

    ppr: dict = {}
    own: dict = {}
    weeks: dict = {}
    latest: dict = {}
    for lid, tid, week, raw, ppr_w in wk0:
        if ppr_w is None and not standard(lid):
            continue
        key = (lid, tid)
        pts = ppr_w if ppr_w is not None else raw
        own[key] = own.get(key, False) or ppr_w is None
        ppr.setdefault(key, []).append(pts)
        weeks.setdefault(week, []).append((key, pts))
        if key not in latest or week > latest[key][0]:
            latest[key] = (week, pts)
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
            "own": own[key],
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


def _row(r: dict) -> dict:
    r = {c: r.get(c) for c in COLS}
    r["league_id"], r["team_id"] = str(r["league_id"]), str(r["team_id"])
    for k in ("score", "ppg", "ap_pct", "win_pct", "ppg_pct", "lw_pts"):
        r[k] = _f(r[k])
    r["rank"] = int(r["rank"])
    return r


def board_view(db, season: int, league_id: str, team_id: str = "") -> dict:
    """The rows one page shows, from the saved board (migration 033): ten
    minutes' cache per league and team. An empty board, not an error page, if
    it can't be read."""
    key = ("view", season, str(league_id), str(team_id or ""))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < VIEW_SECONDS:
        return hit[1]
    try:
        raw = db.power_view(season, league_id, team_id) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[rankings] saved board unavailable: {exc}", flush=True)
        return {"teams": 0, "through": None, "dist": [0] * 20,
                "top": [], "league": [], "near": [], "error": True}
    out = {"teams": int(raw.get("teams") or 0), "through": raw.get("through"),
           "dist": list(raw.get("dist") or [0] * 20),
           "top": [_row(r) for r in raw.get("top") or []],
           "league": [_row(r) for r in raw.get("league") or []],
           "near": [_row(r) for r in raw.get("near") or []]}
    _cache[key] = (time.time(), out)
    return out


def league_view(db, season: int, league: dict, team_id: str = "") -> dict:
    """One league on the national board: its teams by name, the national top,
    and the stretch of the board around the chosen team."""
    from web import luck
    from web.around import describe
    lrows, stats_id = luck._cached(("league", season, league["id"]),
                                   lambda: luck._league_rows(db, season, league))
    nat = board_view(db, season, str(stats_id), str(team_id or ""))
    n = nat["teams"]
    index = {(r["league_id"], r["team_id"]): r for r in nat["league"]}
    names = {str(r["team_id"]): r for r in lrows}
    teams, unranked = [], []
    for tid, lr in names.items():
        nr = index.get((str(stats_id), tid))
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

    top = [board_row(r) for r in nat["top"][:TOP_N]]
    near = []
    if chosen and chosen["rank"] > TOP_N:
        ordered = sorted(nat["near"], key=lambda r: (r["rank"], r["league_id"], r["team_id"]))
        i = next((k for k, r in enumerate(ordered)
                  if r["league_id"] == str(stats_id) and r["team_id"] == chosen["team_id"]), None)
        if i is not None:
            near = [board_row(r) for r in ordered[max(0, i - NEAR): i + NEAR + 1]]
    peak = max(nat["dist"]) if nat["dist"] else 0
    # Every team in the league is labelled (John, 4 Oct); the page stacks the
    # labels so close scores don't pile up, and a click shows the numbers.
    marks = [{"team_id": t["team_id"], "name": t["team_name"], "x": t["score"],
              "score": t["score"], "rank": t["rank"], "tier": t["tier"],
              "share": t["share"], "record": t["record"],
              "you": bool(chosen and t["team_id"] == chosen["team_id"])}
             for t in sorted(teams, key=lambda t: t["score"])]
    # Your place among leagues your size ("#142 of 6,210 12-team leagues").
    you_size = next((r.get("team_count") for r in nat["league"]), None)
    size_rank = None
    if chosen and you_size:
        try:
            p = board_page(db, season, league, chosen["team_id"], int(you_size), 0, 1)
            if p.get("me"):
                size_rank = {"size": int(you_size), "pos": int(p["me"]), "of": p["total"]}
        except Exception as exc:  # noqa: BLE001
            print(f"[rankings] size rank unavailable: {exc}", flush=True)
    return {"teams": teams, "unranked": unranked, "chosen": chosen,
            "size_rank": size_rank, "you_size": you_size,
            "national": n, "through": nat["through"], "top": top, "near": near,
            "board_error": bool(nat.get("error")),
            "dist": [{"lo": i * 5, "n": c, "h": round(100 * c / peak, 1) if peak else 0}
                     for i, c in enumerate(nat["dist"])],
            "marks": marks, "stats_league_id": stats_id}


# ---------------------------------------------------------------------------
# The full board (7 Oct, John: "the list of the top people nationally should
# be smaller, but you should be able to open it and scroll around on it ...
# filterable to different league sizes")
# ---------------------------------------------------------------------------

def size_chips(sizes: list[dict], total: int) -> list[dict]:
    """Every league size common enough to be worth a filter: [{size, n}],
    smallest first. Rare sizes (a 7-team league) stay under "All"."""
    floor = max(1, math.ceil(total * SIZE_MIN_SHARE)) if total else 1
    return [{"size": int(s["size"]), "n": int(s["n"])} for s in sizes
            if s.get("size") is not None and int(s["n"]) >= floor]


def board_page(db, season: int, league: dict, team_id: str = "",
               size: Optional[int] = None, offset: int = 0,
               limit: int = PAGE) -> dict:
    """One page of the whole national board as the browser needs it: your
    league's teams by name, everyone else anonymous, each with its place in
    the filter and its national rank."""
    from web import luck
    from web.around import describe
    lrows, stats_id = luck._cached(("league", season, league["id"]),
                                   lambda: luck._league_rows(db, season, league))
    names = {str(r["team_id"]): r for r in lrows}
    key = ("page", season, str(stats_id), str(team_id or ""), size, int(offset), int(limit))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < VIEW_SECONDS:
        raw = hit[1]
    else:
        raw = db.power_page(season, size=size, offset=offset, limit=limit,
                            league_id=str(stats_id), team_id=str(team_id or "")) or {}
        _cache[key] = (time.time(), raw)
    n = int(raw.get("teams") or 0)
    rows = []
    for a in raw.get("rows") or []:
        r = _row(a)
        mine = r["league_id"] == str(stats_id)
        own = names.get(r["team_id"]) if mine else None
        d = _decorate(r, n)
        rows.append({
            "pos": int(a.get("pos") or 0), "rank": r["rank"], "score": r["score"],
            "ppg": r["ppg"], "record": d["record"], "tier": d["tier"],
            "share": d["share"], "team_count": r.get("team_count"),
            "mine": mine,
            "you": bool(mine and team_id and r["team_id"] == str(team_id)),
            "label": (own.get("team_name") or "") if own else describe(r, cap=True),
            "sub": (own.get("manager") or "") if own else "",
        })
    return {"teams": n, "total": int(raw.get("total") or 0), "me": raw.get("me"),
            "size": size, "offset": int(offset), "rows": rows,
            "sizes": size_chips(raw.get("sizes") or [], n)}
