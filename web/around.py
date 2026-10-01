"""
Around the Leagues: everything the page draws, worked out here so the
template only lays it out.

Two database calls (around_week, around_season; migration 029), cached for
ten minutes, turned into figures, boards and chart geometry. Charts are
drawn as inline SVG from these numbers: no chart library, nothing loaded
from anywhere, and they print.

PUBLIC vs STAFF: the page is staff-only today. `public=True` (or
?preview=public) renders it the way readers would see it: no team or
manager names, only descriptions ("a 12-team PPR league on Sleeper").
"""

from __future__ import annotations

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


def _cached(key, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


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
    return {
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
        "players": players,
        "season_data": ss,
        "season_line": line(weeks),
        "tracker": ss.get("tracker") or {},
        "min_group": MIN_GROUP,
    }
