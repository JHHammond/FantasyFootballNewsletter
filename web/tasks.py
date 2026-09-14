"""
Weekly auto-send.

For every league with auto_send on: generate this week's paper if it doesn't
exist yet, then email it to every confirmed subscriber.

Two ways to run it, because hosting platforms differ:

    python -m web.tasks 3              # real cron
    POST /tasks/weekly  (X-Task-Key)   # hosts without cron, hit by an external scheduler

Idempotency matters more than anything else here. A cron that retries, or two
processes both waking up, must not mail everyone twice — so `emailed_at` is
checked before sending and set after. Generation is separately idempotent: a
paper that already exists is reused rather than regenerated, which also avoids
paying Claude twice for the same week.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers import ProviderError  # noqa: E402

from . import emailer  # noqa: E402
from .generate import generate_and_store, paper_name_for  # noqa: E402


def send_weekly(db, week: int, *, regenerate: bool = False) -> dict[str, Any]:
    """Run the weekly job. Returns a summary suitable for logging or a response."""
    report = {
        "week": week,
        "leagues": 0,
        "generated": 0,
        "emails_sent": 0,
        "skipped": [],
        "errors": [],
    }

    for league in db.leagues_with_auto_send():
        report["leagues"] += 1
        name = paper_name_for(league)
        season = league["season"]

        paper = db.get_paper(league["id"], season, week)

        # Already mailed? Stop here. This is the guard that makes a retry safe.
        if paper and paper.get("emailed_at") and not regenerate:
            report["skipped"].append(f"{name}: week {week} already sent")
            continue

        if not paper or regenerate:
            try:
                generate_and_store(db, league, week)
                report["generated"] += 1
                paper = db.get_paper(league["id"], season, week)
            except ProviderError as exc:
                report["errors"].append(f"{name}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 — one bad league mustn't stop the rest
                report["errors"].append(f"{name}: unexpected: {exc}")
                continue

        if not paper:
            report["errors"].append(f"{name}: no paper after generation")
            continue

        subscribers = db.active_subscribers(league["id"])
        if not subscribers:
            report["skipped"].append(f"{name}: no subscribers")
            # Still mark it, so we don't retry the send every hour forever.
            db.mark_emailed(league["id"], season, week)
            continue

        base = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
        paper_url = f"{base}/p/{league['public_slug']}/{season}/week-{week}"
        headline = ((paper.get("ai_cache") or {}).get("headline")
                    if isinstance(paper.get("ai_cache"), dict) else None)
        headline = headline or f"Week {week} is out"

        sent = 0
        for subscriber in subscribers:
            result = emailer.send_weekly_edition(
                subscriber["email"], name, week, headline,
                paper_url, subscriber["unsubscribe_token"],
            )
            if result.ok:
                sent += 1
            else:
                report["errors"].append(f"{name} -> {subscriber['email']}: {result.detail}")

        report["emails_sent"] += sent
        db.mark_emailed(league["id"], season, week)

    # Housekeeping, while we're already awake once a week. claim_rate_slot only
    # prunes the bucket it was called for, so an address that appeared once and
    # never returned would otherwise leave its row behind indefinitely.
    report["rate_rows_swept"] = db.sweep_rate_events()

    return report


def resolve_week() -> int:
    """Which week the job should write up when nobody says.

    Asks the platform first, because it knows about schedule changes and the
    difference between the regular season and the playoffs. Falls back to date
    arithmetic that works in any year — the previous version of this lived in
    render.yaml as a fixed 2025 date, which meant the job silently wrote up
    week 18 for the whole of every subsequent season.
    """
    import nfl_week

    try:
        from providers import get_provider
        state = get_provider("sleeper").current_state()
    except Exception:  # noqa: BLE001 — a convenience lookup, never fatal
        state = None

    if state and state.get("season_type") == "regular":
        # Sleeper's `week` is the week now in progress. On Tuesday the paper
        # people want is about the weekend that just finished.
        return max(1, min(nfl_week.REGULAR_SEASON_WEEKS, int(state["week"]) - 1)) \
            if int(state["week"]) > 1 else 1

    return nfl_week.completed_week()


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    regenerate = "--regenerate" in sys.argv

    if os.getenv("DEMO_MODE") == "1":
        print("Refusing to run the weekly job in DEMO_MODE — there's no real data.")
        return 1

    if args:
        week = int(args[0])
    else:
        week = resolve_week()
        print(f"No week given; resolved to week {week}.")

    from . import db

    report = send_weekly(db, week, regenerate=regenerate)

    print(f"Week {week}: {report['leagues']} leagues, "
          f"{report['generated']} generated, {report['emails_sent']} emails sent")
    for line in report["skipped"]:
        print(f"  skipped: {line}")
    for line in report["errors"]:
        print(f"  ERROR:   {line}")

    # A cron whose failures go only to a log nobody reads is a cron you don't
    # have. Mail the operator when anything went wrong.
    if report["errors"]:
        emailer.send_ops_alert(f"Weekly job, week {week}", report)

    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
