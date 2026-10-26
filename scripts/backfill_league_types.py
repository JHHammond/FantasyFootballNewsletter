"""Store every league's type, and collect the dynasty drafts we skipped (062).

    python -m scripts.backfill_league_types            # do it
    python -m scripts.backfill_league_types --dry-run  # just count

Two things, both re-runnable:

  1. Asks each Sleeper league what it is (settings.type: redraft, keeper or
     dynasty) and stores it as leagues.league_type. ESPN and Yahoo don't
     say; their leagues keep being judged by the setup form and the shape
     of their draft.
  2. Collects the draft of every DYNASTY league whose draft we never read
     (web/drafts.py used to skip them). Their startup drafts become the
     dynasty market's ADP; SQL keeps them out of the redraft one.

Run migrations/062_dynasty_market.sql in Supabase first. Needs .env with the
Supabase service key, and reaches Sleeper's public API (~2 requests a league,
paced like the weekly collection).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Seconds between Sleeper requests, the collection run's pace for one worker.
PACE = 0.25


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--season", type=int, default=0)
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import nfl_week
    from providers import get_provider
    from web import db, drafts, ppr

    season = args.season or nfl_week.current_season()
    try:
        db.client().table("leagues").select("league_type").limit(1).execute()
    except Exception:  # noqa: BLE001
        print("Run migrations/062_dynasty_market.sql in Supabase first.")
        return 1

    leagues = (db.client().table("leagues")
               .select("id, provider, platform_league_id, season, league_name, format, league_type")
               .eq("season", season).execute().data or [])
    sleeper = [l for l in leagues if l["provider"] == "sleeper" and not l.get("league_type")]
    print(f"{len(leagues):,} leagues in {season}; {len(sleeper):,} Sleeper leagues without a stored type.")

    told = {"redraft": 0, "keeper": 0, "dynasty": 0, None: 0}
    fixed = 0
    provider = get_provider("sleeper")
    for i, lg in enumerate(sleeper, 1):
        kind = provider.league_type(str(lg["platform_league_id"]))
        told[kind] = told.get(kind, 0) + 1
        if kind == "dynasty" and (lg.get("format") or "redraft") != "dynasty":
            fixed += 1
        if kind and not args.dry_run:
            db.set_league_type(lg["id"], kind)
            lg["league_type"] = kind
        if i % 200 == 0:
            print(f"  {i:,} asked...")
        time.sleep(PACE)
    print(f"Sleeper says: {told['redraft']:,} redraft, {told['keeper']:,} keeper, "
          f"{told['dynasty']:,} dynasty, {told[None]:,} didn't answer.")
    print(f"  {fixed:,} of the dynasty leagues were labelled something else in setup.")

    dyn = db.dynasty_league_ids(season)
    scans = {r["league_id"]: r for r in (db.client().table("draft_scans")
                                         .select("league_id, picks").eq("season", season)
                                         .execute().data or [])}
    todo = [l for l in leagues if str(l["id"]) in dyn and int((scans.get(l["id"]) or {}).get("picks") or 0) == 0]
    print(f"{len(dyn):,} dynasty leagues; {len(todo):,} with no draft stored.")
    if args.dry_run or not todo:
        return 0
    ctx = None
    try:
        ctx = ppr.context(season, max(nfl_week.completed_week(), 1))
    except Exception as exc:  # noqa: BLE001 — unresolved ids are kept, not lost
        print(f"  (no PPR matcher: {exc}; player ids stay as the platform's)")
    got = startups = 0
    for i, lg in enumerate(todo, 1):
        try:
            n = drafts.collect_league(db, lg, season, ctx)
        except Exception as exc:  # noqa: BLE001
            print(f"  {lg.get('league_name')}: {type(exc).__name__}: {exc}")
            continue
        got += n
        size = 12
        if n >= 10 * size * 0.8:
            startups += 1
        if i % 100 == 0:
            print(f"  {i:,} drafts read, {got:,} picks...")
        time.sleep(PACE)
    print(f"Done: {got:,} picks from {len(todo):,} dynasty leagues (about {startups:,} look like startups).")
    print("The dynasty market rebuilds on its own within 15 minutes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
