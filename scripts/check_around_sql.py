"""
Does around_week() in Postgres still agree with its Python twin?

    python -m scripts.check_around_sql "postgresql://.../postgres"

The page is computed twice: `around_week` / `around_season` in SQL (what
production runs) and `league_stats.compute_week` / `compute_season` in
Python (what the demo and the tests run). If the two drift, the staff page
and the tests disagree about the week and nobody finds out.

This loads a season of invented leagues — including the shapes that have
gone wrong before: a league starting twenty players, a team whose
standardized score came back 0.00, a league scoring defenders — into a
throwaway schema, runs both, and prints the first field that differs.

It needs a Postgres you don't mind writing to. A local one is enough:

    initdb -D /tmp/pgd -U postgres -A trust
    pg_ctl -D /tmp/pgd -o '-k /tmp -p 5433' -l /tmp/pg.log start
    python -m scripts.check_around_sql "postgresql://postgres@/postgres?host=/tmp&port=5433"

Never point it at production: it creates and drops its own schema, but the
password for that database should not be in your shell history either.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEASON = 2026
TABLES = ("team_weeks", "lineup_players", "nfl_players")


def _psql(dsn: str, sql: str, quiet: bool = True) -> str:
    out = subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
                         capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"psql failed:\n{out.stderr.strip()}")
    return out.stdout.strip()


def _ddl() -> str:
    """The tables and the two functions, read straight from the migrations
    so this can never test a stale copy of them."""
    import re
    parts = ["create table if not exists public.leagues (id uuid primary key);"]
    for name, path in (("team_weeks", "021_team_weeks.sql"),
                       ("nfl_players", "029_lineups_and_ppr.sql"),
                       ("lineup_players", "029_lineups_and_ppr.sql")):
        src = (ROOT / "migrations" / path).read_text()
        m = re.search(r"create table if not exists public\.%s \(.*?\n\);" % name, src, re.S)
        if not m:
            raise SystemExit(f"no CREATE TABLE for {name} in {path}")
        parts.append(m.group(0) + ";")
    parts.append("alter table public.team_weeks add column if not exists ppr_points numeric(7,2);")
    parts.append("alter table public.team_weeks add column if not exists ppr_coverage numeric(4,3);")
    # The functions: whichever migration defines them last wins, the same
    # way they land in Supabase when the migrations are run in order.
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        src = path.read_text()
        for fn in ("around_week", "around_season"):
            i = src.find(f"create or replace function public.{fn}(")
            if i >= 0:
                parts.append(src[i:src.index("$$;", i) + 3])
    return "\n".join(parts)


def _rows(rng: random.Random, weeks: int):
    """A season, with the shapes that have broken this page before."""
    tw, lu, players = [], [], {}
    leagues = []
    # (teams, starters, scale, how the standardized score behaves)
    shapes = [(12, 9, 1.0, "normal"), (10, 9, 1.0, "normal"), (14, 9, 1.0, "normal"),
              (8, 9, 1.0, "normal"), (12, 9, 1.0, "no_ppr"), (12, 9, 1.0, "ghost"),
              (4, 20, 1.0, "normal"), (12, 9, 9.0, "normal"), (16, 9, 1.0, "normal")]
    for i, (size, starters, scale, kind) in enumerate(shapes):
        leagues.append((f"{i:08d}-0000-0000-0000-000000000000", size, starters, scale, kind))
    for key, pos in [(f"p{i}", ("QB", "RB", "WR", "TE", "K", "DEF")[i % 6]) for i in range(80)]:
        players[key] = {"player_key": key, "name": f"Player {key}", "position": pos,
                        "nfl_team": "ATL"}
    for week in range(1, weeks + 1):
        real = {k: round(rng.gauss(11, 7), 2) for k in players}
        for lid, size, starters, scale, kind in leagues:
            for t in range(size):
                roster = rng.sample(list(players), starters + 6)
                start = roster[:starters]
                pts = round(sum(max(0.0, real[k]) for k in start) * scale, 2)
                if kind == "no_ppr":
                    ppr, cov = None, None
                elif kind == "ghost" and t < 2:
                    ppr, cov = 0.0, 1.0           # matched, but no stat lines
                else:
                    ppr, cov = round(sum(max(0.0, real[k]) for k in start), 2), 0.97
                opp = round(pts + rng.uniform(-30, 30), 2)
                tw.append({
                    "league_id": lid, "provider": ["sleeper", "espn", "yahoo"][t % 3],
                    "platform_league_id": str(t), "season": SEASON, "week": week,
                    "team_id": f"t{t}", "team_name": f"Team {t}", "manager": f"m{t}",
                    "points": pts, "opponent_points": opp,
                    "margin": round(pts - opp, 2),
                    "result": "W" if pts > opp else "L" if pts < opp else "T",
                    "optimal_points": round(pts + rng.uniform(0, 30), 2),
                    "bench_left": round(rng.uniform(0, 30), 2),
                    "empty_slots": 1 if rng.random() < 0.05 else 0,
                    "top_player": start[0], "best_bench_player": roster[-1],
                    "team_count": size,
                    "scoring_type": ["ppr", "half_ppr", "std", None][t % 4],
                    "ppr_points": ppr, "ppr_coverage": cov})
                for j, k in enumerate(roster):
                    lu.append({"league_id": lid, "season": SEASON, "week": week,
                               "team_id": f"t{t}", "player_key": k,
                               "slot": players[k]["position"] if j < starters else "BN",
                               "started": j < starters,
                               "points": round(max(0.0, real[k]) * scale, 2),
                               "ppr_points": round(max(0.0, real[k]), 2)})
    return tw, lu, players, [x[0] for x in leagues]


def _load(dsn, tw, lu, players, league_ids) -> None:
    def copy(table, cols, rows):
        if not rows:
            return
        body = "\n".join("\t".join(
            "\\N" if r.get(c) is None else str(r[c]) for c in cols) for r in rows)
        out = subprocess.run(
            ["psql", dsn, "-v", "ON_ERROR_STOP=1",
             "-c", f"copy public.{table} ({', '.join(cols)}) from stdin"],
            input=body + "\n", capture_output=True, text=True)
        if out.returncode:
            raise SystemExit(f"loading {table} failed:\n{out.stderr.strip()}")

    _psql(dsn, "truncate public.team_weeks, public.lineup_players, "
               "public.nfl_players, public.leagues cascade;")
    _psql(dsn, "insert into public.leagues (id) values "
               + ",".join(f"('{i}')" for i in league_ids) + ";")
    copy("nfl_players", ["player_key", "name", "position", "nfl_team"],
         list(players.values()))
    copy("team_weeks", list(tw[0]), tw)
    copy("lineup_players", list(lu[0]), lu)


def _diff(path, a, b, out, depth=0):
    """Every leaf where the two disagree, by path."""
    if len(out) > 25 or depth > 8:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            _diff(f"{path}.{k}", a.get(k), b.get(k), out, depth + 1)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} rows in SQL, {len(b)} in Python")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(f"{path}[{i}]", x, y, out, depth + 1)
    else:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(float(a) - float(b)) > 0.011:       # both round to 2dp
                out.append(f"{path}: SQL {a}, Python {b}")
        elif a != b:
            out.append(f"{path}: SQL {a!r}, Python {b!r}")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__.strip())
    dsn = sys.argv[1]
    weeks = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    week = weeks                      # compare the last week we generated
    from web import league_stats

    print(f"loading schema and functions from {ROOT / 'migrations'} …")
    _psql(dsn, _ddl())
    rng = random.Random(2026)
    tw, lu, players, league_ids = _rows(rng, weeks)
    print(f"{len(tw):,} team rows, {len(lu):,} lineup rows, "
          f"{len(league_ids)} leagues, weeks 1-{weeks}; comparing week {week}")
    _load(dsn, tw, lu, players, league_ids)

    bad = 0
    for name, sql, py in (
        ("around_week",
         f"select around_week({SEASON}, {week}, 150)",
         lambda: league_stats.compute_week(tw, lu, players, SEASON, week, 150)),
        ("around_season",
         f"select around_season({SEASON})",
         lambda: league_stats.compute_season(tw, SEASON)),
    ):
        got = json.loads(_psql(dsn, sql))
        want = py()
        out: list = []
        _diff(name, got, want, out)
        if out:
            bad += 1
            print(f"\n{name}: {len(out)} difference(s)")
            for line in out[:25]:
                print("   ", line)
        else:
            print(f"{name}: agrees")
    if not bad:
        print("\nThe SQL and the Python twin agree.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
