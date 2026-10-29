"""
Does undefeated_live() in Postgres still agree with its Python twin?

    python -m scripts.check_undefeated_sql "postgresql://.../postgres" [leagues]

The Undefeated Watch is computed twice: `undefeated_live` in SQL (migration
068, what production runs from the saved copy) and `web.undefeated.compute`
in Python (what the demo and the tests run). This loads a season of the
demo's invented leagues into a throwaway database, bent into every shape
that should trip one of them up —

  * a league missing a week (it must drop out, not show a 7-game team),
  * a three-team league, a dynasty league (both left out),
  * a team renamed mid-season, two teams sharing a name (trades and waivers
    are joined by name),
  * a tie, a bye, an abandoned roster, a league with half its waiver weeks,
  * a starter who played and scored zero (he turned up),
  * a draft with no team count, and a waiver claim from next week,

runs both, and prints the first fields that differ.

A local Postgres is enough (the same three commands as check_around_sql):

    initdb -D /tmp/pgd -U postgres -A trust
    pg_ctl -D /tmp/pgd -o '-k /tmp -p 5433' -l /tmp/pg.log start
    python -m scripts.check_undefeated_sql "postgresql://postgres@/postgres?host=/tmp&port=5433"

Never point it at production: it truncates the tables it loads.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEASON = 2026
WEEKS = 8

TABLES = [
    ("team_weeks", "021_team_weeks.sql"),
    ("nfl_players", "029_lineups_and_ppr.sql"),
    ("lineup_players", "029_lineups_and_ppr.sql"),
    ("nfl_week_players", "055_who_actually_played.sql"),
    ("trades", "045_trades.sql"),
    ("trade_players", "045_trades.sql"),
    ("draft_picks", "050_drafts.sql"),
    ("waivers", "067_waivers.sql"),
    ("waiver_scans", "067_waivers.sql"),
]
FUNCTIONS = ("dynasty_league_ids", "undefeated_live")


def _psql(dsn: str, sql: str) -> str:
    out = subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
                         capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"psql failed:\n{out.stderr.strip()[:2000]}")
    return out.stdout.strip()


def _ddl() -> str:
    """Tables and functions read straight out of migrations/, so this can
    never test a stale copy of them. The last definition of a function wins,
    as it does when the migrations are run in order."""
    parts = ["drop table if exists public.waivers, public.waiver_scans, public.draft_picks, "
             "public.trade_players, public.trades, public.nfl_week_players, "
             "public.lineup_players, public.nfl_players, public.team_weeks, "
             "public.leagues cascade;",
             "create table public.leagues (id uuid primary key, league_type text, format text);"]
    for name, path in TABLES:
        src = (ROOT / "migrations" / path).read_text()
        m = re.search(r"create table if not exists public\.%s \(.*?\n\);" % name, src, re.S)
        if not m:
            raise SystemExit(f"no CREATE TABLE for {name} in {path}")
        parts.append(m.group(0))
    parts.append("alter table public.team_weeks add column if not exists ppr_points numeric(7,2);")
    parts.append("alter table public.team_weeks add column if not exists ppr_coverage numeric(4,3);")
    for fn in FUNCTIONS:
        body = None
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            src = path.read_text()
            i = src.find(f"create or replace function public.{fn}(")
            if i >= 0:
                body = src[i:src.index("$$;", i) + 3]
        if body is None:
            raise SystemExit(f"no function {fn} in migrations/")
        parts.append(body)
    return "\n".join(parts)


def _season(n_leagues: int):
    """The demo's season, re-keyed to uuids and bent out of shape."""
    os.environ["DEMO_MODE"] = "1"
    from scripts import demo_around
    from web import demo_db

    for store in (demo_db._TEAM_WEEKS, demo_db._LINEUPS, demo_db._NFL_PLAYERS,
                  demo_db._DRAFT_PICKS, demo_db._DRAFT_SCANS, demo_db._TRADES,
                  demo_db._TRADE_PLAYERS, demo_db._TRADE_SCANS, demo_db._WAIVERS,
                  demo_db._WAIVER_SCANS, demo_db._WEEK_PLAYERS):
        store.clear()
    demo_around.sleeper_keys = lambda: {}          # offline, made-up ids
    demo_around.seed(n_leagues, WEEKS, SEASON, random.Random(68))
    rng = random.Random(6868)

    ids = {}
    def uid(lid):
        return ids.setdefault(lid, str(uuid.UUID(int=len(ids) + 1)))

    tw = [dict(r, league_id=uid(r["league_id"])) for r in demo_db._TEAM_WEEKS.values()]
    lu = [dict(r, league_id=uid(r["league_id"])) for r in demo_db._LINEUPS.values()]
    dp = [dict(r, league_id=uid(r["league_id"])) for r in demo_db._DRAFT_PICKS.values()]
    th = [dict(r, league_id=uid(r["league_id"])) for r in demo_db._TRADES.values()]
    tp = [dict(r, league_id=uid(r["league_id"])) for r in demo_db._TRADE_PLAYERS.values()]
    wv = [dict(r, league_id=uid(r["league_id"])) for r in demo_db._WAIVERS.values()]
    ws = [dict(r, league_id=uid(r["league_id"])) for r in demo_db._WAIVER_SCANS.values()]
    nfl = list(demo_db._NFL_PLAYERS.values())
    L = [uid(f"demo-{i}") for i in range(n_leagues)]

    # 0: week 3 never collected.
    tw = [r for r in tw if not (r["league_id"] == L[0] and r["week"] == 3)]
    # 1: three teams.
    keep = {"t0", "t1", "t2"}
    tw = [r for r in tw if r["league_id"] != L[1] or r["team_id"] in keep]
    # 3: a team renamed from week 5; its earlier trades and claims use the old name.
    for r in tw:
        if r["league_id"] == L[3] and r["team_id"] == "t0" and r["week"] >= 5:
            r["team_name"] = "Renamed Late"
    # 4: two teams with one name.
    nm = next(r["team_name"] for r in tw if r["league_id"] == L[4] and r["team_id"] == "t0")
    for r in tw:
        if r["league_id"] == L[4] and r["team_id"] == "t1":
            r["team_name"] = nm
    # 5: a tie, and a bye.
    for r in tw:
        if r["league_id"] == L[5] and r["week"] == 2 and r["team_id"] == "t0":
            r.update(result="T", margin=0.0)
        if r["league_id"] == L[5] and r["week"] == 4 and r["team_id"] == "t1":
            r.update(result="BYE", opponent_points=None, margin=None)
    # 6: half the wire weeks.
    ws = [s for s in ws if not (s["league_id"] == L[6] and s["week"] % 2 == 0)]
    # 7: a draft with no team count.
    for d in dp:
        if d["league_id"] == L[7]:
            d["team_count"] = None
    # Players who played and scored nothing, and some who didn't play.
    wp = []
    keys = sorted({r["player_key"] for r in lu})
    for w in range(1, WEEKS + 1):
        for k in rng.sample(keys, 12):
            wp.append({"season": SEASON, "week": w, "player_key": k,
                       "played": rng.random() < 0.6, "snaps": None, "team_snaps": None})
    for r in lu:
        if r["started"] and rng.random() < 0.01:
            r["points"] = 0.0
            r["ppr_points"] = 0.0
    # A claim from next week (the wire this week reads up to through + 1).
    if wv:
        nxt = dict(wv[0], week=WEEKS + 1, move_key="wnextweek000")
        wv.append(nxt)
    leagues = [{"id": i, "league_type": "dynasty" if i == L[2] else None, "format": None}
               for i in sorted(set(ids.values()))]
    return dict(leagues=leagues, team_weeks=tw, lineup_players=lu, draft_picks=dp,
                trades=th, trade_players=tp, waivers=wv, waiver_scans=ws,
                nfl_players=nfl, nfl_week_players=wp)


