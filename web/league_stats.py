"""
Around the Leagues: one row per team per finished week, across every league.

Each paper is about one league. This is the view across ALL of them: the
lowest score in the country this week, the biggest blowout, the most points
left on a bench. The raw material for a weekly post, and eventually for a box
in every paper ("your 68.4 ranked 9,340th of 14,212").

WHERE ROWS COME FROM

  * The Tuesday job collects every connected league's finished week — not
    just the leagues that write a paper, which are a small fraction of them.
    Stats only: no Claude call, so it costs nothing but platform requests,
    and those are paced (see PACE) so 1,400+ leagues take minutes, not a
    burst that gets us rate limited.
  * Writing a paper for a FINISHED week saves that league's rows as a side
    effect, from data already in memory.
  * `backfill` runs the collector over earlier weeks, once.

Only finished weeks, ever. A paper written on Friday has half a week of
scores, and one of those in the table would win "lowest score in America"
every single week.

Collection is RESUMABLE: a league already collected for a week is skipped,
so a run that dies halfway (a deploy, a timeout) just carries on next time.

PRIVACY: team names are our users' content. This is a staff page; anything
posted publicly describes a team ("a 12-team PPR league") rather than naming
it, unless its owner has said yes.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nfl_week  # noqa: E402

#: Seconds between leagues, per platform. Sleeper allows roughly 1,000
#: requests a minute and a league costs about three; ESPN publishes no limit
#: at all, so it gets the most room.
PACE = {"sleeper": 0.25, "espn": 1.0, "yahoo": 0.5}


def week_is_final(season: int, week: int, now: Optional[datetime] = None) -> bool:
    return (now or datetime.now(timezone.utc)) >= nfl_week.week_final(int(week), int(season))


# ---------------------------------------------------------------------------
# Week -> rows
# ---------------------------------------------------------------------------

def _name(p) -> Optional[str]:
    return p.name if p is not None else None


def rows_from_week(league: dict, week_data, ctx=None,
                   extended: bool = False) -> list[dict[str, Any]]:
    """One row per team. `week_data` should have had the optimizer run
    (load_week does by default) so the bench numbers mean something.

    `extended` once migration 029 is in: adds the standardized PPR score
    (`ctx` is a web.ppr.Context; None leaves those columns empty)."""
    info = week_data.league
    rows: list[dict[str, Any]] = []
    stamp = datetime.now(timezone.utc).isoformat()

    def row(team, opponent) -> dict[str, Any]:
        top = team.top_scorer
        bench = [p for p in team.bench if p.player_id and p.slot == "BN"]
        best_bench = max(bench, key=lambda p: p.points) if bench else None
        if opponent is None:
            result = "BYE"
        elif team.points > opponent.points:
            result = "W"
        elif team.points < opponent.points:
            result = "L"
        else:
            result = "T"
        return {
            "league_id": league["id"],
            "provider": league["provider"],
            "platform_league_id": str(league["platform_league_id"]),
            "season": int(info.season or league["season"]),
            "week": int(week_data.week),
            "team_id": str(team.team_id),
            "team_name": (team.team_name or "")[:120],
            "manager": (team.manager.display_name if team.manager else "")[:120],
            "points": round(float(team.points or 0), 2),
            "opponent_name": (opponent.team_name if opponent else None),
            "opponent_points": round(float(opponent.points), 2) if opponent else None,
            "margin": round(team.points - opponent.points, 2) if opponent else None,
            "result": result,
            "optimal_points": team.optimal_points,
            "bench_left": team.lineup_gap,
            "empty_slots": team.empty_slots,
            "top_player": _name(top),
            "top_player_pos": top.position if top else None,
            "top_player_points": top.points if top else None,
            "best_bench_player": _name(best_bench),
            "best_bench_points": best_bench.points if best_bench else None,
            "team_count": info.team_count or len(week_data.teams),
            "scoring_type": info.scoring_type,
            "collected_at": stamp,
        }

    def full_row(team, opponent) -> dict[str, Any]:
        r = row(team, opponent)
        if extended:
            ppr_pts, coverage = ctx.team_score(team) if ctx else (None, None)
            r["ppr_points"] = ppr_pts
            r["ppr_coverage"] = coverage
        return r

    for m in week_data.matchups:
        a, b = m.teams
        rows.append(full_row(a, b))
        rows.append(full_row(b, a))
    for t in week_data.byes:
        rows.append(full_row(t, None))
    return rows


def _teams(week_data):
    for m in week_data.matchups:
        yield from m.teams
    yield from week_data.byes


def lineup_rows(league: dict, week_data, ctx=None
                ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(one row per rostered player per team, the players they name).

    A player matched to Sleeper is stored under his Sleeper id and Sleeper's
    spelling of his name, so he is one player across all three platforms;
    one we can't match keeps his platform id ("espn:3139477")."""
    from web.ppr import NOT_STARTED
    season = int(week_data.league.season or league["season"])
    week = int(week_data.week)
    out: list[dict[str, Any]] = []
    players: dict[str, dict[str, Any]] = {}
    for team in _teams(week_data):
        seen: set = set()
        starters = {id(p) for p in team.lineup}
        for p in list(team.lineup) + list(team.bench):
            if not p.player_id:
                continue                      # an empty slot
            sid = ctx.resolve(p) if ctx else None
            key = sid or p.player_id
            if key in seen:
                continue
            seen.add(key)
            started = id(p) in starters and p.slot not in NOT_STARTED
            out.append({
                "league_id": league["id"], "season": season, "week": week,
                "team_id": str(team.team_id), "player_key": key,
                "slot": (p.slot or "BN")[:12], "started": started,
                "points": round(float(p.points or 0), 2),
                "ppr_points": ctx.ppr(sid) if (ctx and sid) else None,
            })
            if key not in players:
                known = ctx.name(sid) if (ctx and sid) else None
                name, pos, nfl = known or (p.name, p.position, p.nfl_team)
                players[key] = {"player_key": key, "name": (name or "")[:80],
                                "position": (pos or "")[:8],
                                "nfl_team": (nfl or None)}
    return out, list(players.values())


