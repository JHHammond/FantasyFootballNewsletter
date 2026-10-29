"""Fill in the waiver wire for weeks already collected (migration 067).

    python -m scripts.backfill_waivers            # every finished week
    python -m scripts.backfill_waivers 3 5        # weeks 3 to 5

The same thing as pressing Collect on /staff/around for those weeks: the
collector asks every league-week missing from waiver_scans for its moves
(one call to the transactions feed, the one the trades already come from)
and skips everything it already has. Leagues whose scores are stored are
not fetched again. Re-runnable, and safe to stop: it carries on where it
left off.

Roughly one call per league per week. At the collector's pace that is
about a quarter of an hour a week on Sleeper; ESPN and Yahoo are slower.
The Undefeated Watch's wire sections use a league only once all its weeks
are in, so they appear when this finishes, at the next refresh-undefeated
(:25 and :55).
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
    from web import db, league_stats

    if not db.waivers_ready():
        print("Run migrations/067_waivers.sql in Supabase first "
              "(then: notify pgrst, 'reload schema';).")
        return 1
    last = max(nfl_week.completed_week(), 1)
    if len(sys.argv) > 2:
        weeks = list(range(int(sys.argv[1]), int(sys.argv[2]) + 1))
    elif len(sys.argv) > 1:
        weeks = [int(sys.argv[1])]
    else:
        weeks = list(range(1, last + 1))
    for week in weeks:
        rep = league_stats.collect_week(db, week)
        print(f"Week {week}: {rep.get('waiver_moves', 0):,} moves from "
              f"{rep.get('waiver_scanned', 0):,} leagues asked "
              f"({rep.get('errors', 0)} errors).")
    print("Done. The Undefeated Watch picks this up at its next refresh (:25 and :55).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
