"""
Around the Leagues: everything the page draws, worked out here so the
template only lays it out.

Two database calls (around_week, around_season; migration 029), cached for
ten minutes, turned into figures, boards and chart geometry. Charts are
drawn as inline SVG from these numbers: no chart library, nothing loaded
from anywhere, and they print.

PUBLIC vs STAFF: /around is the public page (open once AROUND_PUBLIC=1
is set on Render; staff can always see it). /staff/around is the same page
with team names in dashed red tags and the collector at the bottom. The
public page never names a team or a manager, only describes one ("a
12-team PPR league on Sleeper").

THE FRONT (21 Oct): the page used to open on a pile of ten-row lists. It now
opens like a newspaper: a banner headline written from the week's most
striking number, four short stories, and three players with their pictures.
All of it is picked here from figures the page already had, so nothing new
is queried and nothing is invented: every sentence is one of the numbers
below, said out loud.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

#: A group (Yahoo, 14-team leagues...) smaller than this isn't shown: an
#: average of 40 teams is noise wearing a decimal point.
MIN_GROUP = 100
#: A player needs this many starts in a week for his win rate to count.
MIN_STARTS = 150
CACHE_SECONDS = 600

FORMAT_LABEL = {"ppr": "PPR", "half_ppr": "half-PPR", "std": "standard",
                None: "custom-scoring", "unknown": "custom-scoring"}
FORMAT_TITLE = {"ppr": "PPR", "half_ppr": "Half-PPR", "std": "Standard"}
PLATFORM = {"sleeper": "Sleeper", "espn": "ESPN", "yahoo": "Yahoo"}
SIZE_LABEL = {"8-": "8 teams or fewer", "10": "10 teams", "12": "12 teams",
              "14": "14 teams", "16+": "16 or more"}

_cache: dict = {}


def clear_cache() -> None:
    _cache.clear()


#: After a failed refresh, how long to wait before the next try.
RETRY_SECONDS = 120
_locks: dict = {}
_retry_at: dict = {}


def swr(store: dict, key, fn, ttl: float = CACHE_SECONDS):
    """Cached, for pages many people load at once (5 Oct: the homepage).

    The old cache had two failure modes under load. When an entry expired,
    every request in that moment ran the same national query at once; and
    when that query timed out, nothing was stored, so every request after it
    ran it again. The Postgres log showed dozens of statement timeouts a
    minute, in bursts ten minutes apart.

    Now: one request refreshes and the rest are served the last good value;
    a failure keeps that value and waits RETRY_SECONDS before trying again.
    With nothing stored yet, the first request computes and the others wait
    for it rather than start their own.
    """
    now = time.time()
    hit = store.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if _retry_at.get(key, 0) > now:
        if hit:
            return hit[1]
        raise RuntimeError("cooling off after a failed refresh")
    lock = _locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        if hit:
            return hit[1]
        with lock:
            pass
        fresh = store.get(key)
        if fresh:
            return fresh[1]
        raise RuntimeError("refresh failed")
    try:
        value = fn()
        store[key] = (time.time(), value)
        _retry_at.pop(key, None)
        return value
    except Exception:
        _retry_at[key] = time.time() + RETRY_SECONDS
        if hit:
            return hit[1]
        raise
    finally:
        lock.release()


def _cached(key, fn):
    return swr(_cache, key, fn)


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------

def describe(r: dict, cap: bool = False) -> str:
    """'a 12-team PPR league on Sleeper' — never a name. `cap` starts it
    with a capital, leaving "PPR" and "ESPN" alone (Jinja's |capitalize
    would lowercase them)."""
    size = r.get("team_count")
    fmt = FORMAT_LABEL.get(r.get("scoring_type"), "custom-scoring")
    where = PLATFORM.get(r.get("provider"), (r.get("provider") or "").title())
    lead = f"{size}-team " if size else ""
    article = "an" if (lead or fmt)[:1] in "8aeiou" or (size in (8, 11, 18)) else "a"
    out = f"{article} {lead}{fmt} league on {where}".strip()
    return out[:1].upper() + out[1:] if cap else out


def one_in(part: int, whole: int) -> Optional[str]:
    """6,000 of 31,000 -> '1 in 5'."""
    if not part or not whole:
        return None
    n = round(whole / part)
    return f"1 in {n}" if n >= 2 else None


def share(part: int, whole: int) -> Optional[str]:
    """'1 in 6' while that reads well; past one in two, a percentage."""
    if not part or not whole:
        return None
    return one_in(part, whole) or pct(part / whole)


def pct(x: Optional[float], digits: int = 0) -> str:
    if x is None:
        return "–"
    return f"{x * 100:.{digits}f}%"


# ---------------------------------------------------------------------------
# Where a score ranks
# ---------------------------------------------------------------------------

#: The ladder the readout and the share card both read from: the lowest
#: percentile that earns each verdict, best first.
VERDICTS = [
    (99, "One of the best in the country.", "TOP 1%"),
    (90, "A top-10% week.", "TOP 10%"),
    (75, "A strong week.", "TOP 25%"),
    (50, "Better than most.", "ABOVE PAR"),
    (25, "Below the middle.", "BELOW PAR"),
    (10, "A rough one.", "BOTTOM 25%"),
    (0, "One of the worst in the country.", "BOTTOM 10%"),
]


def verdict(p: int) -> tuple[str, str]:
    """(the sentence, the stamp) for a percentile."""
    for floor, words, stamp in VERDICTS:
        if p >= floor:
            return words, stamp
    return VERDICTS[-1][1], VERDICTS[-1][2]


def ordinal(n: int) -> str:
    """728 -> '728th'."""
    if 11 <= (n % 100) <= 13:
        return f"{n:,}th"
    return f"{n:,}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _below(arr: list, v: float) -> float:
    """The share of teams scoring less than v, read off a 101-point
    percentile array. The twin of below() in the page's script: the page
    answers as you type and the share card answers on the server, and the
    two must agree."""
    if not arr:
        return 0.0
    if v <= arr[0]:
        return 0.0
    if v >= arr[-1]:
        return 1.0
    for i in range(1, len(arr)):
        if v <= arr[i]:
            span = arr[i] - arr[i - 1]
            f = (v - arr[i - 1]) / span if span > 0 else 0.0
            return (i - 1 + f) / (len(arr) - 1)
    return 1.0


def rank(pct: dict, score: float, fmt: str = "all") -> Optional[dict[str, Any]]:
    """Where one score lands among the week's teams, or None if that pool
    wasn't collected. `place` counts the teams at or above it, so the best
    score in the country is 1st."""
    arr = (pct or {}).get(fmt)
    teams = int((pct or {}).get(f"n_{fmt}") or 0)
    if not arr or not teams:
        return None
    share = _below(arr, float(score))
    beat = round(share * teams)
    p = round(share * 100)
    words, stamp = verdict(p)
    return {"score": float(score), "pct": p, "beat": beat, "teams": teams,
            "place": max(1, teams - beat), "verdict": words, "stamp": stamp}


def my_teams(db, user: Optional[dict], season: int, week: int) -> list[dict[str, Any]]:
    """The signed-in viewer's own teams for this week, so the lookup can
    fill itself in instead of asking them to go and read their score off
    another site.

    Their leagues only, and their own teams' names — this is the one place
    on the page where a team is named, and it is named to the person who
    owns it. Never raises: the lookup still takes a typed score.
    """
    if not user:
        return []
    try:
        leagues = [l for l in db.leagues_for_user(user["id"])
                   if int(l.get("season") or 0) == int(season)][:20]
        if not leagues:
            return []
        by_id = {str(l["id"]): l for l in leagues}
        rows = db.team_weeks_for_leagues(season, week, list(by_id)) or []
    except Exception as exc:  # noqa: BLE001 — a convenience is never worth the page
        print(f"[around] no connected teams for the lookup: {exc}", flush=True)
        return []

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        lg = by_id.get(str(r.get("league_id")))
        if lg is None or (r.get("points") or 0) <= 0:
            continue
        e = out.setdefault(str(lg["id"]), {
            #: The id rides along so the "your lineup" panel can ask for
            #: one team's roster; ownership is re-checked server-side.
            "id": str(lg["id"]),
            "league": lg.get("paper_name") or lg.get("league_name") or "Your league",
            "scoring": (r.get("scoring_type") or ""), "teams": []})
        e["teams"].append({
            "id": str(r.get("team_id")),
            "name": (r.get("team_name") or "").strip() or "Unnamed team",
            "points": round(float(r["points"]), 2),
            "ppr": round(float(r["ppr_points"]), 2) if r.get("ppr_points") is not None else None,
            "mine": str(r.get("team_id")) == str(lg.get("my_team_id") or ""),
        })
    for e in out.values():
        # Their own team first when we know it (it's saved at connect time),
        # then the rest of the league alphabetically.
        e["teams"].sort(key=lambda t: (not t["mine"], t["name"].lower()))
    return [e for e in out.values() if e["teams"]]


#: What each pool is called in a sentence.
POOL = {"all": "fantasy teams", "ppr": "PPR teams", "half_ppr": "half-PPR teams",
        "std": "standard-scoring teams"}


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def histogram(hist: list[dict], width: int = 720, height: int = 210) -> dict:
    """Columns for the score distribution, in viewBox units."""
    if not hist:
        return {}
    bins = {h["lo"]: h["n"] for h in hist}
    lo_bin, hi_bin = min(bins), max(bins)
    keys = list(range(lo_bin, hi_bin + 10, 10))
    top = max(bins.values())
    pad_l, pad_b, pad_t = 6, 26, 10
    plot_w, plot_h = width - pad_l * 2, height - pad_b - pad_t
    step = plot_w / len(keys)
    bar_w = min(24, step - 2)
    cols = []
    for i, k in enumerate(keys):
        n = bins.get(k, 0)
        h = (n / top) * plot_h if top else 0
        x = pad_l + i * step + (step - bar_w) / 2
        label = f"{k}+" if k == 250 else f"{k}–{k + 10}"
        cols.append({"x": round(x, 1), "y": round(pad_t + plot_h - h, 1),
                     "w": round(bar_w, 1), "h": round(h, 1), "n": n, "lo": k,
                     "label": label, "tick": k % 50 == 0})
    return {"cols": cols, "width": width, "height": height,
            "base": pad_t + plot_h, "lo": lo_bin, "hi": hi_bin + 10,
            "x0": pad_l, "plot_w": plot_w}


def line(weeks: list[dict], key: str = "avg_ppr", width: int = 720,
         height: int = 200) -> dict:
    pts = [(w["week"], w.get(key)) for w in weeks if w.get(key) is not None]
    if not pts:
        return {}
    vals = [v for _, v in pts]
    lo = (int(min(vals)) // 10) * 10 - 10
    hi = (int(max(vals)) // 10) * 10 + 20
    pad_l, pad_r, pad_t, pad_b = 40, 24, 14, 28
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(pts)
    xs = lambda i: pad_l + (pw * (i / (n - 1)) if n > 1 else pw / 2)  # noqa: E731
    ys = lambda v: pad_t + ph * (1 - (v - lo) / (hi - lo))  # noqa: E731
    dots = [{"x": round(xs(i), 1), "y": round(ys(v), 1), "week": wk, "v": v}
            for i, (wk, v) in enumerate(pts)]
    grid = [{"y": round(ys(g), 1), "v": g} for g in range(lo, hi + 1, 10)
            if (g - lo) % (20 if hi - lo > 60 else 10) == 0]
    return {"dots": dots, "path": " ".join(f"{'M' if i == 0 else 'L'}{d['x']},{d['y']}"
                                           for i, d in enumerate(dots)),
            "grid": grid, "width": width, "height": height,
            "base": pad_t + ph, "left": pad_l, "right": width - pad_r}


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def _groups(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"provider": [], "scoring": [], "size": []}
    hidden: dict[str, int] = {"provider": 0, "scoring": 0, "size": 0}
    for g in rows:
        if g["dim"] not in out:
            continue
        if g["n"] < MIN_GROUP:
            hidden[g["dim"]] += 1
            continue
        label = (PLATFORM.get(g["key"], g["key"]) if g["dim"] == "provider"
                 else FORMAT_TITLE.get(g["key"], "Custom scoring")
                 if g["dim"] == "scoring" else SIZE_LABEL.get(g["key"], g["key"]))
        out[g["dim"]].append(dict(g, label=label))
    order = {"8-": 0, "10": 1, "12": 2, "14": 3, "16+": 4}
    out["size"].sort(key=lambda g: order.get(g["key"], 9))
    for dim, gs in out.items():
        top = max((g["avg_ppr"] or 0 for g in gs), default=0)
        bench = max((g["avg_bench"] or 0 for g in gs), default=0)
        for g in gs:
            g["ppr_w"] = round(100 * (g["avg_ppr"] or 0) / top, 1) if top else 0
            g["bench_w"] = round(100 * (g["avg_bench"] or 0) / bench, 1) if bench else 0
        if gs:
            best = max(gs, key=lambda g: g["avg_ppr"] or 0)
            best["leader"] = True
    return {"groups": out, "hidden": hidden}


# ---------------------------------------------------------------------------
# The front: a headline, four stories and three players
# ---------------------------------------------------------------------------

#: A flop only makes the banner when this share of the week's lineups
#: started him: "312 teams started him" is a list entry, not a headline.
HEADLINE_FLOP_SHARE = 0.15
#: A losing score this high (PPR) is a headline on its own.
HEADLINE_HEARTBREAK = 160.0


def _num(v: Optional[float]) -> str:
    return f"{float(v):.1f}" if v is not None else "–"


def _std(r: dict) -> Optional[float]:
    return r.get("ppr_points") if r.get("ppr_points") is not None else r.get("points")


def _pronoun(p: dict) -> str:
    return "They" if p.get("position") == "DEF" else "He"


def _skill(rows: list[dict]) -> Optional[dict]:
    """The first player who isn't a kicker or a defense, else the first.
    Everybody starts a kicker; 'everybody started Tyler Bass' is true every
    week and news in none of them."""
    return next((p for p in rows if p.get("position") not in ("K", "DEF")),
                rows[0] if rows else None)


def photo(p: Optional[dict]) -> Optional[str]:
    if not p:
        return None
    from .luck import photo_url
    return photo_url(p.get("player_key"), p.get("position"))


def front(a: dict) -> dict[str, Any]:
    """The banner headline, its deck, the four stories and the three player
    cards. Every piece is optional; the template leaves out what's missing."""
    P = a.get("players") or {}
    halls = a.get("halls") or {}
    lu = a.get("lineup") or {}
    lineup_teams = (a.get("summary") or {}).get("lineup_teams") or 0

    flop = _skill(P.get("flops") or [])
    sleeper = (P.get("sleepers") or [None])[0]
    carry = (P.get("top_on") or [None])[0]
    heart = (a.get("unlucky") or [None])[0]
    robbery = (a.get("lucky") or [None])[0]
    bench = (halls.get("bench") or [None])[0]
    blowout = (halls.get("blowouts") or [None])[0]

    # The banner: the first of these that's big enough to shout about.
    head = deck = None
    if flop and lineup_teams and flop["started"] >= HEADLINE_FLOP_SHARE * lineup_teams:
        head = (f"{flop['started']:,} Teams Started {flop['name']}. "
                f"{_pronoun(flop)} Scored {_num(flop['ppr'])}.")
        deck = "The rest of the week, across every league we follow, wasn't much kinder."
    elif heart and (_std(heart) or 0) >= HEADLINE_HEARTBREAK:
        head = f"{_num(_std(heart))} Points. Still Lost."
        deck = (f"{describe(heart, True)} put up one of the best scores in the country "
                f"and drew the one opponent who did better.")
    elif lu.get("lineup_losses"):
        head = (f"{lu['lineup_losses']:,} Teams Lost With the Winning Lineup "
                f"Sitting on Their Bench")
        deck = "Every one of them had the points. They just didn't start them."
    elif a.get("teams"):
        head = f"{a['teams']:,} Teams. One Scoreboard."

    stories = []
    if heart:
        stories.append({
            "kicker": "The heartbreaker", "big": _num(_std(heart)), "unit": "points",
            "head": "The best score that lost",
            "body": (f"{describe(heart, True)} scored {_num(heart.get('points'))} and lost "
                     f"to {_num(heart.get('opponent_points'))}."),
            "row": heart})
    if robbery:
        stories.append({
            "kicker": "Highway robbery", "big": _num(_std(robbery)), "unit": "points",
            "head": "The worst score that won",
            "body": (f"{describe(robbery, True)} won {_num(robbery.get('points'))}"
                     f"–{_num(robbery.get('opponent_points'))}. Nobody asked how."),
            "row": robbery})
    if bench and bench.get("bench_left"):
        who = bench.get("best_bench_player")
        stories.append({
            "kicker": "The bench disaster", "big": _num(bench["bench_left"]),
            "unit": "points on the bench",
            "head": "Points left sitting down",
            "body": (f"{describe(bench, True)} scored {_num(bench.get('points'))}"
                     + (f" with {who} on the bench." if who else
                        " and could have had far more.")),
            "row": bench})
    if blowout and blowout.get("margin"):
        stories.append({
            "kicker": "The beatdown", "big": _num(blowout["margin"]),
            "unit": "point margin",
            "head": "The biggest blowout",
            "body": (f"{describe(blowout, True)} won {_num(blowout.get('points'))}"
                     f"–{_num(blowout.get('opponent_points'))}."),
            "row": blowout})

    # The cards lead with the player's photo, so the figures live inside the
    # sentence rather than as a number over it (John, 21 Oct).
    stars = []
    if carry:
        stars.append({"kicker": "Carried the most teams", "p": carry,
                      "line": (f"The top scorer on {carry['teams']:,} teams this week, "
                               f"more lineups than anyone else in the country.")})
    if flop:
        stars.append({"kicker": "The bust", "p": flop,
                      "line": (f"{_pronoun(flop)} started on {flop['started']:,} teams "
                               f"and scored {_num(flop['ppr'])}.")})
    if sleeper:
        sat = pct(1 - (sleeper.get("start_pct") or 0))
        stars.append({"kicker": "The one nobody started", "p": sleeper,
                      "line": (f"{_pronoun(sleeper)} scored {_num(sleeper['ppr'])} "
                               f"on {sleeper['rostered']:,} rosters, and {sat} of them "
                               f"left {'them' if sleeper.get('position') == 'DEF' else 'him'} "
                               f"on the bench.")})
    return {"headline": head, "deck": deck, "stories": stories, "stars": stars}


