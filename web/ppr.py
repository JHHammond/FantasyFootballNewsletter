"""
One scoring system for every league: what each lineup would have scored in
plain PPR.

A 130 in a standard league and a 130 in a PPR league are not the same week,
and a 6-point-passing-TD league inflates every quarterback. So "the highest
score in America" means nothing until every team is scored the same way.

HOW

  * Sleeper publishes every player's real stat line for a week, already
    scored three ways (pts_ppr, pts_half_ppr, pts_std). That is the one
    yardstick: a player's standardized score is his Sleeper pts_ppr,
    whatever platform his fantasy team is on.
  * ESPN and Yahoo players are matched to Sleeper through the ids Sleeper
    keeps for them (espn_id, yahoo_id), then by name and position, then by
    name, position and NFL team. Defenses match by team.
  * A team's standardized score is the sum of its STARTERS. A starter we
    can't match counts at his league's own points, and the share we could
    match is kept as `ppr_coverage`, so a page can say how solid a number is.
  * Matched players are stored under their Sleeper id, which is what makes
    "the most-started player in the country" one row per player rather than
    "Ja'Marr Chase" on Sleeper and "J. Chase" on Yahoo.

What this is NOT: a recalculation of each league's own rules. IDP starters
(DL/LB/DB) are left out, since plain PPR has no defenders. A superflex or a
two-flex lineup still starts more players than a standard one; that's a real
difference between leagues, and it shows up as one.

Never raises to a caller that matters: no context means no standardized
numbers, and every page and paper works without them.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
STATS_URL = "https://api.sleeper.app/stats/nfl/{season}/{week}"
STATS_URL_OLD = "https://api.sleeper.app/v1/stats/nfl/regular/{season}/{week}"
TIMEOUT = 30
#: Sleeper asks for the player blob at most once a day.
TTL_INDEX = 24 * 3600
#: A finished week's stats barely move (the odd stat correction), so a day.
TTL_STATS = 24 * 3600

IDP = frozenset({"DL", "LB", "DB", "DE", "DT", "CB", "S", "IDP"})
NOT_STARTED = frozenset({"BN", "IR", "TAXI", "RES", "NA"})

#: Every platform's spelling of a team -> Sleeper's.
TEAM_ALIASES = {"WSH": "WAS", "JAC": "JAX", "LA": "LAR", "STL": "LAR",
                "OAK": "LV", "LVR": "LV", "SD": "LAC", "GBP": "GB",
                "KCC": "KC", "NEP": "NE", "NOS": "NO", "SFO": "SF",
                "TBB": "TB", "ARZ": "ARI", "HST": "HOU", "BLT": "BAL",
                "CLV": "CLE"}

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def team(abbr: Optional[str]) -> Optional[str]:
    if not abbr:
        return None
    a = str(abbr).strip().upper()
    return TEAM_ALIASES.get(a, a)


def norm_name(name: Optional[str]) -> str:
    n = (name or "").lower()
    n = re.sub(r"[^a-z ]", "", n.replace("-", " "))
    n = _SUFFIX.sub("", n)
    return " ".join(n.split())


# ---------------------------------------------------------------------------
# The two downloads
# ---------------------------------------------------------------------------

def _cache():
    from providers.cache import TTLCache
    return TTLCache(namespace="ppr")


def build_index(raw: dict) -> dict:
    """Sleeper's player blob -> just what matching needs."""
    players: dict[str, list] = {}
    espn: dict[str, str] = {}
    yahoo: dict[str, str] = {}
    by_name: dict[str, list[str]] = {}
    for sid, p in (raw or {}).items():
        if not isinstance(p, dict):
            continue
        sid = str(sid)
        pos = p.get("position") or ((p.get("fantasy_positions") or [None])[0])
        if pos not in ("QB", "RB", "WR", "TE", "K", "DEF"):
            continue
        if pos == "DEF":
            name = f"{p.get('last_name') or sid} D/ST"
        else:
            name = (p.get("full_name")
                    or f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip()
                    or sid)
        players[sid] = [name, pos, team(p.get("team"))]
        if p.get("espn_id"):
            espn[str(p["espn_id"])] = sid
        if p.get("yahoo_id"):
            yahoo[str(p["yahoo_id"])] = sid
        if pos != "DEF":
            by_name.setdefault(f"{norm_name(name)}|{pos}", []).append(sid)
    return {"players": players, "espn": espn, "yahoo": yahoo, "name": by_name}


