"""Prove the trade SQL agrees with its Python twin (22 Oct 2026).

    createdb cd && python -m scripts.check_trade_sql "postgresql:///cd"

Every function in the trade stack exists twice: in SQL, which production
runs, and in web/demo_db.py, which the demo and the tests run. Two copies
of one idea drift, and one already had:

    migration 047 added the trade's DAY to trade_exchanges(). The demo
    twin never got it, so every demo trade fell back to the middle of its
    own week and the daily price line became a staircase that changed once
    every seven days. It looked like a quiet market rather than a broken
    page, and it survived a full test suite, because the tests and the
    demo shared the same wrong answer.

That is the failure this script exists to catch: not SQL that errors, but
SQL and Python that quietly disagree. It loads the tables and functions
straight out of migrations/ so it can never check a stale copy, invents a
season of trades containing the awkward shapes (picks, FAAB, three-way
deals, a trade with no timestamp, a player who never resolved to a Sleeper
id), runs both implementations, and prints the first field that differs.

Postgres 16 and a throwaway database are enough:

    pg_ctl -D /tmp/pg -o '-p 5455 -k /tmp' start
    createdb -h /tmp -p 5455 cd
    python -m scripts.check_trade_sql "postgresql://postgres@/cd?host=/tmp&port=5455"

NEVER point it at production. It creates and drops its own rows.
"""

from __future__ import annotations

import json
import random
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEASON = 2026
#: Which functions are checked, and how to call each one in SQL.
CHECKS = ("trade_exchanges", "trade_player_names", "trade_prior", "trade_backfill")


def _sql(dsn: str, statement: str) -> str:
    """Run SQL. Long scripts go through a file: a season of inserts is
    comfortably past the command line's limit."""
    import tempfile
    args = ["psql", dsn, "-qtAX", "-v", "ON_ERROR_STOP=1"]
    if len(statement) > 60_000:
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
            fh.write(statement)
            path = fh.name
        args += ["-f", path]
    else:
        args += ["-c", statement]
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"psql failed:\n{out.stderr.strip()}")
    return out.stdout.strip()


def _ddl() -> str:
    """Tables and functions, read out of the migrations themselves."""
    parts = ["create table if not exists public.leagues ("
             " id uuid primary key, provider text not null default 'sleeper');",
             "create table if not exists public.nfl_players ("
             " player_key text primary key, name text not null default '',"
             " position text, nfl_team text);",
             "create table if not exists public.lineup_players ("
             " league_id uuid not null, season smallint not null,"
             " week smallint not null, team_id text not null,"
             " player_key text not null, slot text not null,"
             " started boolean not null, points numeric(6,2) not null default 0,"
             " ppr_points numeric(6,2),"
             " primary key (league_id, season, week, team_id, player_key));"]
    src = (ROOT / "migrations" / "045_trades.sql").read_text()
    for name in ("trades", "trade_players", "trade_scans"):
        m = re.search(r"create table if not exists public\.%s \(.*?\n\);" % name, src, re.S)
        if not m:
            raise SystemExit(f"no CREATE TABLE for {name} in 045")
        # The real tables reference leagues; the stub above satisfies that.
        parts.append(m.group(0) + ";")
    # Functions: last definition wins, exactly as the migrations land.
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        text = path.read_text()
        for fn in CHECKS:
            i = text.find(f"create or replace function public.{fn}(")
            if i >= 0:
                parts.append(text[i:text.index("$$;", i) + 3])
    return "\n".join(parts)


def _season(rng: random.Random):
    """Leagues, lineups and trades, with every awkward shape in them."""
    leagues, lineups, heads, moves, scans = [], [], [], [], []
    players = [(f"p{i}", ("QB", "RB", "WR", "TE", "K", "DEF")[i % 6]) for i in range(40)]
    # One player who never resolved to a Sleeper id, as the collector leaves him.
    players.append(("espn:99887", "WR"))

    for n, provider in enumerate(("sleeper", "espn", "yahoo")):
        lid = f"{n:08d}-0000-0000-0000-000000000000"
        leagues.append((lid, provider))
        for week in (1, 2, 3):
            scans.append((lid, week, rng.randint(0, 3)))
            for team in range(8):
                for key, pos in rng.sample(players, 14):
                    started = rng.random() < 0.6
                    pts = round(rng.uniform(0, 26), 2)
                    lineups.append((lid, week, f"t{team}", key, pos if started else "BN",
                                    started, pts,
                                    None if rng.random() < 0.08 else pts))
        for i in range(12):
            week = rng.randint(1, 3)
            key = f"tr{n}-{i}"
            picks = [["t1", "2027 1st"]] if i % 5 == 0 else []
            faab = [["t1", "t2", 10]] if i % 7 == 0 else []
            # One in six carries no timestamp at all.
            when = None if i % 6 == 0 else f"2026-09-{10 + (i % 18):02d}T15:04:00+00:00"
            heads.append((lid, week, key, provider, picks, faab, when))
            a, b = rng.sample(players, 2)
            moves.append((lid, week, key, a[0], a[1], "t1", "t2"))
            moves.append((lid, week, key, b[0], b[1], "t2", "t1"))
            if i % 9 == 0:                      # a three-way: three sides
                c = rng.choice(players)
                moves.append((lid, week, key, c[0], c[1], "t3", "t1"))
    return leagues, lineups, heads, moves, scans, players


