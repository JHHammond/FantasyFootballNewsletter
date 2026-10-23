"""Fill in who actually played, for weeks already collected (migration 055).

    python -m scripts.backfill_played            # every finished week
    python -m scripts.backfill_played 6          # one week

One request to Sleeper per week and ~1,500 rows each. Re-runnable: a week
already stored is simply written again. New weeks are saved by the normal
collection run, so this is for the weeks before 055.

It also prints the evidence the boards now rely on: how many stat lines
carried snap counts, and who played and scored exactly zero, the players
the old rule (054) wrongly took off the win-rate boards.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import nfl_week
    from web import db, ppr

    if not db.week_players_ready():
        print("Run migrations/055_who_actually_played.sql in Supabase first "
              "(then: notify pgrst, 'reload schema';).")
        return 1
    season = nfl_week.current_season()
    last = max(nfl_week.completed_week(), 1)
    weeks = [int(sys.argv[1])] if len(sys.argv) > 1 else list(range(1, last + 1))
    names = (ppr.fetch_index() or {}).get("players") or {}
    for week in weeks:
        played = ppr.fetch_played(season, week)
        if not played:
            print(f"Week {week}: Sleeper returned no games or snaps. Nothing saved.")
            continue
        rows = [{"season": season, "week": week, "player_key": k, **v} for k, v in played.items()]
        db.upsert_week_players(rows)
        took = [k for k, v in played.items() if v["played"]]
        with_snaps = sum(1 for v in played.values() if v["snaps"] is not None)
        stats = ppr.fetch_stats(season, week) or {}
        zero = [k for k in took if (stats.get(k) or [None])[0] in (0, 0.0)]
        zero_names = [(names.get(k) or [k])[0] for k in zero]
        print(f"Week {week}: {len(rows):,} stat lines saved, {len(took):,} took the field, "
              f"{with_snaps:,} with snap counts. Played and scored 0: {len(zero)}"
              + (f" (e.g. {', '.join(sorted(zero_names)[:8])})" if zero_names else ""))
    print("Done. Around the Leagues picks this up when its cache refreshes (10 minutes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