def _stats_from(raw) -> dict[str, list]:
    """Either shape Sleeper returns -> {sleeper_id: [ppr, half, std]}."""
    out: dict[str, list] = {}
    if isinstance(raw, list):
        items = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            stats = item.get("stats") or {}
            pid = item.get("player_id") or stats.get("player_id")
            items.append((pid, {**item, **stats}))
    elif isinstance(raw, dict):
        items = list(raw.items())
    else:
        return out
    for pid, s in items:
        if not pid or not isinstance(s, dict):
            continue
        vals = [s.get("pts_ppr"), s.get("pts_half_ppr"), s.get("pts_std")]
        if all(v is None for v in vals):
            continue
        out[str(pid)] = [round(float(v), 2) if v is not None else None for v in vals]
    return out


def _get(url, params=None):
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_index() -> dict:
    return _cache().get_or_fetch("index:v1", TTL_INDEX,
                                 lambda: build_index(_get(PLAYERS_URL)))


def fetch_stats(season: int, week: int) -> dict:
    def fetch():
        try:
            got = _stats_from(_get(STATS_URL.format(season=season, week=week),
                                   {"season_type": "regular"}))
        except Exception:  # noqa: BLE001 — try the older address
            got = {}
        if not got:
            got = _stats_from(_get(STATS_URL_OLD.format(season=season, week=week)))
        return got
    return _cache().get_or_fetch(f"stats:{season}:{week}", TTL_STATS, fetch)


# ---------------------------------------------------------------------------
# Context: one per week, shared by every league in a run
# ---------------------------------------------------------------------------

class Context:
    def __init__(self, index: dict, stats: dict):
        self.players = index.get("players") or {}
        self.espn = index.get("espn") or {}
        self.yahoo = index.get("yahoo") or {}
        self.by_name = index.get("name") or {}
        self.stats = stats or {}
        self._defs = {p[2]: sid for sid, p in self.players.items()
                      if p[1] == "DEF" and p[2]}

    # -- matching ----------------------------------------------------------

    def resolve(self, p) -> Optional[str]:
        """A provider's PlayerLine -> Sleeper id, or None."""
        pid = str(getattr(p, "player_id", "") or "")
        if not pid:
            return None
        provider, _, raw = pid.partition(":")
        pos = getattr(p, "position", None)
        if provider == "sleeper":
            return raw if raw in self.players or raw in self.stats else None
        if pos == "DEF":
            return self._defs.get(team(getattr(p, "nfl_team", None)))
        if provider == "espn" and raw in self.espn:
            return self.espn[raw]
        if provider == "yahoo":
            key = raw.rsplit(".", 1)[-1]
            if key in self.yahoo:
                return self.yahoo[key]
        found = self.by_name.get(f"{norm_name(getattr(p, 'name', ''))}|{pos}") or []
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            nfl = team(getattr(p, "nfl_team", None))
            same = [s for s in found if self.players.get(s, [None, None, None])[2] == nfl]
            if len(same) == 1:
                return same[0]
        return None

    def ppr(self, sid: Optional[str]) -> Optional[float]:
        """Standard PPR points for a matched player. A matched player with
        no stat line didn't play: zero."""
        if not sid:
            return None
        line = self.stats.get(sid)
        if line is None:
            return 0.0
        return line[0] if line[0] is not None else 0.0

    def name(self, sid: str) -> Optional[list]:
        return self.players.get(sid)

    # -- a team ------------------------------------------------------------

    def team_score(self, team_obj) -> tuple[Optional[float], Optional[float]]:
        """(standardized points, share of starters matched)."""
        starters = [p for p in (team_obj.lineup or [])
                    if getattr(p, "player_id", "") and p.position not in IDP
                    and p.slot not in NOT_STARTED]
        if not starters:
            return None, None
        total, matched = 0.0, 0
        for p in starters:
            sid = self.resolve(p)
            if sid:
                matched += 1
                total += self.ppr(sid) or 0.0
            else:
                total += float(p.points or 0)
        return round(total, 2), round(matched / len(starters), 3)


_ctx_lock = threading.Lock()
_ctx_memo: dict = {}


def context(season: int, week: int) -> Optional[Context]:
    """The week's Context, or None if Sleeper couldn't be reached. Held in
    memory for an hour so a 1,400-league run builds it once."""
    key = (int(season), int(week))
    with _ctx_lock:
        hit = _ctx_memo.get(key)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
    try:
        index, stats = fetch_index(), fetch_stats(int(season), int(week))
    except Exception as exc:  # noqa: BLE001
        print(f"[ppr] no standardized scores for {season} week {week}: {exc}",
              flush=True)
        return None
    if not index or not stats:
        print(f"[ppr] Sleeper returned no {'players' if not index else 'stats'} "
              f"for {season} week {week}; standardized scores skipped.", flush=True)
        return None
    ctx = Context(index, stats)
    with _ctx_lock:
        _ctx_memo[key] = (time.time(), ctx)
    return ctx