def _fill_sql(dsn: str, data) -> None:
    leagues, lineups, heads, moves, scans, players = data
    q = []
    for lid, provider in leagues:
        q.append(f"insert into public.leagues values ('{lid}', '{provider}');")
    for key, pos in players:
        q.append("insert into public.nfl_players (player_key, name, position, nfl_team)"
                 f" values ('{key}', '{key.upper()}', '{pos}', 'CIN');")
    for lid, week, team, key, slot, started, pts, ppr in lineups:
        q.append("insert into public.lineup_players (league_id, season, week, team_id,"
                 " player_key, slot, started, points, ppr_points) values"
                 f" ('{lid}', {SEASON}, {week}, '{team}', '{key}', '{slot}',"
                 f" {str(started).lower()}, {pts},"
                 f" {'null' if ppr is None else ppr});")
    for lid, week, key, provider, picks, faab, when in heads:
        q.append("insert into public.trades (league_id, season, week, trade_key,"
                 " provider, teams, picks, faab, traded_at) values"
                 f" ('{lid}', {SEASON}, {week}, '{key}', '{provider}', '[]'::jsonb,"
                 f" '{json.dumps(picks)}'::jsonb, '{json.dumps(faab)}'::jsonb,"
                 f" {'null' if when is None else repr(when).replace(chr(39), chr(39))});")
    for lid, week, key, pkey, pos, frm, to in moves:
        q.append("insert into public.trade_players (league_id, season, week, trade_key,"
                 " player_key, name, position, nfl_team, from_team, to_team) values"
                 f" ('{lid}', {SEASON}, {week}, '{key}', '{pkey}', '{pkey.upper()}',"
                 f" '{pos}', 'CIN', '{frm}', '{to}')"
                 " on conflict do nothing;")
    for lid, week, n in scans:
        q.append("insert into public.trade_scans (league_id, season, week, trades)"
                 f" values ('{lid}', {SEASON}, {week}, {n});")
    _sql(dsn, "\n".join(q))


def _fill_demo(data) -> None:
    from web import demo_db
    leagues, lineups, heads, moves, scans, players = data
    for lid, provider in leagues:
        demo_db._LEAGUES[lid] = {"id": lid, "provider": provider}
    demo_db.upsert_nfl_players([{"player_key": k, "name": k.upper(),
                                 "position": p, "nfl_team": "CIN"} for k, p in players])
    demo_db.upsert_lineups([
        {"league_id": lid, "season": SEASON, "week": w, "team_id": t,
         "player_key": k, "slot": s, "started": st, "points": pts, "ppr_points": ppr}
        for lid, w, t, k, s, st, pts, ppr in lineups])
    demo_db.upsert_trades(
        [{"league_id": lid, "season": SEASON, "week": w, "trade_key": k,
          "provider": pr, "teams": [], "picks": pk, "faab": fb, "traded_at": when}
         for lid, w, k, pr, pk, fb, when in heads],
        [{"league_id": lid, "season": SEASON, "week": w, "trade_key": k,
          "player_key": pk, "name": pk.upper(), "position": pos, "nfl_team": "CIN",
          "from_team": frm, "to_team": to}
         for lid, w, k, pk, pos, frm, to in moves])
    for lid, w, n in scans:
        demo_db.upsert_trade_scan({"league_id": lid, "season": SEASON, "week": w,
                                   "trades": n, "scanned_at": "2026-10-22T00:00:00+00:00"})


def _norm(x):
    """Compare meaning, not spelling: Decimal vs float, key order, row order."""
    if isinstance(x, dict):
        return {str(k): _norm(v) for k, v in sorted(x.items())}
    if isinstance(x, list):
        return sorted((_norm(v) for v in x), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, (int, float)):
        return round(float(x), 2)
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return str(x)


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    dsn = sys.argv[1]
    rng = random.Random(49)

    _sql(dsn, "drop schema public cascade; create schema public;")
    _sql(dsn, _ddl())
    data = _season(rng)
    _fill_sql(dsn, data)
    _fill_demo(data)

    from web import demo_db
    calls = {
        "trade_exchanges": (f"select trade_exchanges({SEASON}, 1, 18)",
                            lambda: demo_db.trade_exchanges(SEASON, 1, 18)),
        "trade_player_names": (f"select trade_player_names({SEASON})",
                               lambda: demo_db.trade_player_names(SEASON)),
        "trade_prior": (f"select trade_prior({SEASON}, 1, 18)",
                        lambda: demo_db.trade_prior(SEASON, 1, 18)),
        "trade_backfill": (f"select trade_backfill({SEASON})",
                           lambda: demo_db.trade_backfill(SEASON)),
    }

    bad = 0
    for name in CHECKS:
        statement, twin = calls[name]
        mine = _norm(json.loads(_sql(dsn, statement) or "null"))
        theirs = _norm(twin())
        if mine == theirs:
            size = len(mine) if isinstance(mine, (list, dict)) else 1
            print(f"{name}: agrees ({size} entries)")
            continue
        bad += 1
        print(f"{name}: DIFFERS")
        if isinstance(mine, dict) and isinstance(theirs, dict):
            for key in sorted(set(mine) | set(theirs)):
                if mine.get(key) != theirs.get(key):
                    print(f"    {key}:\n      sql:    {str(mine.get(key))[:300]}"
                          f"\n      python: {str(theirs.get(key))[:300]}")
        else:
            print(f"    sql:    {str(mine)[:400]}")
            print(f"    python: {str(theirs)[:400]}")

    print()
    print("The SQL and its twin say the same thing." if not bad
          else f"{bad} function(s) disagree.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
