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


def rows_from_week(league: dict, week_data) -> list[dict[str, Any]]:
    """One row per team. `week_data` should have had the optimizer run
    (load_week does by default) so the bench numbers mean something."""
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

    for m in week_data.matchups:
        a, b = m.teams
        rows.append(row(a, b))
        rows.append(row(b, a))
    for t in week_data.byes:
        rows.append(row(t, None))
    return rows


def save_from_paper(db, league: dict, week_data) -> None:
    """Called after a paper is written. Never raises: a leaderboard is not a
    reason for a paper to fail."""
    try:
        if not week_is_final(week_data.season, week_data.week):
            return
        db.upsert_team_weeks(rows_from_week(league, week_data))
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
    done = db.team_week_league_ids(season, week)

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
            rows = rows_from_week(league, wd)
            if rows:
                db.upsert_team_weeks(rows)
                report["collected"] += 1
                report["rows"] += len(rows)
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
