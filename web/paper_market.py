"""DRAFT REGRETS, PRICED ON THE TRADE MARKET (26 Oct 2026).

John: "could we merge regrets and steals into the trade market cross
promotion? Maybe it lists three steals and 3 regrets and then has their
chart below them?"

The Draft Regrets section already names three steals and three regrets.
This adds what the trade market says each of them costs today, and each
one's price line from draft day to now, so the section ends on "See the
whole chart" and a reader who never signs in has a reason to open /market.

* The market's prices and lines are the same cached build /market serves,
  in the league's own market (dynasty board for dynasty leagues; but Draft
  Regrets only runs in redraft leagues, so in practice redraft).
* A market line's first point is where the COUNTRY drafted him (day 0 is
  "the draft"; see trade_rating.daily), so a steal's line climbs from his
  national draft price to his price now. The section's own numbers ("drafted
  as the WR28, now the WR5") stay this league's.
* A player is matched to the market by his market key: his Sleeper id
  (Sleeper leagues), or ESPN/Yahoo ids resolved to Sleeper ids the same way
  league_stats.lineup_rows does it. An unpriced player keeps his card and
  simply has no line.
* FROZEN into the paper inside the draft regrets snapshot (regrets["market"])
  when the paper is generated, like everything else in it.

Switched on per paper by PAPER_MARKET (Render, or .env locally):

    PAPER_MARKET=all                      every paper with Draft Regrets
    PAPER_MARKET=slug-one,slug-two        only these leagues' papers
    (unset)                               off: Draft Regrets prints as before

Garnish: any failure is logged and Draft Regrets prints without prices.
"""

from __future__ import annotations

import os
import time
from typing import Optional
from urllib.parse import quote

#: Where the prices travel inside the frozen draft regrets.
KEY = "market"


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------

def enabled(league: Optional[dict]) -> bool:
    """PAPER_MARKET: "all" (or "1") for every paper, else a comma-separated
    list of public slugs. Unset means off."""
    raw = (os.getenv("PAPER_MARKET") or "").strip()
    if not raw:
        return False
    if raw.lower() in ("all", "1", "true", "on"):
        return True
    slugs = {s.strip() for s in raw.split(",") if s.strip()}
    return bool(league and league.get("public_slug") in slugs)


# ---------------------------------------------------------------------------
# Pure parts
# ---------------------------------------------------------------------------

def _entries(regrets: Optional[dict]) -> list[dict]:
    return list((regrets or {}).get("steals") or []) + list((regrets or {}).get("regrets") or [])


def price(regrets: dict, rating: Optional[dict], keys: dict[str, str]) -> Optional[dict]:
    """The market block for these steals and regrets, or None if the market
    prices none of them.

    `keys` maps each entry's platform id ("pid") to its market key."""
    daily = (rating or {}).get("daily") or {}
    lines = daily.get("lines") or {}
    last = daily.get("last")
    if last is None:
        return None
    priced = {p["k"]: p for p in (rating or {}).get("pickable") or []}
    players: dict[str, dict] = {}
    for e in _entries(regrets):
        pid = e.get("pid")
        k = keys.get(pid or "")
        p = priced.get(k or "")
        if not p:
            continue
        line = [[int(d), round(float(v), 2)] for d, v in (lines.get(k) or [])]
        players[pid] = {"k": k, "costs": p.get("costs"), "rank": p.get("rank"),
                        "trades": p.get("trades"), "line": line}
    if not players:
        return None
    return {"market": (rating or {}).get("market") or "redraft",
            "base": daily.get("base"), "last": int(last),
            "used": (rating or {}).get("used"), "players": players}


def market_keys(regrets: dict) -> list[str]:
    """Market keys for the button: steals first, then regrets, priced only."""
    block = (regrets or {}).get(KEY) or {}
    out = []
    for e in _entries(regrets):
        p = (block.get("players") or {}).get(e.get("pid") or "")
        if p and p["k"] not in out:
            out.append(p["k"])
    return out


def chart_url(regrets: dict, base_url: str = "") -> str:
    """/market opened on these players. ?p= is the market page's own
    hand-picked set; from=paper tells the visit apart in the logs."""
    block = (regrets or {}).get(KEY) or {}
    market = "dynasty" if block.get("market") == "dynasty" else "redraft"
    keys = ",".join(market_keys(regrets))
    tail = f"&p={quote(keys, safe=',')}" if keys else ""
    return f"{base_url}/market?m={market}{tail}&from=paper"


