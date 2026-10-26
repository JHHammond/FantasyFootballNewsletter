"""My Team (/team) on localhost, with a season of made-up leagues.

    python -m scripts.demo_team              # then sign in and open /team
    python -m scripts.demo_team --port 8001

Seeds the same invented season as scripts/demo_around (rosters, drafts,
trades, so the market prices everybody), gives one of those leagues to a
demo account, and says which team is theirs. Nothing touches Supabase.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EMAIL, PASSWORD = "team@demo.test", "demo-team-2026"


def seed_account(season: int, leagues: int = 1) -> dict:
    """The demo account, owning `leagues` of the invented leagues; its team
    is known in the first and must be picked in the rest (ESPN-style)."""
    from web import auth, demo_db
    user = demo_db.create_user(EMAIL, auth.hash_password(PASSWORD)) or demo_db.user_by_email(EMAIL)
    make_dynasty(season)
    for i in range(leagues):
        lid = f"demo-{i}"
        demo_db._LEAGUES[lid] = {
            "id": lid, "created_at": f"2026-08-2{i}T00:00:00+00:00", "user_id": user["id"],
            "season": season, "provider": "sleeper" if i == 0 else "espn",
            "league_name": ["The Gridiron Gazette League", "Office League"][i % 2],
            "public_slug": lid, "my_team_id": "t0" if i == 0 else None,
            "format": "redraft", "setup_complete": True,
            # The second league is dynasty, and says so the way Sleeper
            # would, while its setup form still reads "redraft" (062).
            "league_type": "dynasty" if i % 3 == 1 else "redraft",
        }
    give_team_a_history(season)
    return user


def make_dynasty(season: int, every: int = 3) -> int:
    """A third of the invented leagues are dynasty, so there are two
    markets to look at (062). Their full drafts count as dynasty startups,
    their trades as dynasty trades. Only leagues with stored rows."""
    from web import demo_db
    ids = sorted({r["league_id"] for r in demo_db._DRAFT_PICKS.values() if int(r["season"]) == season})
    n = 0
    for lid in ids:
        if not lid.startswith("demo-") or int(lid.split("-")[1]) % every != 1:
            continue
        row = demo_db._LEAGUES.setdefault(lid, {"id": lid, "season": season, "provider": "sleeper",
                                               "league_name": f"Dynasty {lid}", "format": "redraft",
                                               "created_at": "2026-08-01T00:00:00+00:00"})
        row["league_type"] = "dynasty"
        n += 1
    return n


def give_team_a_history(season: int, lid: str = "demo-0", me: str = "t0") -> None:
    """The invented season never moves a player between rosters, so the
    Portfolio would show nothing but draft picks. Give the demo team one
    trade (a WR out, a back in, in week 4) and one waiver pickup (week 3)."""
    import nfl_week
    from datetime import date, timedelta
    from web import demo_db
    L = demo_db._LINEUPS
    rows = [r for r in L.values() if r["league_id"] == lid and int(r["season"]) == season]
    if not rows:
        return
    last = max(int(r["week"]) for r in rows)
    held = {(r["team_id"], r["player_key"]) for r in rows if int(r["week"]) == last}
    pos = {k: (v or {}).get("position") for k, v in demo_db._NFL_PLAYERS.items()}
    names = {r["team_id"]: r["team_name"] for r in demo_db._TEAM_WEEKS.values()
             if r["league_id"] == lid and int(r["season"]) == season}
    mine = sorted(k for t, k in held if t == me)
    other = next(t for t in sorted({t for t, _ in held}) if t != me)
    theirs = sorted(k for t, k in held if t == other)
    out_k = next((k for k in mine if pos.get(k) == "WR"), None)
    in_k = next((k for k in theirs if pos.get(k) == "RB"), None)
    if not out_k or not in_k:
        return

    def move(key, frm, to, from_week):
        for (l, s, w, t, k), r in list(L.items()):
            if l == lid and s == season and k == key and t == frm and w >= from_week:
                del L[(l, s, w, t, k)]
                L[(l, s, w, to, k)] = dict(r, team_id=to, slot="BN", started=False)

    week = 4
    move(out_k, me, other, week)
    move(in_k, other, me, week)
    when = nfl_week.season_opener(season) + timedelta(days=7 * (week - 1) + 2)
    demo_db.upsert_trades(
        [{"league_id": lid, "season": season, "week": week, "trade_key": "demo-team-1",
          "provider": "sleeper", "team_count": 12, "scoring_type": "ppr",
          "teams": [names.get(me), names.get(other)], "picks": [], "faab": [],
          "traded_at": f"{when.isoformat()}T18:00:00+00:00"}],
        [{"league_id": lid, "season": season, "week": week, "trade_key": "demo-team-1",
          "player_key": out_k, "name": "", "position": "WR",
          "from_team": names.get(me), "to_team": names.get(other)},
         {"league_id": lid, "season": season, "week": week, "trade_key": "demo-team-1",
          "player_key": in_k, "name": "", "position": "RB",
          "from_team": names.get(other), "to_team": names.get(me)}])

    # A waiver pickup: somebody nobody in the league rosters, from week 3.
    rostered = {k for _, k in held}
    traded = {r["player_key"] for r in demo_db._TRADE_PLAYERS.values() if int(r["season"]) == season}
    free = next((k for k in sorted(traded) if k not in rostered and pos.get(k) == "WR"), None)
    if free:
        for w in range(3, last + 1):
            L[(lid, season, w, me, free)] = {"league_id": lid, "season": season, "week": w,
                                             "team_id": me, "player_key": free, "slot": "BN",
                                             "started": False, "points": 0.0, "ppr_points": 0.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", type=int, default=300)
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    os.environ["DEMO_MODE"] = "1"
    os.environ.setdefault("TEAM_PUBLIC", "1")
    import nfl_week
    from scripts.demo_around import seed
    season = nfl_week.current_season()
    seed(args.leagues, max(nfl_week.completed_week(), 1))
    seed_account(season, leagues=2)
    import uvicorn
    from web.app import app
    print(f"My Team: http://localhost:{args.port}/team  (sign in as {EMAIL} / {PASSWORD})")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