def save_week(db, league: dict, week_data, ctx=None,
              extended: Optional[bool] = None) -> int:
    """Store one league's week: team rows, plus lineups once 029 is in.
    Returns the number of team rows."""
    if extended is None:
        extended = db.lineups_ready()
    rows = rows_from_week(league, week_data, ctx, extended=extended)
    if not rows:
        return 0
    db.upsert_team_weeks(rows)
    if extended:
        lineups, players = lineup_rows(league, week_data, ctx)
        if players:
            db.upsert_nfl_players(players)
        if lineups:
            db.upsert_lineups(lineups)
    return len(rows)


def save_from_paper(db, league: dict, week_data) -> None:
    """Called after a paper is written. Never raises: a leaderboard is not a
    reason for a paper to fail."""
    try:
        if not week_is_final(week_data.season, week_data.week):
            return
        ctx = None
        if db.lineups_ready():
            from web import ppr
            ctx = ppr.context(int(week_data.season), int(week_data.week))
        save_week(db, league, week_data, ctx)
    except Exception as exc:  # noqa: BLE001
        print(f"[league_stats] could not save from paper: {exc}", flush=True)


# ---------------------------------------------------------------------------
# The collector
# ---------------------------------------------------------------------------

def _unique(leagues: Iterable[dict]) -> list[dict]:
    """One row per REAL league. Two rows pointing at the same platform league
    would count its teams twice in a national total."""
    seen: set = set()
    out = []
    for l in leagues:
        key = (l.get("provider"), str(l.get("platform_league_id")), l.get("season"))
        if key in seen:
            continue
        seen.add(key)
        out.append(l)
    return out


