"""The live trade feed's collector (John, 24 Oct 2026).

Trades used to arrive once a week, with the Tuesday job. This asks every
Sleeper league for this week's trades, every half hour, so /market/trades
shows them as they happen. Sleeper only: it is most of the leagues, and
ESPN and Yahoo are slower and stricter about how often they are asked.

Two rules that matter:

  * It never writes trade_scans. The Tuesday collector skips a league it
    has already scanned for a week, so a mid-week poll that recorded a scan
    would hide every trade made after it. Trades are upserted on their key,
    so the Tuesday run storing them again changes nothing.
  * Waiver claims (067) ride along from the same feed, under the same
    rule: stored as they happen, never a waiver_scans row, so the Tuesday
    run still asks the week in full.
  * It keeps Sleeper's pace: a few workers, each waiting between leagues,
    well under the 1,000 calls a minute Sleeper asks people to stay below.

Run: `python -m web.tasks --live-trades` (the live-trades cron on Render).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

#: Workers, and the wait each keeps between leagues. 4 / 0.3s is about
#: 500 calls a minute at Sleeper's usual response time.
WORKERS = 4
PACE = 0.3


def poll(db, season: int, week: int, *, log=print) -> dict[str, Any]:
    from providers import load_transactions
    from . import trades as trades_mod
    from . import waivers as waivers_mod

    leagues = db.sleeper_leagues(season)
    report = {"leagues": len(leagues), "asked": 0, "trades": 0, "errors": 0,
              "waiver_moves": 0}
    try:
        waiving = bool(db.waivers_ready())
    except Exception:  # noqa: BLE001 — before migration 067
        waiving = False
    lock = threading.Lock()
    started = time.time()

    def one(league):
        try:
            moves = load_transactions("sleeper", league["platform_league_id"], season, week)
            heads, players = trades_mod.rows(league, moves, season, week)
            if heads:
                db.upsert_trades(heads, players)
            wire = waivers_mod.rows(league, moves, season, week) if waiving else []
            if wire:
                db.upsert_waivers(wire)
            with lock:
                report["asked"] += 1
                report["trades"] += len(heads)
                report["waiver_moves"] += len({r["move_key"] for r in wire})
        except Exception as exc:  # noqa: BLE001 — one league never stops the run
            with lock:
                report["errors"] += 1
                quiet = report["errors"] > 10
            if not quiet:
                log(f"  live trades: {league.get('league_name')}: {type(exc).__name__}: {exc}")
        time.sleep(PACE)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(one, leagues))
    log(f"Live trades, week {week}: asked {report['asked']} of {report['leagues']} Sleeper leagues, "
        f"{report['trades']} trades seen ({report['errors']} errors) in {time.time() - started:.0f}s.")
    return report