def faceoff_deltas(faceoffs: dict, avg: Optional[float]) -> None:
    """Each group's average against the national one (+1.2, -0.8): the
    groups sit within a few points of each other, which bars from zero
    can't show and a signed number can."""
    if avg is None:
        return
    for gs in (faceoffs.get("groups") or {}).values():
        for g in gs:
            if g.get("avg_ppr") is not None:
                g["delta"] = round(g["avg_ppr"] - avg, 1)


def build(db, season: int, week: int, *, public: bool = False) -> dict[str, Any]:
    wk = _cached(("w", season, week),
                 lambda: db.around_week(season, week, MIN_STARTS)) or {}
    ss = _cached(("s", season), lambda: db.around_season(season)) or {}
    s = wk.get("summary") or {}
    lu = wk.get("lineup") or {}
    players = wk.get("players") or {}
    teams = s.get("teams") or 0
    compared = s.get("compared") if s.get("compared") is not None else teams

    lineup_share = share(lu.get("lineup_losses") or 0, lu.get("losses") or 0)
    has_lineups = bool(s.get("lineup_teams"))
    standardized = bool(s.get("standardized"))

    figures = []
    if teams:
        figures = [
            {"value": f"{teams:,}", "label": "teams scored"},
            {"value": f"{s.get('leagues') or 0:,}", "label": "leagues"},
            {"value": f"{s.get('median_ppr') or 0:.1f}",
             "label": "median score, PPR" if standardized else "median score"},
        ]
        if lineup_share:
            figures.append({"value": lineup_share, "label": "losses were lineup losses"})

    weeks = ss.get("weeks") or []
    out = {
        "public": public,
        "season": season, "week": week,
        "teams": teams,
        "compared": compared,
        "summary": s,
        "standardized": standardized,
        "coverage": s.get("coverage"),
        "has_lineups": has_lineups,
        "figures": figures,
        "hist": histogram(wk.get("hist") or []),
        "pct": wk.get("pct") or {},
        "lineup": lu,
        "lineup_share": lineup_share,
        "perfect_share": (lu.get("perfect") or 0) / lu["with_optimal"]
                         if lu.get("with_optimal") else None,
        "halls": wk.get("halls") or {},
        "unlucky": wk.get("unlucky") or [],
        "lucky": wk.get("lucky") or [],
        "faceoffs": _groups(wk.get("groups") or []),
        # The ten-a-board lists, but NOT the four hundred behind them: the
        # full list is fetched from /around/players.json by a signed-in
        # reader. Shipping it in the page made the gate a decoration and
        # cost every visitor the weight of a list most never opened.
        "players": {k: v for k, v in players.items() if k != "all"},
        #: The size of what is behind the sign-in, which is worth showing.
        "player_count": len(players.get("all") or []),
        "season_data": ss,
        "season_line": line(weeks),
        "tracker": ss.get("tracker") or {},
        "min_group": MIN_GROUP,
    }
    faceoff_deltas(out["faceoffs"], s.get("avg_ppr"))
    out["front"] = front(out)
    return out