def collect_week(db, week: int, *, season: Optional[int] = None,
                 leagues: Optional[list[dict]] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = print,
                 should_stop: Callable[[], bool] = lambda: False) -> dict:
    """Pull one finished week for every league this season. Resumable."""
    from providers import ProviderError, load_week

    season = int(season or nfl_week.current_season())
    report = {"week": week, "season": season, "leagues": 0, "collected": 0,
              "skipped_done": 0, "no_data": 0, "errors": 0, "rows": 0}

    if not week_is_final(season, week):
        log(f"Week {week} isn't final yet; nothing collected.")
        report["not_final"] = True
        return report

    pool = leagues if leagues is not None else db.all_leagues(season)
    pool = [l for l in _unique(pool) if int(l.get("season") or 0) == season]
    report["leagues"] = len(pool)
    # Once lineups are being stored, "done" means the lineups are in: a week
    # collected before 029 is collected again, for its lineups and PPR scores.
    extended = db.lineups_ready()
    done = (db.lineup_league_ids(season, week) if extended
            else db.team_week_league_ids(season, week))
    ctx = None
    if extended:
        from web import ppr
        ctx = ppr.context(season, week)
        log(f"Week {week}: standardized PPR "
            f"{'ready' if ctx else 'UNAVAILABLE (Sleeper unreachable) - raw scores only'}.")

    for i, league in enumerate(pool, 1):
        if should_stop():
            log("Stopped early.")
            break
        if league["id"] in done:
            report["skipped_done"] += 1
            continue
        try:
            wd = load_week(league["provider"], league["platform_league_id"],
                           season, week)
            n = save_week(db, league, wd, ctx, extended=extended)
            if n:
                report["collected"] += 1
                report["rows"] += n
            else:
                report["no_data"] += 1
        except ProviderError as exc:
            # Unplayed, private, deleted, never drafted: all ordinary for a
            # league somebody connected once in August and forgot.
            report["no_data"] += 1
            if report["no_data"] <= 20:
                log(f"  no data: {league.get('league_name')} "
                    f"({league['provider']}): {exc}")
        except Exception as exc:  # noqa: BLE001 — one league never stops the run
            report["errors"] += 1
            log(f"  ERROR {league.get('league_name')} ({league['provider']}): "
                f"{type(exc).__name__}: {exc}")
        sleep(PACE.get(league["provider"], 1.0))
        if i % 100 == 0:
            log(f"  ...{i}/{len(pool)} leagues, {report['rows']} rows")

    log(f"Week {week}: {report['collected']} leagues collected "
        f"({report['rows']} team rows), {report['skipped_done']} already done, "
        f"{report['no_data']} with no data, {report['errors']} errors.")
    return report


def backfill(db, weeks: Iterable[int], **kwargs) -> list[dict]:
    return [collect_week(db, w, **kwargs) for w in weeks]


# ---------------------------------------------------------------------------
# Running it from the staff page without blocking a request
# ---------------------------------------------------------------------------

_job_lock = threading.Lock()
_job: dict[str, Any] = {"running": False, "log": [], "started": None,
                        "stop": False}


def job_status() -> dict:
    with _job_lock:
        return {"running": _job["running"], "log": list(_job["log"][-40:]),
                "started": _job["started"]}


def start_background(db, weeks: list[int]) -> bool:
    """Start a collection in a thread. False if one is already running.

    Safe to lose: if the server restarts mid-run, whatever was collected is
    kept and the next run skips it.
    """
    with _job_lock:
        if _job["running"]:
            return False
        _job.update(running=True, log=[], started=datetime.now(timezone.utc).isoformat(),
                    stop=False)

    def log(line: str) -> None:
        print(f"[league_stats] {line}", flush=True)
        with _job_lock:
            _job["log"].append(line)

    def run():
        try:
            backfill(db, weeks, log=log, should_stop=lambda: _job["stop"])
        except Exception as exc:  # noqa: BLE001
            log(f"Run failed: {type(exc).__name__}: {exc}")
        finally:
            with _job_lock:
                _job["running"] = False
            clear_cache()
            try:
                from web import around
                around.clear_cache()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=run, name="league-stats", daemon=True).start()
    return True


def stop_background() -> None:
    with _job_lock:
        _job["stop"] = True


# ---------------------------------------------------------------------------
# Leaderboards
# ---------------------------------------------------------------------------

#: (key, title, column, descending, extra filter)
BOARDS = [
    ("lowest", "Lowest scores", "points", False, "played"),
    ("highest", "Highest scores", "points", True, "played"),
    ("blowouts", "Biggest blowouts", "margin", True, "won"),
    ("closest", "Closest games", "margin", False, "won"),
    ("bench", "Most points left on the bench", "bench_left", True, "played"),
    ("players", "Best single-player games", "top_player_points", True, "played"),
    ("bench_players", "Best games on a bench", "best_bench_points", True, "played"),
]

_cache: dict = {}
CACHE_SECONDS = 600