def _load(dsn: str, data: dict) -> None:
    def cell(v):
        if v is None:
            return "\\N"
        if isinstance(v, bool):
            return "t" if v else "f"
        if isinstance(v, (list, dict)):
            v = json.dumps(v)
        return str(v).replace("\\", "\\\\").replace("\t", " ").replace("\n", " ")

    for table in ("leagues", "nfl_players", "team_weeks", "lineup_players",
                  "nfl_week_players", "trades", "trade_players", "draft_picks",
                  "waivers", "waiver_scans"):
        rows = data[table]
        if not rows:
            continue
        cols = sorted({c for r in rows for c in r})
        if table == "trades":
            cols = [c for c in cols if c != "scanned_at"]
        body = "\n".join("\t".join(cell(r.get(c)) for c in cols) for r in rows)
        out = subprocess.run(
            ["psql", dsn, "-v", "ON_ERROR_STOP=1",
             "-c", f"copy public.{table} ({', '.join(cols)}) from stdin"],
            input=body + "\n", capture_output=True, text=True)
        if out.returncode:
            raise SystemExit(f"loading {table} failed:\n{out.stderr.strip()[:1500]}")


def _diff(path, a, b, out, depth=0):
    if len(out) > 40 or depth > 9:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            _diff(f"{path}.{k}", a.get(k), b.get(k), out, depth + 1)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} rows in SQL, {len(b)} in Python")
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(f"{path}[{i}]", x, y, out, depth + 1)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        if abs(float(a) - float(b)) > 0.0011:
            out.append(f"{path}: SQL {a}, Python {b}")
    elif a != b and not (a is None and b is None):
        out.append(f"{path}: SQL {a!r}, Python {b!r}")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__.strip())
    dsn = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    from web import undefeated

    print(f"loading schema and functions from {ROOT / 'migrations'} …")
    _psql(dsn, _ddl())
    data = _season(n)
    print(f"{len(data['team_weeks']):,} team rows, {len(data['lineup_players']):,} lineup rows, "
          f"{len(data['waivers']):,} waiver rows, {len(data['leagues'])} leagues")
    _load(dsn, data)

    got = json.loads(_psql(dsn, f"select public.undefeated_live({SEASON})"))
    dyn = set(filter(None, _psql(dsn, f"select public.dynasty_league_ids({SEASON})").split()))
    played = {(int(r["week"]), r["player_key"]): r["played"] for r in data["nfl_week_players"]}
    want = undefeated.compute(
        team_rows=data["team_weeks"], lineup_rows=data["lineup_players"],
        draft_rows=data["draft_picks"], trade_heads=data["trades"],
        trade_players=data["trade_players"], waiver_rows=data["waivers"],
        waiver_scans=data["waiver_scans"], played=played,
        nfl_players={p["player_key"]: p for p in data["nfl_players"]},
        dynasty_ids=dyn, season=SEASON)
    out: list = []
    _diff("undefeated", got, want, out)
    s = got.get("summary") or {}
    print(f"through week {got.get('through')}: {s.get('teams')} teams in {s.get('leagues')} "
          f"leagues, {s.get('unbeaten')} unbeaten, {s.get('winless')} winless, "
          f"{s.get('incomplete_left')} incomplete and {s.get('dynasty_left')} dynasty left out")
    if out:
        print(f"\n{len(out)} difference(s):")
        for line in out[:40]:
            print("   ", line)
        return 1
    print("\nThe SQL and the Python twin agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