def week_json(db, season: int, week: int) -> dict:
    """The raw week, through the same cache the page uses.

    One door to around_week, so the player endpoint and the page cannot
    disagree about what this week was, and so a reader opening the full
    board does not set a second copy of a 0.9-second query running.
    """
    return _cached(("w", season, week, MIN_STARTS),
                   lambda: db.around_week(season, week, MIN_STARTS)) or {}


# ---------------------------------------------------------------------------
# Your own lineup, against the country
# ---------------------------------------------------------------------------

#: A player this many teams started is one "everybody" had. Below it he was
#: a choice somebody made rather than the obvious play.
COMMON_SHARE = 0.40
#: ...and this few makes him genuinely yours.
RARE_SHARE = 0.05


def my_lineup(db, user: Optional[dict], season: int, week: int,
              league_id: str, team_id: str) -> dict[str, Any]:
    """One of the viewer's own teams, set against what the country did.

    The national page is about 14,000 strangers, which is interesting once
    and then abstract. This is the same week read back through the only
    lineup the reader actually cares about: who else started these men,
    what they scored, and which of them nobody else had.

    OWNERSHIP IS CHECKED HERE and nowhere else. The league must be one of
    this user's or the answer is empty — a team id is guessable and a
    lineup is somebody's private roster. Never raises: a broken panel must
    not cost the page.
    """
    if not user or not league_id or not team_id:
        return {}
    try:
        mine = {str(l["id"]): l for l in db.leagues_for_user(user["id"])
                if int(l.get("season") or 0) == int(season)}
        league = mine.get(str(league_id))
        if league is None:
            return {}
        rows = db.lineup_for_team(league_id, season, week, team_id) or []
    except Exception as exc:  # noqa: BLE001
        print(f"[around] no lineup for the viewer: {exc}", flush=True)
        return {}
    if not rows:
        return {}

    try:
        wk = week_json(db, season, week)
    except Exception:  # noqa: BLE001
        wk = {}
    players = wk.get("players") or {}
    national = {p["player_key"]: p for p in players.get("all") or []}
    # How many lineups there were to be one of. summary.lineup_teams, not
    # summary.teams: a team with no stored lineup never started anybody.
    lineups = int((wk.get("summary") or {}).get("lineup_teams") or 0)

    started, benched = [], []
    for r in rows:
        key = str(r.get("player_key") or "")
        card = national.get(key) or {}
        ppr = r.get("ppr_points")
        ppr = float(ppr) if ppr is not None else None
        n = int(card.get("started") or 0)
        share_started = (n / lineups) if lineups else None
        row = {
            "player_key": key,
            "name": card.get("name") or key,
            "position": card.get("position") or (r.get("slot") or None),
            "nfl_team": card.get("nfl_team"),
            "slot": r.get("slot"),
            "points": round(float(r.get("points") or 0), 2),
            "ppr": round(ppr, 2) if ppr is not None else None,
            #: How much of the country started him, which is the whole
            #: point of reading your own lineup against a national page.
            "started_by": n or None,
            "share": round(share_started, 4) if share_started else None,
            "common": bool(share_started and share_started >= COMMON_SHARE),
            "rare": bool(share_started is not None and 0 < share_started <= RARE_SHARE),
            "known": bool(card),
        }
        (started if r.get("started") else benched).append(row)

    started.sort(key=lambda p: -(p["ppr"] if p["ppr"] is not None else p["points"]))
    benched.sort(key=lambda p: -(p["ppr"] if p["ppr"] is not None else p["points"]))

    scored = [p for p in started if p["ppr"] is not None]
    best_bench = next((p for p in benched if p["ppr"] is not None), None)
    worst = scored[-1] if scored else None
    return {
        "league": league.get("paper_name") or league.get("league_name") or "Your league",
        "started": started,
        "bench": benched[:6],
        "lineups": lineups,
        #: The one line worth leading with: a player almost nobody else had.
        "differential": next((p for p in scored if p["rare"]), None),
        "best": scored[0] if scored else None,
        "worst": worst,
        #: Did somebody on the bench outscore your worst starter? The page
        #: already says this nationally; here it has a name attached.
        "regret": (best_bench if best_bench and worst
                   and best_bench["ppr"] > (worst["ppr"] or 0) else None),
    }