def leaderboards(db, season: int, week: Optional[int], limit: int = 10) -> dict:
    """Every board for one week (or the whole season when week is None).

    Cached for ten minutes: the page is a handful of ordered queries against
    a table that only changes on Tuesdays.
    """
    key = (season, week, limit)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]

    boards = []
    for slug, title, column, desc, where in BOARDS:
        boards.append({"key": slug, "title": title, "column": column,
                       "rows": db.team_weeks_top(season, week, column, desc,
                                                 where, limit)})
    summary = db.team_weeks_summary(season, week)
    out = {"season": season, "week": week, "boards": boards, "summary": summary}
    _cache[key] = (time.time(), out)
    return out


def clear_cache() -> None:
    _cache.clear()


# ---------------------------------------------------------------------------
# Around the Leagues, the full page: the same arithmetic as the SQL functions
# around_week / around_season (migration 029). Production runs the SQL; this
# is the demo database's version, and the tests hold the two to each other.
# ---------------------------------------------------------------------------

from decimal import Decimal, ROUND_HALF_UP  # noqa: E402


def _r(x, k=2):
    """Postgres round(): half away from zero."""
    if x is None:
        return None
    q = Decimal(1).scaleb(-k)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


def _pct(sorted_vals: list, f: float):
    """percentile_cont(f)."""
    n = len(sorted_vals)
    if not n:
        return None
    pos = f * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


_CARD = ("league_id", "team_id", "team_name", "manager", "provider", "team_count",
         "scoring_type", "week", "result", "points", "ppr_points",
         "opponent_points", "margin", "optimal_points", "bench_left", "empty_slots",
         "top_player", "best_bench_player")


def _size(n):
    n = n or 0
    return ("8-" if n <= 8 else "10" if n <= 10 else "12" if n <= 12
            else "14" if n <= 14 else "16+")