def player_url(key: str, market: str, base_url: str = "") -> str:
    m = "dynasty" if market == "dynasty" else "redraft"
    return f"{base_url}/market/player/{quote(str(key), safe='')}?m={m}&from=paper"


# ---------------------------------------------------------------------------
# Matching players to the market
# ---------------------------------------------------------------------------

def _lines_by_pid(weeks: list) -> dict[str, object]:
    """{platform player id: his latest PlayerLine} across the season's weeks."""
    out: dict[str, object] = {}
    for wd in sorted((w for w in weeks or [] if w is not None), key=lambda w: w.week):
        for team in getattr(wd, "teams", None) or []:
            for p in list(team.lineup or []) + list(team.bench or []):
                if getattr(p, "player_id", None):
                    out[str(p.player_id)] = p
    return out


def _with_pids(regrets: dict, lines: dict[str, object]) -> None:
    """Papers written before entries carried "pid": match by name and
    position, where that is unambiguous."""
    by_name: dict[tuple, list] = {}
    for pid, p in lines.items():
        by_name.setdefault(((p.name or "").strip().lower(), (p.position or "").upper()), []).append(pid)
    for e in _entries(regrets):
        if e.get("pid"):
            continue
        found = by_name.get(((e.get("player") or "").strip().lower(), (e.get("pos") or "").upper())) or []
        if len(found) == 1:
            e["pid"] = found[0]


def keys_for(regrets: dict, weeks: list, ctx=None) -> dict[str, str]:
    """{platform id: market key} for every steal and regret we can match."""
    lines = _lines_by_pid(weeks)
    _with_pids(regrets, lines)
    out: dict[str, str] = {}
    for e in _entries(regrets):
        pid = str(e.get("pid") or "")
        if not pid:
            continue
        provider, _, raw = pid.partition(":")
        if provider == "sleeper" and raw:
            out[pid] = raw
            continue
        line = lines.get(pid)
        sid = ctx.resolve(line) if (ctx and line is not None) else None
        out[pid] = sid or pid
    return out


# ---------------------------------------------------------------------------
# With a database
# ---------------------------------------------------------------------------

def rating_for(db, season: int, latest: int, market: str) -> Optional[dict]:
    """The market's price list, without a second build when this process
    already holds the one /market uses (cached under ("rating" |
    "rating-dynasty", season, week)); otherwise built and cached here."""
    from . import around, trade_rating
    tag = "rating" if market == "redraft" else "rating-dynasty"
    held = [k for k in list(around._cache)
            if isinstance(k, tuple) and len(k) == 3 and k[:2] == (tag, season)]
    if held:
        hit = around._cache.get(max(held, key=lambda k: k[2]))
        if hit and hit[1]:
            return hit[1]
    return around.swr(
        around._cache, ("paper-" + tag, season, latest),
        lambda: trade_rating.build(db, season, list(range(1, max(latest, 1) + 1)),
                                   market=market) or None,
        ttl=900) or None


def _market_for(db, league: dict, season: int) -> str:
    from . import around
    from .league_kind import league_market
    try:
        dyn = around.swr(around._cache, ("dynasty-ids", season),
                         lambda: db.dynasty_league_ids(season) or set(), ttl=900) or set()
    except Exception:  # noqa: BLE001
        dyn = None
    return league_market(league, dyn)


def attach(db, league: dict, weeks: list, regrets: Optional[dict], *,
           force: bool = False) -> Optional[dict]:
    """Price this paper's steals and regrets and freeze the result into
    `regrets`. Only while the switch is on (or `force`, for the preview).
    Never raises; returns the block or None."""
    if not regrets or (regrets.get(KEY) and not force):
        return (regrets or {}).get(KEY)
    if not (force or enabled(league)):
        return None
    t0 = time.time()
    try:
        import nfl_week
        season = int(league["season"])
        latest = max(nfl_week.completed_week(), 1)
        market = _market_for(db, league, season)
        rating = rating_for(db, season, latest, market)
        if not rating:
            print("[paper market] no market prices yet; skipped", flush=True)
            return None
        ctx = None
        if (league.get("provider") or "sleeper") != "sleeper":
            from . import ppr
            ctx = ppr.context(season, latest)
        block = price(regrets, rating, keys_for(regrets, weeks, ctx))
        if block:
            regrets[KEY] = block
        print(f"[paper market] {league.get('public_slug')}: "
              f"{len((block or {}).get('players') or {})}/{len(_entries(regrets))} "
              f"priced in {time.time() - t0:.1f}s", flush=True)
        return block
    except Exception as exc:  # noqa: BLE001
        print(f"[paper market] skipped: {type(exc).__name__}: {exc}", flush=True)
        return None