def compute_week(team_rows: list[dict], lineup_rows: list[dict],
                 players: dict[str, dict], season: int, week: int,
                 min_starts: int = 150) -> dict:
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    tw = []
    for r in team_rows:
        if (int(r["season"]) == season and int(r["week"]) == week
                and r["result"] != "BYE" and (f(r["points"]) or 0) > 0):
            r = dict(r)
            for k in ("points", "ppr_points", "opponent_points", "margin",
                      "optimal_points", "bench_left", "ppr_coverage"):
                r[k] = f(r.get(k))
            r["std_pts"] = r["ppr_points"] if r["ppr_points"] is not None else r["points"]
            tw.append(r)
    # Which teams count where (see the SQL): in_sd for cross-league
    # comparisons, in_norm for anything read from a league's own points.
    has_std = any(r["ppr_points"] is not None for r in tw)
    per_league: dict = {}
    for r in tw:
        e = per_league.setdefault(r["league_id"], ([], []))
        e[0].append(r["points"])
        if r["ppr_points"] is not None:
            e[1].append(r["ppr_points"])
    okl = set()
    for lid, (raw, ppr) in per_league.items():
        raw_avg, ppr_avg = _avg(raw), _avg(ppr)
        if ppr_avg is not None and ppr_avg > 0:
            ok = 0.7 <= raw_avg / ppr_avg <= 1.3
        else:
            ok = 40 <= raw_avg <= 200
        if ok:
            okl.add(lid)
    for r in tw:
        r["in_norm"] = r["league_id"] in okl
        r["in_sd"] = (r["ppr_points"] is not None) if has_std else r["in_norm"]
    sd = [r for r in tw if r["in_sd"]]
    norm = [r for r in tw if r["in_norm"]]

    by_team = {(r["league_id"], r["team_id"]): r for r in tw}
    tkey = lambda r: (str(r["league_id"]), str(r["team_id"]))  # noqa: E731
    card = lambda r: {k: r.get(k) for k in _CARD}  # noqa: E731

    pts = sorted(r["points"] for r in tw)
    std = sorted(r["std_pts"] for r in sd)
    fr = [g / 100 for g in range(101)]

    def pcts(vals):
        return [_pct(vals, x) for x in fr] if vals else None

    lut = []
    for l in lineup_rows:
        if int(l["season"]) != season or int(l["week"]) != week:
            continue
        t = by_team.get((l["league_id"], l["team_id"]))
        if t is None:
            continue
        lut.append(dict(l, points=f(l["points"]), ppr_points=f(l.get("ppr_points")),
                        result=t["result"]))

    top1: dict = {}
    for l in lut:
        if not l["started"]:
            continue
        k = (l["league_id"], l["team_id"])
        cur = top1.get(k)
        if cur is None or (-l["points"], l["player_key"]) < (-cur["points"], cur["player_key"]):
            top1[k] = l
    top_on: dict = {}
    for l in top1.values():
        e = top_on.setdefault(l["player_key"], {"teams": 0, "best": None})
        e["teams"] += 1
        e["best"] = l["points"] if e["best"] is None else max(e["best"], l["points"])

    ps: dict = {}
    for l in lut:
        e = ps.setdefault(l["player_key"], {"rostered": 0, "started": 0, "benched": 0,
                                            "wins": 0.0, "ppr": None, "best": None,
                                            "bench": []})
        e["rostered"] += 1
        bench = (not l["started"]) and l["slot"] == "BN"
        if l["started"]:
            e["started"] += 1
            e["wins"] += 1 if l["result"] == "W" else 0.5 if l["result"] == "T" else 0
        if bench:
            e["benched"] += 1
            e["bench"].append(l["points"])
        if l["ppr_points"] is not None:
            e["ppr"] = l["ppr_points"] if e["ppr"] is None else max(e["ppr"], l["ppr_points"])
        e["best"] = l["points"] if e["best"] is None else max(e["best"], l["points"])
    pcard = []
    for key, e in ps.items():
        meta = players.get(key) or {}
        pcard.append({
            "player_key": key, "name": meta.get("name") or key,
            "position": meta.get("position"), "nfl_team": meta.get("nfl_team"),
            "rostered": e["rostered"], "started": e["started"], "benched": e["benched"],
            "start_pct": _r(e["started"] / e["rostered"], 3) if e["rostered"] else None,
            "win_pct": _r(e["wins"] / e["started"], 3) if e["started"] else None,
            "wins": e["wins"], "ppr": e["ppr"], "best": e["best"],
            "bench_total": _r(sum(e["bench"]), 2),
            "bench_avg": _r(_avg(e["bench"]), 2),
        })
    pk = lambda c: c["player_key"]  # noqa: E731

    def top(rows, key, n):
        return sorted(rows, key=key)[:n]

    groups = []
    for dim, keyf in (("provider", lambda r: r["provider"]),
                      ("scoring", lambda r: r.get("scoring_type") or "unknown"),
                      ("size", lambda r: _size(r.get("team_count")))):
        buckets: dict = {}
        for r in tw:
            buckets.setdefault(keyf(r), []).append(r)
        for k, rows in buckets.items():
            opt = [r for r in rows if r["bench_left"] is not None]
            groups.append({
                "dim": dim, "key": k, "n": len(rows),
                "avg_ppr": _r(_avg([r["std_pts"] for r in rows if r["in_sd"]])),
                "avg_bench": _r(_avg([r["bench_left"] for r in rows if r["in_norm"]])),
                "ghost_pct": _r(_avg([1.0 if (r.get("empty_slots") or 0) > 0 else 0.0
                                      for r in rows]), 4),
                "perfect_pct": _r(_avg([1.0 if r["bench_left"] <= 0.005 else 0.0
                                        for r in opt]), 4) if opt else None,
            })
    groups.sort(key=lambda g: (g["dim"], g["key"]))

    hist: dict = {}
    for v in std:
        lo = min(int(v // 10) * 10, 250)
        hist[lo] = hist.get(lo, 0) + 1
    fmt = lambda name: sorted(r["points"] for r in norm if r.get("scoring_type") == name)  # noqa: E731
    with_opt = [r for r in tw if r["bench_left"] is not None]
    cov = [r.get("ppr_coverage") for r in tw if r.get("ppr_coverage") is not None]
    return {
        "season": season, "week": week,
        "summary": {
            "teams": len(tw), "leagues": len({r["league_id"] for r in tw}),
            "compared": len(sd),
            "custom_leagues": len({r["league_id"] for r in tw if not r["in_norm"]}),
            "avg_raw": _r(_avg(pts)), "median_raw": _r(_pct(pts, 0.5)),
            "avg_ppr": _r(_avg(std)), "median_ppr": _r(_pct(std, 0.5)),
            "standardized": sum(1 for r in tw if r["ppr_points"] is not None),
            "coverage": _r(_avg(cov), 3),
            "lineup_teams": len({(l["league_id"], l["team_id"]) for l in lut}),
        },
        "hist": [{"lo": k, "n": hist[k]} for k in sorted(hist)],
        "pct": {"all": pcts(std), "ppr": pcts(fmt("ppr")),
                "half_ppr": pcts(fmt("half_ppr")), "std": pcts(fmt("std")),
                "n_all": len(std), "n_ppr": len(fmt("ppr")),
                "n_half_ppr": len(fmt("half_ppr")), "n_std": len(fmt("std"))},
        "lineup": {
            "played": len(tw),
            "losses": sum(1 for r in tw if r["result"] == "L"),
            "lineup_losses": sum(1 for r in tw if r["result"] == "L"
                                 and r["optimal_points"] is not None
                                 and r["opponent_points"] is not None
                                 and r["optimal_points"] > r["opponent_points"]),
            "with_optimal": len(with_opt),
            "perfect": sum(1 for r in with_opt if r["bench_left"] <= 0.005),
            "ghosts": sum(1 for r in tw if (r.get("empty_slots") or 0) > 0),
            "avg_bench_left": _r(_avg([r["bench_left"] for r in norm])),
        },
        "unlucky": [card(r) for r in top([r for r in sd if r["result"] == "L"],
                                         lambda r: (-r["std_pts"], tkey(r)), 5)],
        "lucky": [card(r) for r in top([r for r in sd if r["result"] == "W"],
                                       lambda r: (r["std_pts"], tkey(r)), 5)],
        "halls": {
            "highest": [card(r) for r in top(sd, lambda r: (-r["std_pts"], tkey(r)), 5)],
            "lowest": [card(r) for r in top(sd, lambda r: (r["std_pts"], tkey(r)), 5)],
            "blowouts": [card(r) for r in top(
                [r for r in norm if r["result"] == "W" and r["margin"] is not None],
                lambda r: (-r["margin"], tkey(r)), 5)],
            "bench": [card(r) for r in top(
                [r for r in norm if r["bench_left"] is not None],
                lambda r: (-r["bench_left"], tkey(r)), 5)],
        },
        "groups": groups,
        "players": {
            "top_on": [{"player_key": k, "name": (players.get(k) or {}).get("name") or k,
                        "position": (players.get(k) or {}).get("position"),
                        "teams": v["teams"], "best": v["best"]}
                       for k, v in sorted(top_on.items(),
                                          key=lambda kv: (-kv[1]["teams"], kv[0]))[:10]],
            "benched": top([c for c in pcard if c["benched"] > 0],
                           lambda c: (-c["bench_total"], pk(c)), 10),
            "started": top([c for c in pcard if c["started"] > 0],
                           lambda c: (-c["started"], pk(c)), 10),
            "win_best": top([c for c in pcard if c["started"] >= min_starts],
                            lambda c: (-c["win_pct"], -c["started"], pk(c)), 10),
            "win_worst": top([c for c in pcard if c["started"] >= min_starts],
                             lambda c: (c["win_pct"], -c["started"], pk(c)), 10),
            "flops": top([c for c in pcard if c["started"] > 0 and c["ppr"] is not None
                          and c["ppr"] < 5], lambda c: (-c["started"], pk(c)), 5),
            "sleepers": top([c for c in pcard if c["rostered"] >= 100
                             and c["ppr"] is not None and c["start_pct"] < 0.25],
                            lambda c: (-c["ppr"], pk(c)), 5),
            "min_starts": min_starts,
        },
    }


def compute_season(team_rows: list[dict], season: int) -> dict:
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    games = []
    for r in team_rows:
        if int(r["season"]) == season and r["result"] in ("W", "L", "T"):
            r = dict(r, points=f(r["points"]), ppr_points=f(r.get("ppr_points")),
                     opponent_points=f(r.get("opponent_points")),
                     optimal_points=f(r.get("optimal_points")),
                     bench_left=f(r.get("bench_left")))
            r["std_pts"] = r["ppr_points"] if r["ppr_points"] is not None else r["points"]
            games.append(r)
    through = max((int(r["week"]) for r in games), default=None)
    latest = {(r["league_id"], r["team_id"]): r for r in games
              if int(r["week"]) == through and r["points"] > 0}
    hist: dict = {}
    for r in games:
        k = (r["league_id"], r["team_id"])
        if k in latest:
            hist.setdefault(k, []).append(r)

    # all-play, over every game in each league-week
    lw: dict = {}
    for r in games:
        lw.setdefault((r["league_id"], int(r["week"])), []).append(r)
    luck_parts: dict = {}
    for rows in lw.values():
        n = len(rows)
        if n <= 1:
            continue
        for r in rows:
            k = (r["league_id"], r["team_id"])
            if k not in latest:
                continue
            lower = sum(1 for o in rows if o["points"] < r["points"])
            same = sum(1 for o in rows if o["points"] == r["points"])
            e = luck_parts.setdefault(k, [0.0, 0.0])
            e[0] += 1 if r["result"] == "W" else 0.5 if r["result"] == "T" else 0
            e[1] += (lower + 0.5 * (same - 1)) / (n - 1)

    board = []
    for k, a in latest.items():
        rows = sorted(hist[k], key=lambda r: -int(r["week"]))
        cur = rows[0]["result"]
        length = next((i for i, r in enumerate(rows) if r["result"] != cur), len(rows))
        w = sum(1 for r in rows if r["result"] == "W")
        l = sum(1 for r in rows if r["result"] == "L")
        t = sum(1 for r in rows if r["result"] == "T")
        lp = luck_parts.get(k)
        wins, expected = (lp[0], _r(lp[1])) if lp else (None, None)
        luck = (wins - expected) if lp else None
        board.append({
            "league_id": a["league_id"], "team_id": a["team_id"],
            "team_name": a.get("team_name"), "manager": a.get("manager"),
            "provider": a.get("provider"), "team_count": a.get("team_count"),
            "scoring_type": a.get("scoring_type"),
            "w": w, "l": l, "t": t, "total": _r(sum(r["std_pts"] for r in rows)),
            "streak": length, "streak_kind": cur,
            "wins": wins, "expected": expected,
            "luck": _r(luck) if luck is not None else None,
            "_luck": luck,
        })
    tk = lambda b: (str(b["league_id"]), str(b["team_id"]))  # noqa: E731
    strip = lambda b: {k: v for k, v in b.items() if k != "_luck"}  # noqa: E731

    weeks = []
    for wk in sorted({int(r["week"]) for r in games}):
        rows = [r for r in games if int(r["week"]) == wk and r["points"] > 0]
        if not rows:
            continue
        has_std = any(r["ppr_points"] is not None for r in rows)
        std = sorted(r["std_pts"] for r in rows
                     if not has_std or r["ppr_points"] is not None)
        losses = [r for r in rows if r["result"] == "L"]
        opt = [r for r in rows if r["bench_left"] is not None]
        weeks.append({
            "week": wk, "teams": len(rows), "avg_ppr": _r(_avg(std)),
            "median_ppr": _r(_pct(std, 0.5)),
            "ghost_pct": _r(_avg([1.0 if (r.get("empty_slots") or 0) > 0 else 0.0
                                  for r in rows]), 4),
            "lineup_loss_pct": _r(sum(1 for r in losses if r["optimal_points"] is not None
                                      and r["opponent_points"] is not None
                                      and r["optimal_points"] > r["opponent_points"])
                                  / len(losses), 4) if losses else None,
            "perfect_pct": _r(_avg([1.0 if r["bench_left"] <= 0.005 else 0.0
                                    for r in opt]), 4) if opt else None,
        })
    return {
        "season": season, "through": through, "weeks": weeks,
        "tracker": {"teams": len(board),
                    "unbeaten": sum(1 for b in board if b["l"] == 0 and b["t"] == 0),
                    "winless": sum(1 for b in board if b["w"] == 0 and b["t"] == 0)},
        "win_streaks": [strip(b) for b in sorted(
            [b for b in board if b["streak_kind"] == "W"],
            key=lambda b: (-b["streak"], tk(b)))[:5]],
        "loss_streaks": [strip(b) for b in sorted(
            [b for b in board if b["streak_kind"] == "L"],
            key=lambda b: (-b["streak"], tk(b)))[:5]],
        "unluckiest": [strip(b) for b in sorted(
            [b for b in board if b["_luck"] is not None],
            key=lambda b: (b["_luck"], tk(b)))[:5]],
        "luckiest": [strip(b) for b in sorted(
            [b for b in board if b["_luck"] is not None],
            key=lambda b: (-b["_luck"], tk(b)))[:5]],
    }
